"""
from services.balance_manager import freeze, settle, get_balance
pipeline.py - v5 会员一键生成版（管家直连 + 服务端持久化 + 断点续跑）
===================================================================
v5 新增:
  1. pipeline_progress 表: 每阶段完成后立即持久化到 DB
  2. GET /progress/{project_id}: 返回所有阶段进度 + 文件URL
  3. POST /resume/{project_id}: 从断点续跑
  4. POST /start 两阶段提交: 检测未完成项目自动 resume
  5. 自动重试: 失败后2分钟后自动重试，最多3次
  6. 不再依赖 localStorage 保存进度

保留: API 端点、Pydantic 模型、进度回调。
"""

import os
import time
import json
import logging
import sqlite3
import concurrent.futures
import asyncio
import glob
import traceback
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from services.orchestrator import (
    PipelineOrchestrator, PipelineContext, Stage, create_context,
    STAGE_LABELS, STAGE_ICONS, STAGE_ORDER, STAGE_DEPENDS,
)
from utils.auth_util import get_user_id
from utils.path_util import url_to_local_path
from utils.storage_path import local_to_url
from app_config import BASE_URL

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/pipeline", tags=["短剧流水线"])

# ─── 常量 ──────────────────────────────────────────────
DB_DIR = "/www/wwwroot/api.mzsh.top/data"
DB_PATH = os.path.join(DB_DIR, "short_drama.db")
MAX_WORKERS = 4  # SQLite单写锁，4线程足够，避免database locked
MAX_AUTO_RETRIES = 3          # v5: 最多自动重试次数
AUTO_RETRY_DELAY_SEC = 120    # v5: 自动重试等待秒数 (2分钟)
os.makedirs(DB_DIR, exist_ok=True)

# ─── 枚举 ──────────────────────────────────────────────
class PipelineStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PENDING_RETRY = "pending_retry"       # v5: 等待自动重试
    FAILED_PERMANENT = "failed_permanent" # v5: 重试耗尽，彻底失败

# ─── Pydantic 模型 ─────────────────────────────────────
class PipelineStart(BaseModel):
    project_id: str = "default"
    script_text: str = ""
    plot: str = ""
    genre: str = ""
    title: str = ""
    synopsis: str = ""
    characters: List[Dict[str, Any]] = []
    max_shots: int = 8
    resume_pipeline_id: str = ""
    mode: str = ""

    @field_validator("project_id", mode="before")
    @classmethod
    def coerce_project_id(cls, v):
        return str(v) if v is not None else "default"

class StartResponse(BaseModel):
    success: bool = True
    pipeline_id: str = ""
    project_id: str = ""
    message: str = ""
    script: str = ""
    task_id: str = ""            # v5: 统一用 task_id = project_id
    status: str = "started"      # v5: started / resumed
    progress: list = []          # v5: 续传时返回已有进度

# ─── 数据库 ────────────────────────────────────────────
def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def _execute_db(query: str, params: tuple = ()) -> Optional[List[sqlite3.Row]]:
    try:
        with _get_conn() as conn:
            c = conn.cursor()
            c.execute(query, params)
            conn.commit()
            if query.strip().upper().startswith("SELECT"):
                return c.fetchall()
            return None
    except Exception as e:
        logger.error(f"DB操作失败: {query[:60]}... {e}")
        raise

def _init_db():
    try:
        with _get_conn() as conn:
            c = conn.cursor()
            c.executescript("""
                CREATE TABLE IF NOT EXISTS pipelines (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    script_text TEXT DEFAULT '',
                    genre TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    progress INTEGER DEFAULT 0,
                    total_stages INTEGER DEFAULT 13,
                    current_stage TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    step_results TEXT DEFAULT '{}',
                    stage_outputs TEXT DEFAULT '{}',
                    created REAL DEFAULT 0,
                    updated REAL DEFAULT 0,
                    user_id TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT DEFAULT '',
                    script TEXT DEFAULT '',
                    genre TEXT DEFAULT '',
                    progress INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'draft',
                    characters TEXT DEFAULT '[]',
                    pipeline_steps TEXT DEFAULT '[]',
                    created REAL DEFAULT 0,
                    updated REAL DEFAULT 0,
                    user_id INTEGER DEFAULT 0
                );

                -- v5: 阶段级进度持久化表
                CREATE TABLE IF NOT EXISTS pipeline_progress (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT NOT NULL,
                    pipeline_id TEXT DEFAULT '',
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    data TEXT,
                    error TEXT,
                    retry_count INTEGER DEFAULT 0,
                    started_at TEXT,
                    finished_at TEXT,
                    UNIQUE(project_id, pipeline_id, stage)
                );
                CREATE INDEX IF NOT EXISTS idx_pp_project ON pipeline_progress(project_id);
                CREATE INDEX IF NOT EXISTS idx_pp_status ON pipeline_progress(status);
            """)
            # 兼容旧表缺列 - 用 PRAGMA 检查避免重复 ALTER 报错
            for tbl, col, coldef in [
                ("pipelines", "step_results", "TEXT DEFAULT '{}'"),
                ("pipelines", "stage_outputs", "TEXT DEFAULT '{}'"),
                ("pipelines", "user_id", "TEXT DEFAULT ''"),
                ("projects", "pipeline_steps", "TEXT DEFAULT '[]'"),
                ("projects", "progress", "INTEGER DEFAULT 0"),
                ("projects", "status", "TEXT DEFAULT 'draft'"),
            ]:
                try:
                    existing = {r[1] for r in c.execute(f"PRAGMA table_info({tbl})").fetchall()}
                    if col not in existing:
                        c.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {coldef}")
                        logger.info(f"[pipeline] 成功添加列: {tbl}.{col}")
                except Exception as _pe:
                    logger.warning(f"[pipeline] 添加列失败: {tbl}.{col} - {_pe}")
            conn.commit()
    except Exception as e:
        logger.error(f"DB初始化失败: {e}")
        raise

_init_db()

# ─── 全局资源 ──────────────────────────────────────────
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)

# ─── 阶段运行锁（防重复提交）──────────────────────────────
# key = f"{project_id}:{stage}"，value = 提交时间戳
# 同一项目同一阶段正在后台运行时，拒绝再次提交，避免 video 等慢阶段被反复重跑。
import threading as _threading_mod

_step_lock_map: Dict[str, float] = {}
_step_lock_guard = _threading_mod.Lock()
# 单阶段最长运行时间（秒），超过视为僵死、自动释放锁
_STEP_LOCK_TTL = 60 * 40  # 40 分钟（video/scene 等慢阶段足够）


def _acquire_step_lock(project_id: str, stage: str) -> bool:
    """尝试获取阶段锁。返回 True 表示拿到（可以跑），False 表示已有任务在跑。"""
    key = f"{project_id}:{stage}"
    now = time.time()
    with _step_lock_guard:
        started = _step_lock_map.get(key)
        if started and (now - started) < _STEP_LOCK_TTL:
            return False  # 还在跑，拒绝
        # 没在跑 或 已超时僵死 → 占坑
        _step_lock_map[key] = now
        return True


def _release_step_lock(project_id: str, stage: str) -> None:
    key = f"{project_id}:{stage}"
    with _step_lock_guard:
        _step_lock_map.pop(key, None)

def _extract_video_url(data) -> str:
    """统一从视频生成结果提取 video_url。
    无论哪个模型/agent返回，都用这个函数提取，避免到处不一样。
    
    支持的结构：
    - {"video_url": "xxx"}
    - {"url": "xxx"}  
    - {"videos": [{"result": {"video_url": "xxx"}}]}
    - {"videos": [{"video_url": "xxx"}]}
    - {"result": {"video_url": "xxx"}}
    """
    if not data or not isinstance(data, dict):
        return ""
    # 1. 顶层
    url = data.get("video_url", "") or data.get("url", "")
    if url:
        return url
    # 2. videos 数组
    videos = data.get("videos", data.get("clips", []))
    if isinstance(videos, list) and len(videos) > 0:
        v0 = videos[0]
        if isinstance(v0, dict):
            # 2a. result 嵌套
            vr = v0.get("result", {})
            if isinstance(vr, dict):
                url = vr.get("video_url", "") or vr.get("url", "")
                if url:
                    return url
            # 2b. 直接在 video 项里
            url = v0.get("video_url", "") or v0.get("url", "")
            if url:
                return url
    # 3. result 嵌套
    nested = data.get("result")
    if isinstance(nested, dict):
        url = nested.get("video_url", "") or nested.get("url", "")
        if url:
            return url
    elif isinstance(nested, str) and nested.startswith("http"):
        return nested
    return ""


# ─── 阶段 UI 配置 (仅用于前端展示) ─────────────────────
STAGE_UI: Dict[str, Dict] = {}
for s in STAGE_ORDER:
    STAGE_UI[s.value] = {
        "key": f"{s.value}_result",
        "label": STAGE_LABELS.get(s, s.value),
        "icon": STAGE_ICONS.get(s, "❓"),
    }

# ═══════════════════════════════════════════════════════════════════════════
# v5: pipeline_progress 表操作
# ═══════════════════════════════════════════════════════════════════════════

def _save_stage_progress(pipeline_id: str, project_id: str = "",
                         stage: str = "", status: str = "pending",
                         data: Dict[str, Any] = None, error: str = ""):
    """v5: 保存单阶段进度到 pipeline_progress 表"""
    try:
        pid = project_id or pipeline_id
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        data_json = json.dumps(data, ensure_ascii=False, default=str) if data else "{}"
        _execute_db("""
            INSERT INTO pipeline_progress (project_id, pipeline_id, stage, status, data, error, retry_count, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(project_id, pipeline_id, stage)
            DO UPDATE SET status=excluded.status, data=excluded.data, error=excluded.error,
                          finished_at=excluded.finished_at,
                          retry_count=CASE WHEN excluded.status='failed' THEN pipeline_progress.retry_count + 1 ELSE pipeline_progress.retry_count END
        """, (pid, pipeline_id, stage, status, data_json, error, now, now if status in ("completed", "failed") else None))
    except Exception as e:
        logger.warning(f"保存阶段进度失败 ({stage}): {e}")

def _get_project_progress(project_id: str) -> List[Dict]:
    """v5: 获取项目的所有阶段进度"""
    try:
        rows = _execute_db(
            "SELECT stage, status, data, error, retry_count, started_at, finished_at FROM pipeline_progress WHERE project_id=? ORDER BY id",
            (str(project_id),))
        if not rows:
            return []
        results = []
        for r in rows:
            data = {}
            try:
                data = json.loads(r["data"]) if r["data"] else {}
            except Exception as _pe:
                logger.error(f"[pipeline] 操作失败(traceback): {_pe}", exc_info=True)
            results.append({
                "stage": r["stage"],
                "status": r["status"],
                "data": data,
                "error": r["error"] or "",
                "retry_count": r["retry_count"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
            })
        return results
    except Exception as e:
        logger.warning(f"获取项目进度失败: {e}")
        return []

def _get_latest_pipeline_id(project_id: str) -> str:
    """v5: 获取项目最新的 pipeline_id"""
    try:
        rows = _execute_db(
            "SELECT pipeline_id FROM pipeline_progress WHERE project_id=? ORDER BY id DESC LIMIT 1",
            (str(project_id),))
        return rows[0]["pipeline_id"] if rows else ""
    except Exception as _pe: logger.warning(f"[pipeline] 操作失败: {_pe}")

    return ""

def _get_stage_retry_count(project_id: str, stage: str, pipeline_id: str = "") -> int:
    """v5: 获取某阶段的已重试次数"""
    try:
        if pipeline_id:
            rows = _execute_db(
                "SELECT retry_count FROM pipeline_progress WHERE project_id=? AND stage=? AND pipeline_id=?",
                (str(project_id), stage, pipeline_id))
        else:
            rows = _execute_db(
                "SELECT retry_count FROM pipeline_progress WHERE project_id=? AND stage=? ORDER BY id DESC LIMIT 1",
                (str(project_id), stage))
        return rows[0]["retry_count"] if rows else 0
    except Exception as _pe: logger.warning(f"[pipeline] 操作失败: {_pe}")

    return 0

def _aggregate_progress_data(project_id: str) -> Dict[str, Any]:
    """v5: 汇总项目进度为前端可用的数据（含文件URL）"""
    stages_raw = _get_project_progress(project_id)
    if not stages_raw:
        return {"project_id": project_id, "stages": [], "finished": False}

    # 构建阶段状态列表
    stage_map = {s["stage"]: s for s in stages_raw}
    all_stages = []
    all_completed = True
    has_failed = False
    current_stage = ""

    for s in STAGE_ORDER:
        info = stage_map.get(s.value, {})
        if not info:
            all_stages.append({
                "stage": s.value,
                "label": STAGE_LABELS.get(s, s.value),
                "icon": STAGE_ICONS.get(s, "❓"),
                "status": "pending",
                "data": {},
                "error": "",
            })
            all_completed = False
        else:
            st = info.get("status", "pending") if info else "pending"
            if st == "failed":
                has_failed = True
                all_completed = False
            elif st in ("pending", "running"):
                all_completed = False
                current_stage = s.value
            all_stages.append({
                "stage": s.value,
                "label": STAGE_LABELS.get(s, s.value),
                "icon": STAGE_ICONS.get(s, "❓"),
                "status": "paused" if st == "failed" else st,
                "data": info.get("data", {}),
                "error": info.get("error", ""),
                "retry_count": info.get("retry_count", 0),
            })

    # 汇总顶层数据（从 completed 阶段的 data 提取）
    script_text = ""
    characters = []
    scene_images = []
    tts_audio = []
    bgm_url = ""
    final_video_url = ""

    for s in all_stages:
        if s["status"] != "completed":
            continue
        d = s.get("data", {})
        sv = s["stage"]

        if sv == "script":
            script_text = d.get("script", d.get("outline", d.get("text", script_text))) or script_text
            if not characters:
                characters = d.get("characters", [])

        elif sv == "director":
            refined = d.get("analysis", d.get("refined_script", {}))
            if isinstance(refined, dict):
                ref_chars = refined.get("characters", [])
                if ref_chars and len(ref_chars) > len(characters):
                    characters = ref_chars
                if not script_text:
                    script_text = refined.get("script", refined.get("text", ""))

        elif sv == "character":
            chars = d.get("characters", [])
            if chars and len(chars) > len(characters):
                characters = chars

        elif sv == "scene":
            images = d.get("images", d.get("scene_images", []))
            if images:
                scene_images = images

        elif sv == "tts":
            audio = d.get("audio_files", d.get("audio", []))
            if audio:
                tts_audio = audio

        elif sv == "bgm":
            bgm_url = d.get("audio_file", d.get("bgm_url", d.get("url", bgm_url))) or bgm_url

        elif sv == "composite":
            video = d.get("video_url", d.get("url", d.get("output", "")))
            if video:
                final_video_url = video

        elif sv == "video":
            clips = d.get("clips", d.get("videos", []))
            if clips and not final_video_url:
                # 从第一个clip提取
                if isinstance(clips[0], dict):
                    result = clips[0].get("result", {})
                    final_video_url = result.get("video_url", result.get("url", ""))

    # 构建 shot_videos: 每个 shot_index 对应的视频URL（供前端逐镜展示）
    shot_videos = {}
    shot_video_errors = {}
    for s in all_stages:
        if s["stage"] == "video" and s["status"] == "completed":
            d = s.get("data", {})
            clips = d.get("clips", d.get("videos", []))
            if isinstance(clips, list):
                for clip in clips:
                    if not isinstance(clip, dict):
                        continue
                    si = clip.get("shot_index", clip.get("shot_num"))
                    if si is None:
                        continue
                    result = clip.get("result", {}) if isinstance(clip.get("result"), dict) else {}
                    url = result.get("video_url", result.get("url", ""))
                    err = result.get("error", "")
                    if url:
                        shot_videos[str(si)] = url
                    elif err:
                        shot_video_errors[str(si)] = err

    # 查找视频文件
    if not final_video_url:
        try:
            patterns = [
                os.path.join(DB_DIR, f"../storage/videos/*_{project_id}.mp4"),
                os.path.join(DB_DIR, f"../storage/videos/*_{project_id[:20]}*.mp4"),
            ]
            for pat in patterns:
                matches = glob.glob(pat)
                if matches:
                    fname = os.path.basename(matches[0])
                    final_video_url = local_to_url(matches[0])
                    break
        except Exception as _pe:
            logger.error(f"[pipeline] 操作失败(traceback): {_pe}", exc_info=True)

    return {
        "project_id": project_id,
        "stages": all_stages,
        "finished": all_completed and not has_failed,
        "script_text": script_text,
        "characters": characters,
        "scene_images": scene_images,
        "tts_audio": tts_audio,
        "bgm_url": bgm_url,
        "final_video_url": final_video_url,
        "current_stage": current_stage,
        "has_failed": has_failed,
        "shot_videos": shot_videos,
        "shot_video_errors": shot_video_errors,
    }


# ─── 进度回调 & DB 更新 ────────────────────────────────
def _update_pipeline_db(pid: str, progress: int, current_stage: str = "",
                        status: PipelineStatus = PipelineStatus.RUNNING, error: str = ""):
    try:
        _execute_db(
            "UPDATE pipelines SET status=?, progress=?, current_stage=?, updated=?, error=? WHERE id=?",
            (status.value, progress, current_stage, time.time(), error, pid))
    except Exception as e:
        logger.warning(f"更新pipeline进度失败: {e}")

def _update_project_status(project_id: str, status: str, progress: int = 0):
    """v5: 更新项目状态"""
    try:
        _execute_db(
            "UPDATE projects SET status=?, progress=?, updated=? WHERE id=?",
            (status, progress, time.time(), project_id))
    except Exception as e:
        logger.warning(f"更新项目状态失败: {e}")

def _update_project_steps(project_id: str, stage_label: str, stage_key: str,
                          pct: int, status: str = "busy", log: str = ""):
    try:
        rows = _execute_db("SELECT pipeline_steps FROM projects WHERE id=?", (project_id,))
        if not rows:
            return
        steps = json.loads(rows[0][0] or "[]")
        found = False
        for s in steps:
            if s.get("key") == stage_key or s.get("label") == stage_label:
                s["status"] = status
                s["progress"] = pct
                if log:
                    s["log"] = log
                found = True
                break
        if not found:
            icon = STAGE_UI.get(stage_key, {}).get("icon", "❓")
            steps.append({
                "key": stage_key, "label": stage_label, "icon": icon,
                "desc": "", "status": "paused" if status == "failed" else status, "progress": pct,
                "duration": "", "log": log or f"{stage_label}..."
            })
        total_pct = max(pct, max((s.get("progress", 0) for s in steps), default=0))
        _execute_db(
            "UPDATE projects SET pipeline_steps=?, progress=?, updated=? WHERE id=?",
            (json.dumps(steps, ensure_ascii=False), total_pct, time.time(), project_id))
    except Exception as e:
        logger.warning(f"更新项目步骤失败: {e}")

def _save_snapshot_to_db(pid: str, snapshot: Dict[str, Any]):
    """orchestrator 的 db_save 回调"""
    try:
        _execute_db(
            "UPDATE pipelines SET step_results=?, current_stage=?, updated=? WHERE id=?",
            (json.dumps(snapshot, ensure_ascii=False, default=str),
             snapshot.get("current_stage", ""),
             time.time(), pid))
    except Exception as e:
        logger.warning(f"保存快照失败: {e}")

# ─── v5: orchestrator stage_callback ────────────────────

def _orchestrator_stage_callback(pipeline_id: str, stage: str, status: str,
                                  data: Dict[str, Any] = None, error: str = ""):
    """v5: 每阶段完成后由 orchestrator 调用，持久化到 pipeline_progress"""
    try:
        # 查找 project_id
        rows = _execute_db("SELECT project_id FROM pipelines WHERE id=?", (pipeline_id,))
        project_id = str(rows[0]["project_id"]) if rows else pipeline_id

        _save_stage_progress(
            pipeline_id=pipeline_id,
            project_id=project_id,
            stage=stage,
            status=status,
            data=data or {},
            error=error,
        )

        # 阶段完成 → 记录花费
        if status == "completed":
            try:
                from services.balance_manager import record_cost
                _cost_map = {
                    "scene": "scene_image", "character": "portrait_image",
                    "tts": "tts", "bgm": "bgm",
                    "video": "video_r2v", "composite": "videoretalk",
                    "director": "llm", "storyboard": "llm", "subtitle": "llm",
                }
                _ct = _cost_map.get(stage)
                if _ct and str(project_id).isdigit():
                    uid = int(project_id)  # user_id == project_id 在现有系统里
                    record_cost(uid, project_id, _ct, 1)
            except Exception:
                pass

        # 管线全部完成 → 结算
        if status in ("completed", "failed"):
            try:
                all_stages = ["director","storyboard","character","scene","tts","bgm","video","subtitle","composite"]
                _all_done = all(
                    _execute_db(
                        "SELECT status FROM pipeline_progress WHERE project_id=? AND stage=? ORDER BY id DESC LIMIT 1",
                        (project_id, s)
                    )[0]["status"] == "completed"
                    if _execute_db("SELECT status FROM pipeline_progress WHERE project_id=? AND stage=? ORDER BY id DESC LIMIT 1",
                                   (project_id, s))
                    else False
                    for s in all_stages
                )
                if _all_done or status == "failed":
                    from services.balance_manager import settle, get_balance
                    uid = int(project_id) if str(project_id).isdigit() else 0
                    settle(uid, project_id)
                    bal = get_balance(uid)
                    logger.info(f"[Pipeline] 结算完成 user={uid} 可用余额={bal.get('available', 0)}")
            except Exception:
                pass

        if status == "failed":
            # 不再自动重试：单阶段失败就如实标记，保留原因等人工处理。
            # 之前标记 pending_retry 会被 delayed_retry 自动重跑整条管线。
            logger.warning(f"[Pipeline] {stage} 失败，标记 failed（不自动重试，等人工处理）")
            _update_project_status(project_id, "failed")
    except Exception as e:
        logger.warning(f"stage_callback 失败: {e}")


# ─── 核心: 用管家执行完整管线 ──────────────────────────

def _run_with_orchestrator(
    pid: str, project_id: str, genre: str,
    script_text: str, title: str, synopsis: str,
    characters: List[Dict], resume_from: Optional[Dict] = None,
    polish_only: bool = False,
    retry_cycle: int = 0,  # v5: 自动重试周期计数
):
    """用 PipelineOrchestrator 执行完整管线（在后台线程运行）"""
    try:
        # 1) 构建上下文
        logger.info(f"[DBG] _run_with_orchestrator: synopsis={repr(synopsis)[:100]}, script_text={repr(script_text)[:100]}, title={repr(title)[:50]}")
        ctx = create_context(
            synopsis=synopsis or script_text,
            script_text=script_text,
            genre=genre or "都市",
            title=title,
            project_id=project_id,
            characters=characters,
            polish_only=polish_only,
        )
        ctx.pipeline_id = pid

        # 恢复重试计数
        for sv in ctx.stage_retry_count:
            ctx.stage_retry_count[sv] = _get_stage_retry_count(project_id, sv, pid)

        # 2) 如果有断点续跑数据，回填上下文
        if resume_from:
            for key, val in resume_from.items():
                if isinstance(val, dict):
                    # key 格式: "character_result" → stage.value = "character"
                    stage_val = key.replace("_result", "")
                    if stage_val in [s.value for s in Stage]:
                        ctx.results[stage_val] = val.get("data", val)
                        if stage_val not in ctx.completed_stages:
                            ctx.completed_stages.append(stage_val)
                        # 同步顶层字段
                        if stage_val == "character":
                            # resume 回填角色时，保留已有角色的 portrait_url（用户已建模的图）。
                            # character agent 的 extract 只返回姓名/性格/外貌，不含 portrait_url；
                            # 直接覆盖会丢掉 create_context 传入的（来自 DB projects.characters）
                            # 已建模角色图，导致下游 SCENE 锁脸拿不到参考图。
                            resume_chars = val.get("data", {}).get("characters", []) or []
                            if resume_chars and ctx.characters:
                                # 以 resume 角色为底，按名字合并已有 portrait_url/photo/avatar
                                existing_map = {}
                                for c in ctx.characters:
                                    nm = c.get("name", "")
                                    if nm:
                                        existing_map[nm] = c
                                for rc in resume_chars:
                                    nm = rc.get("name", "")
                                    if nm and nm in existing_map:
                                        ex = existing_map[nm]
                                        for k in ("portrait_url", "photo", "avatar"):
                                            if ex.get(k) and not rc.get(k):
                                                rc[k] = ex[k]
                                ctx.characters = resume_chars
                            elif resume_chars:
                                ctx.characters = resume_chars
                            # 否则保留 ctx.characters 不变
                        elif stage_val == "script":
                            # resume 回填剧本：兼容 script/outline/text 多种字段名
                            _sd = val.get("data", {}) or {}
                            ctx.script_text = _sd.get("script") or _sd.get("outline") or _sd.get("text") or ctx.script_text
                            ctx.title = _sd.get("title", ctx.title)
                            ctx.genre = _sd.get("genre", ctx.genre) or ctx.genre
                        elif stage_val == "storyboard":
                            ctx.shots = val.get("data", {}).get("shots", ctx.shots)
                        elif stage_val == "scene":
                            ctx.scene_images = val.get("data", {}).get("images", ctx.scene_images)
                        elif stage_val == "tts":
                            ctx.tts_audio = val.get("data", {}).get("audio_files", ctx.tts_audio)
                        elif stage_val == "bgm":
                            ctx.bgm_url = val.get("data", {}).get("bgm_url", ctx.bgm_url)
                        elif stage_val == "director":
                            pass  # 导演数据在 ctx.results 中
                        elif stage_val == "cinematographer":
                            # P0-3: 合并摄影参数到ctx.shots
                            data_val = val.get("data", val)
                            shots = data_val.get("shots", [])
                            if shots and ctx.shots:
                                for i, s in enumerate(shots):
                                    if i < len(ctx.shots):
                                        ctx.shots[i]["camera_movement"] = s.get("camera_movement", ctx.shots[i].get("camera_movement",""))
                                        ctx.shots[i]["camera_angle"] = s.get("camera_angle", ctx.shots[i].get("camera_angle",""))
                                        ctx.shots[i]["shot_type"] = s.get("shot_type", ctx.shots[i].get("shot_type",""))
                                        ctx.shots[i]["lighting"] = s.get("lighting", ctx.shots[i].get("lighting",""))
                                        ctx.shots[i]["transition"] = s.get("transition", ctx.shots[i].get("transition",""))
                                        ctx.shots[i]["flow_notes"] = s.get("flow_notes", ctx.shots[i].get("flow_notes",""))
                                        ctx.shots[i]["rationale"] = s.get("rationale","")
                            elif shots:
                                ctx.shots = shots
                        elif stage_val == "wardrobe":
                            # P0-3: 合并服化道参数到ctx.shots
                            data_val = val.get("data", val)
                            shots = data_val.get("shots", [])
                            if shots and ctx.shots:
                                for i, s in enumerate(shots):
                                    if i < len(ctx.shots):
                                        ctx.shots[i]["outfit"] = s.get("outfit", ctx.shots[i].get("outfit",{}))
                                        ctx.shots[i]["props"] = s.get("props", ctx.shots[i].get("props",{}))
                                        ctx.shots[i]["makeup"] = s.get("makeup", ctx.shots[i].get("makeup",{}))
                                        ctx.shots[i]["char_ages"] = s.get("char_ages", ctx.shots[i].get("char_ages",{}))
                                        ctx.shots[i]["wardrobe_notes"] = s.get("wardrobe_notes","")
                            elif shots:
                                ctx.shots = shots
                        elif stage_val == "sfx":
                            # P0-3: 合并音效参数到ctx.shots
                            data_val = val.get("data", val)
                            shots = data_val.get("shots", [])
                            if shots and ctx.shots:
                                for i, s in enumerate(shots):
                                    if i < len(ctx.shots):
                                        ctx.shots[i]["needs_sfx"] = s.get("needs_sfx", False)
                                        ctx.shots[i]["action_effects"] = s.get("action_effects", [])
                                        ctx.shots[i]["atmosphere_effects"] = s.get("atmosphere_effects", [])
                                        ctx.shots[i]["transition_effect"] = s.get("transition_effect", "")
                                        ctx.shots[i]["sfx_intensity"] = s.get("sfx_intensity", 0)
                                        ctx.shots[i]["color_grade"] = s.get("color_grade", "")
                                        ctx.shots[i]["sfx_reason"] = s.get("sfx_reason", "")
                            elif shots:
                                ctx.shots = shots
            logger.info(f"[Pipeline] 续传: 回填 {len(ctx.completed_stages)} 阶段")

        # 3) 计算起始阶段
        start_from = None
        # [锁脸修复] resume 时若 CHARACTER 已完成但角色 portrait_url 全空或过期，
        # 强制从 CHARACTER 重跑（_post_gen_portraits 会补生成肖像并回写）。
        # 否则下游场景图/视频缺参考图，锁脸和 I2V 全部失败。
        if "character" in ctx.completed_stages and ctx.characters:
            EXPIRED_DOMAINS = ("aliyuncs.com", "volces.com", "dashscope", "ark-acg")
            def _valid_portrait(url):
                u = str(url or "")
                if not u.startswith("http"):
                    return False
                # 过期的 OSS/TOS 临时 URL 不算有效
                if any(d in u for d in EXPIRED_DOMAINS):
                    return False
                return True
            has_portrait = any(
                _valid_portrait(c.get("portrait_url")) or _valid_portrait(c.get("photo"))
                for c in ctx.characters
            )
            if not has_portrait:
                logger.warning(f"[Pipeline] CHARACTER 已完成但 portrait_url 全空，从 CHARACTER 重跑补肖像")
                ctx.completed_stages = [s for s in ctx.completed_stages if s not in ("character",)]
                # character 之后的阶段也清除（storyboard/scene 等依赖 portrait）
                char_idx = next((i for i, s in enumerate(STAGE_ORDER) if s.value == "character"), 0)
                after_char = {s.value for s in STAGE_ORDER[char_idx:]}
                ctx.completed_stages = [s for s in ctx.completed_stages if s not in after_char]
        if ctx.completed_stages:
            last_idx = -1
            for i, s in enumerate(STAGE_ORDER):
                if s.value in ctx.completed_stages:
                    last_idx = i
            if last_idx < len(STAGE_ORDER) - 1:
                start_from = STAGE_ORDER[last_idx + 1]
                logger.info(f"[Pipeline] 从 {STAGE_LABELS.get(start_from, start_from.value)} 续传")
            elif last_idx == len(STAGE_ORDER) - 1:
                logger.info(f"[Pipeline] 所有阶段已完成")
                _update_pipeline_db(pid, 100, status=PipelineStatus.COMPLETED)
                _update_project_status(project_id, "completed", 100)
                return

        # 4) 创建管家
        orch = PipelineOrchestrator(
            ctx, pipeline_id=pid,
            db_save=lambda snap: _save_snapshot_to_db(pid, snap),
            stage_callback=lambda **kw: _orchestrator_stage_callback(**kw),
        )

        # 5) 进度回调 → 更新 DB
        total_stages = len(STAGE_ORDER)
        stage_index_map = {s: i for i, s in enumerate(STAGE_ORDER)}

        def progress_cb(stage_val: str, status: str, data: Any = None):
            stage_idx = stage_index_map.get(
                next((s for s in STAGE_ORDER if s.value == stage_val), None), 0)
            pct = int((stage_idx + 1) / total_stages * 100)
            stage_label = STAGE_LABELS.get(
                next((s for s in STAGE_ORDER if s.value == stage_val), None), stage_val)

            if status == "running":
                _update_pipeline_db(pid, pct, current_stage=stage_label)
                _update_project_steps(project_id, stage_label, stage_val, 30, "busy")
            elif status == "completed":
                _update_pipeline_db(pid, pct, current_stage=stage_label)
                _update_project_steps(project_id, stage_label, stage_val, 100, "done",
                                      f"{stage_label}完成")
            elif status == "failed":
                _update_project_steps(project_id, stage_label, stage_val, 0, "error",
                                      f"失败: {str(data.get('error', ''))[:80]}" if isinstance(data, dict) else "失败")

        orch.on_progress(progress_cb)

        # 6) 执行 (DAG 并行)
        logger.info(f"[Pipeline] 启动管家 DAG, pid={pid}, 起始阶段={start_from or '从头'}, retry_cycle={retry_cycle}")
        result = orch.run_sync(start_from=start_from, use_dag=True)

        # 7) 最终状态
        if result["success"]:
            _update_pipeline_db(pid, 100, status=PipelineStatus.COMPLETED)
            _update_project_status(project_id, "completed", 100)
            logger.info(f"[Pipeline] {pid} 完成 ✅")
        else:
            failed_stages = result.get("failed", [])
            if not failed_stages:
                failed_stages = [k for k, v in result.get("stages", {}).items() if not v.get("success")]

            err_msg = f"阶段失败: {failed_stages}" if failed_stages else "部分阶段失败"
            _update_pipeline_db(pid, 0, status=PipelineStatus.FAILED, error=err_msg)
            logger.warning(f"[Pipeline] {pid} 部分失败: {err_msg}")

            # 不再自动重试整个管道：失败就停下，保留原因，由人工定位后单独补/重跑。
            # 之前自动重试会导致 video 等慢阶段被反复重跑（同一镜头生成8-9次）。
            _update_project_status(project_id, "failed")
            logger.info(f"[Pipeline] {pid} 已标记 failed，等待人工处理（不再自动重试）")

    except Exception as e:
        logger.error(f"[Pipeline] 管家执行崩溃: {e}\n{traceback.format_exc()}")
        try:
            _update_pipeline_db(pid, 0, status=PipelineStatus.FAILED, error=str(e)[:500])
            _update_project_status(project_id, "failed")
        except Exception as _pe:
            logger.error(f"[pipeline] 操作失败(traceback): {_pe}", exc_info=True)


# ═══════════════════════════════════════════════════════════════════════════
# v5: 进度查询 & 续跑 API
# ═══════════════════════════════════════════════════════════════════════════

@router.get("/progress/{project_id}")
async def get_pipeline_progress(project_id: str, request: Request = None):
    """v5: 获取项目管道进度（含文件URL）"""
    try:
        progress = _aggregate_progress_data(project_id)

        # 检查项目是否 pending_retry 且已超过2分钟
        project_rows = _execute_db(
            "SELECT id, status, updated FROM projects WHERE id=?",
            (project_id,))
        project_status = project_rows[0]["status"] if project_rows else "draft"

        return {
            "success": True,
            "data": progress,
            "project_status": project_status,
        }
    except Exception as e:
        logger.error(f"获取进度失败: {e}")
        return {"success": False, "error": str(e)[:200]}


@router.post("/resume/{project_id}")
async def resume_pipeline(project_id: str, request: Request = None):
    """v5: 从断点续跑管道"""
    try:
        user_id = get_user_id(request) if request else ""

        # 加载 pipeline_progress
        stages = _get_project_progress(project_id)
        if not stages:
            return {"success": False, "message": "没有找到项目进度数据"}

        # 检查是否全部13个阶段都完成
        stage_names = [s.value for s in STAGE_ORDER]
        stage_status_map = {s["stage"]: s["status"] for s in stages}
        all_completed = all(stage_status_map.get(sn) == "completed" for sn in stage_names)
        if all_completed:
            prog = _aggregate_progress_data(project_id)
            return {
                "success": True,
                "message": "所有阶段已完成",
                "data": prog,
                "finished": True,
            }

        # 检查是否有 pending_retry 状态且已过时间
        proj_rows = _execute_db("SELECT status, updated FROM projects WHERE id=?", (project_id,))
        project_status = proj_rows[0]["status"] if proj_rows else "draft"

        # 获取上下文数据（从 projects 表）
        p_rows = _execute_db(
            "SELECT id, title, script, genre, characters FROM projects WHERE id=?",
            (project_id,))
        if not p_rows:
            return {"success": False, "message": "项目不存在"}

        proj = p_rows[0]
        title = proj["title"] or ""
        script_text = proj["script"] or ""
        genre = proj["genre"] or ""
        characters = json.loads(proj["characters"] or "[]")

        # 从已完成阶段汇总简历上下文
        resume_data = {}
        for s in stages:
            if s["status"] == "completed":
                outer_key = f"{s['stage']}_result"
                resume_data[outer_key] = {
                    "data": s.get("data", {}),
                    "success": True,
                }
                # 从data中提取关键字段
                d = s.get("data", {})
                if s["stage"] == "script" and d.get("script"):
                    script_text = d["script"] or script_text
                    title = d.get("title", title)
                elif s["stage"] == "character" and d.get("characters"):
                    characters = d["characters"] or characters

        if not resume_data:
            return {"success": False, "message": "没有已完成的阶段，请从 API start 开始"}

        pid = _get_latest_pipeline_id(project_id) or f"pipe_{int(time.time()*1000)}_{project_id}"

        # 启动后台续跑
        _update_project_status(project_id, "running")

        def wrapped_resume():
            try:
                _run_with_orchestrator(
                    pid=pid, project_id=project_id,
                    genre=genre, script_text=script_text,
                    title=title, synopsis=script_text,
                    characters=characters,
                    resume_from=resume_data,
                    retry_cycle=0,
                )
            except Exception as exc:
                logger.error(f"[Pipeline] resume 崩溃: {exc}\n{traceback.format_exc()}")

        _executor.submit(wrapped_resume)

        return {
            "success": True,
            "message": f"续跑已启动 (已完成 {len(resume_data)} 个阶段)",
            "pipeline_id": pid,
            "project_id": project_id,
            "completed_stages": list(resume_data.keys()),
            "remaining_stages": len(STAGE_ORDER) - len(resume_data),
        }

    except Exception as e:
        logger.error(f"续跑失败: {e}\n{traceback.format_exc()}")
        return {"success": False, "message": str(e)[:200]}


# ═══════════════════════════════════════════════════════════════
# 手动逐步执行：会员未点一键生成时，逐步执行单个阶段
# ═══════════════════════════════════════════════════════════════

# 阶段 → 智能体映射（与 agents.py AGENT_MAP 对齐）
_STEP_AGENT_MAP = {
    "director":       ("director",       "analyze_script"),
    "script":         ("script",         "generate_script"),
    "character":      ("character",      "extract"),
    "storyboard":     ("storyboard",     "generate"),
    "cinematographer":("cinematographer","design"),
    "wardrobe":       ("wardrobe",       "design"),
    "sfx":            ("sfx",            "design"),
    "scene":          ("scene",          "batch_generate"),
    "tts":            ("tts",            "generate"),
    "subtitle":       ("subtitle",       "generate"),
    "bgm":            ("bgm",            "generate_bgm"),
    "video":          ("video",          "generate"),
    "composite":      ("composite",      "composite"),
}

class StepRequest(BaseModel):
    stage: str                                  # director/script/character/...
    action: str = ""                            # 可选，覆盖默认 action
    params: Dict[str, Any] = {}                 # 传给智能体的参数
    pipeline_id: str = ""                       # 可选，指定 pipeline


@router.post("/step/{project_id}")
async def step_pipeline(project_id: str, body: StepRequest, request: Request):
    """V2统一管道 - 所有步骤请求自动启动V2全流程"""
    from routers.pipeline_v2 import start as v2_start
    from core.pipeline_ids import next_id
    
    # 构建V2请求
    class V2Req:
        def __init__(self):
            self.title = ""
            self.genre = ""
            self.synopsis = ""
            self.script_text = ""
            self.user_id = 0
            self.episode = 1
    
    v2req = V2Req()
    try:
        v2req.user_id = get_user_id(request)
    except:
        v2req.user_id = 0
    
    # 从数据库获取项目信息
    try:
        rows = _execute_db("SELECT script, genre, title FROM projects WHERE id=?", (str(project_id),))
        if rows:
            v2req.script_text = rows[0].get("script", "") or ""
            v2req.genre = rows[0].get("genre", "") or ""
            v2req.title = rows[0].get("title", "") or ""
    except:
        pass
    
    # 从请求参数补充
    params = body.params or {}
    v2req.script_text = v2req.script_text or params.get("script_text", "")
    v2req.genre = v2req.genre or params.get("genre", "") or "现代"
    v2req.synopsis = (v2req.script_text or "")[:100]
    v2req.title = v2req.title or (v2req.script_text or "短剧")[:20]
    # 传递角色数据
    if "characters" in params:
        v2req.characters = params["characters"]
    
    logger.info(f"[V2] step '{body.stage}' → 启动完整V2管道 project={project_id}, chars={len(v2req.characters)}")
    result = await v2_start(v2req, request)
    # 返回 v2_pipeline_id 供前端轮询
    result["v2_pipeline_id"] = result.get("pipeline_id", "")
    return result


@router.get("/step-status/{project_id}")
async def step_status(project_id: str, request: Request):
    """查询项目的逐步执行状态：哪些阶段已完成、当前应执行哪个阶段。
    供前端继续创作时定位到断点步骤。"""
    try:
        user_id = get_user_id(request)
        owner = _execute_db("SELECT user_id, title, genre, script FROM projects WHERE id=?", (str(project_id),))
        if not owner:
            return {"success": False, "error": "项目不存在"}
        if str(owner[0]["user_id"]) != str(user_id) and user_id:
            return {"success": False, "error": "无权操作此项目"}

        stages = _get_project_progress(project_id)
        completed = [s["stage"] for s in stages if s["status"] == "completed"]
        # 下一个待执行阶段（按 STAGE_ORDER 顺序）
        next_stage = ""
        for sv in STAGE_ORDER:
            if sv.value not in completed:
                next_stage = sv.value
                break

        # 各阶段结果数据（供前端回显）
        stage_data = {s["stage"]: s["data"] for s in stages if s["status"] == "completed"}

        return {
            "success": True,
            "project_id": str(project_id),
            "title": owner[0]["title"],
            "genre": owner[0]["genre"],
            "completed_stages": completed,
            "next_stage": next_stage,
            "stage_data": stage_data,
            "all_completed": len(completed) >= len(STAGE_ORDER),
        }
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}


# ─── API 端点 ──────────────────────────────────────────

@router.post("/start")
async def start_pipeline(body: PipelineStart, request: Request):
    try:
        user_id = get_user_id(request)

        # mode=script_only: 用管家调度剧本agent
        if body.mode == "script_only":
            synopsis_text = body.synopsis or body.plot or body.script_text or body.title
            if not synopsis_text or len(synopsis_text.strip()) < 5:
                return StartResponse(success=False, message="内容太短，至少5个字")
            try:
                # 创建项目以便前端拿到 project_id
                all_stage_keys = [s.value for s in STAGE_ORDER]
                steps = [{"key": sk, "label": STAGE_UI.get(sk, {}).get("label", sk), "icon": STAGE_UI.get(sk, {}).get("icon", "?"), "desc": "", "status": "idle"} for sk in all_stage_keys]
                _execute_db("INSERT INTO projects (title, script, genre, pipeline_steps, progress, status, user_id) VALUES (?,?,?,?,0,'active',?)",
                    (body.title or body.genre or "短剧", synopsis_text[:500], body.genre, json.dumps(steps, ensure_ascii=False), user_id))
                script_project_id = str(_execute_db("SELECT last_insert_rowid()")[0][0])
                ctx = create_context(synopsis=synopsis_text, genre=body.genre or "都市", project_id=script_project_id)
                orch = PipelineOrchestrator(ctx, pipeline_id=f"pipe_{script_project_id}", stage_callback=lambda **kw: None)
                success, data, error = await orch.run_single(Stage.SCRIPT)
                if success:
                    script = data.get("script") or data.get("outline") or data.get("text") or ""
                    if not script and data.get("title"):
                        script = data["title"] + "\n\n" + (data.get("outline") or data.get("summary") or "")
                    if script:
                        logger.info(f"[Pipeline] script_only 生成成功 ({len(script)}字)")
                        return StartResponse(success=True, script=script, message="剧本生成成功", project_id=script_project_id)
                logger.warning(f"[Pipeline] script_only 管家返回失败: {error[:200]}")
                return StartResponse(success=False, message="剧本生成失败，请重试")
            except Exception as e:
                logger.error(f"[Pipeline] script_only异常: {e}")
                return StartResponse(success=False, message=f"生成异常: {str(e)[:100]}")

        # ── v5: 两阶段提交 ──
        # 先查 project_id 是否有未完成的 pipeline_progress
        project_db_id = body.project_id

        # 获取或创建项目
        rows = _execute_db("SELECT id, title, status FROM projects WHERE id=?", (project_db_id,))
        if not rows:
            rows = _execute_db("SELECT id, title, status FROM projects WHERE title=? LIMIT 1",
                               (body.title or body.project_id,))

        if not rows:
            # 创建新项目
            all_stage_keys = [s.value for s in STAGE_ORDER]
            steps = [{
                "key": sk, "label": STAGE_UI.get(sk, {}).get("label", sk),
                "icon": STAGE_UI.get(sk, {}).get("icon", "❓"),
                "desc": "", "status": "idle", "progress": 0, "duration": "", "log": ""
            } for sk in all_stage_keys]
            _execute_db(
                "INSERT INTO projects (title, script, genre, pipeline_steps, progress, status, user_id) VALUES (?,?,?,?,0,'active',?)",
                (body.title or body.genre or "短剧", (body.script_text or body.synopsis or body.plot)[:500],
                 body.genre, json.dumps(steps, ensure_ascii=False), user_id))
            project_db_id = str(_execute_db("SELECT last_insert_rowid()")[0][0])
            has_existing_progress = False
        else:
            project_db_id = str(rows[0]["id"])
            # 检查是否有未完成进度
            existing_stages = _get_project_progress(project_db_id)
            has_existing_progress = len(existing_stages) > 0
            has_incomplete = any(s["status"] in ("pending", "running", "failed") for s in existing_stages)

            # 一键生成策略：保留已完成的阶段，只清理 failed/running 的，从断点继续。
            # 这样用户手动做完角色图/场景图后点一键生成，不会重做，直接从下一阶段开始。
            if has_existing_progress and has_incomplete:
                completed_stages = [s for s in existing_stages if s["status"] == "completed"]
                if completed_stages:
                    logger.info(f"[Pipeline] 项目 {project_db_id} 有 {len(completed_stages)} 个已完成阶段，保留并续跑")
                    # 只删除 failed/running 的进度，保留 completed
                    try:
                        _execute_db(
                            "DELETE FROM pipeline_progress WHERE project_id=? AND status IN ('failed','running','pending')",
                            (str(project_db_id),))
                    except Exception as _ce:
                        logger.warning(f"[Pipeline] 清理失败阶段失败: {_ce}")
                else:
                    # 没有已完成的 → 全清从头跑
                    logger.info(f"[Pipeline] 项目 {project_db_id} 无已完成阶段，清理旧进度从头跑")
                    try:
                        _execute_db("DELETE FROM pipeline_progress WHERE project_id=?", (str(project_db_id),))
                    except Exception as _ce:
                        logger.warning(f"[Pipeline] 清理旧进度失败: {_ce}")
                    has_existing_progress = False

        # ── 正常启动 ──
        # 流派推导
        if (not body.genre or body.genre.strip() == ""):
            # 先看 title 关键词
            hint = (body.title or "").strip()
            # 再看剧本内容关键词（更准确）
            script_hint = (body.script_text or body.synopsis or body.plot or "")[:2000]
            detect_text = hint + " " + script_hint
            for kw in ["古装", "古代", "朕", "皇子", "将军", "战场", "攻城",
                        "仙侠", "修真", "灵力", "飞剑", "渡劫",
                        "武侠", "江湖", "武林", "镖局",
                        "宫斗", "后宫", "嫔妃", "太后",
                        "玄幻", "魔法", "异世界",
                        "商战", "并购", "董事长", "股份", "财务造假", "CEO", "投资", "上市",
                        "职场", "面试", "加班", "老板", "同事", "打卡",
                        "甜宠", "总裁", "恋爱", "表白",
                        "悬疑", "谋杀", "侦探", "案件",
                        "都市", "城市"]:
                if kw in detect_text:
                    body.genre = kw
                    logger.info(f"[Pipeline] 自动检测类型: {kw}")
                    break
            if not body.genre:
                body.genre = "都市"  # 默认都市
                logger.info(f"[Pipeline] 类型未检测到，默认都市")

        pid = f"pipe_{int(time.time())}_{body.project_id}"

        # 断点续跑: 加载旧管线结果
        resume_data = None
        if body.resume_pipeline_id:
            try:
                rows = _execute_db(
                    "SELECT step_results, script_text, genre, project_id FROM pipelines WHERE id=?",
                    (body.resume_pipeline_id,))
                if rows:
                    old_row = rows[0]
                    sr_raw = old_row[0] or "{}"
                    resume_data = json.loads(sr_raw) if isinstance(sr_raw, str) else sr_raw
                    logger.info(f"[Pipeline] 续传: 加载 {len(resume_data)} 阶段结果")
                    if not body.script_text and old_row[1]:
                        body.script_text = old_row[1]
                    if not body.genre and old_row[2]:
                        body.genre = old_row[2]
            except Exception as e:
                logger.warning(f"[Pipeline] 加载续传数据失败: {e}")

        # 创建 pipeline 记录
        _execute_db(
            "INSERT INTO pipelines (id, project_id, script_text, genre, status, created, updated, user_id) VALUES (?,?,?,?,'pending',?,?,?)",
            (pid, project_db_id, (body.script_text or body.synopsis or body.plot)[:1000], body.genre, time.time(), time.time(), user_id))

        # 更新项目状态为 active
        _update_project_status(project_db_id, "active")

        # 如果项目已存在 pipeline_steps，保留；否则初始化
        existing_steps = _execute_db(
            "SELECT pipeline_steps FROM projects WHERE id=?", (project_db_id,))
        if existing_steps and existing_steps[0][0]:
            try:
                steps = json.loads(existing_steps[0][0])
                if len(steps) < len(STAGE_ORDER):
                    all_stage_keys = [s.value for s in STAGE_ORDER]
                    existing_keys = {s.get("key") for s in steps}
                    for sk in all_stage_keys:
                        if sk not in existing_keys:
                            steps.append({
                                "key": sk, "label": STAGE_UI.get(sk, {}).get("label", sk),
                                "icon": STAGE_UI.get(sk, {}).get("icon", "❓"),
                                "desc": "", "status": "idle", "progress": 0, "duration": "", "log": ""
                            })
                    _execute_db(
                        "UPDATE projects SET pipeline_steps=? WHERE id=?",
                        (json.dumps(steps, ensure_ascii=False), project_db_id))
            except Exception as _pe:
                logger.error(f"[pipeline] 操作失败(traceback): {_pe}", exc_info=True)

        # 自动复用旧管线结果 + 从 pipeline_progress 加载已完成阶段
        if not resume_data and project_db_id:
            # 优先从 pipeline_progress 加载所有已完成阶段（逐步操作的成果）
            try:
                pp_rows = _execute_db(
                    "SELECT stage, data FROM pipeline_progress WHERE project_id=? AND status='completed'",
                    (str(project_db_id),))
                if pp_rows:
                    resume_data = {}
                    for r in pp_rows:
                        stage_name = r["stage"]
                        stage_data = json.loads(r["data"] or "{}")
                        outer_key = f"{stage_name}_result"
                        resume_data[outer_key] = {"data": stage_data, "success": True}
                    logger.info(f"[Pipeline] 从 pipeline_progress 加载 {len(resume_data)} 个已完成阶段: {list(resume_data.keys())}")
            except Exception as e:
                logger.warning(f"[Pipeline] 加载 pipeline_progress 失败: {e}")

            # 兜底：从旧 pipelines.step_results 加载
            if not resume_data:
                try:
                    prev_rows = _execute_db(
                        "SELECT id, step_results FROM pipelines WHERE project_id=? AND status='completed' ORDER BY created DESC LIMIT 1",
                        (str(project_db_id),))
                    if prev_rows:
                        prev_data = json.loads(prev_rows[0][1]) if isinstance(prev_rows[0][1], str) else (prev_rows[0][1] or {})
                        resume_data = {}
                        for k in ["character", "scene", "tts", "bgm"]:
                            outer_key = f"{k}_result"
                            if k in prev_data and prev_data[k]:
                                resume_data[outer_key] = prev_data[k]
                        if resume_data:
                            logger.info(f"[Pipeline] 自动复用旧管线: {list(resume_data.keys())}")
                except Exception as e:
                    logger.warning(f"[Pipeline] 自动复用失败: {e}")

        # 启动后台任务
        def wrapped_run():
            try:
                proj_data = _execute_db("SELECT script,genre,characters FROM projects WHERE id=?", (project_db_id,))
                if proj_data:
                    proj_script = dict(proj_data[0]).get("script","") or ""
                    proj_genre = dict(proj_data[0]).get("genre","") or ""
                    try: proj_chars = json.loads(dict(proj_data[0]).get("characters","") or "[]")
                    except Exception as _pe:
                        logger.error(f"[pipeline] 操作失败(traceback): {_pe}", exc_info=True)

                    # proj_chars = []  # already assigned in try block above
                else:
                    proj_script = ""; proj_genre = ""; proj_chars = []
                _run_with_orchestrator(
                    pid=pid, project_id=project_db_id,
                    genre=body.genre or body.title or proj_genre,
                    script_text=body.script_text or body.synopsis or proj_script,
                    title=body.title, synopsis=body.synopsis or proj_script,
                    characters=body.characters or proj_chars,
                    resume_from=resume_data,
                )
            except Exception as exc:
                logger.error(f"[Pipeline] _run_with_orchestrator crashed: {exc}\
{traceback.format_exc()}")
                try:
                    _execute_db("UPDATE pipelines SET status=\"failed\", error=?, updated=? WHERE id=?",
                                (str(exc)[:500], time.time(), pid))
                except Exception as _pe:
                    logger.error(f"[pipeline] 操作失败(traceback): {_pe}", exc_info=True)

        _executor.submit(wrapped_run)

        return StartResponse(
            success=True,
            pipeline_id=pid,
            project_id=project_db_id,
            task_id=project_db_id,
            status="started",
            message="流水线已启动（管家 DAG 模式）"
        )

    except Exception as e:
        logger.error(f"[Pipeline] 启动失败: {e}\n{traceback.format_exc()}")
        return StartResponse(success=False, message=f"启动失败: {str(e)[:200]}")


# ─── 其他 API 端点 ─────────────────────────────────────

def _format_stages(step_results: Dict[str, Any], current_stage: str) -> List[Dict]:
    """格式化阶段数据供前端展示"""
    stages = []
    for s in STAGE_ORDER:
        key = f"{s.value}_result"
        ui = STAGE_UI.get(s.value, {"label": s.value, "icon": "❓"})
        raw = step_results.get(s.value, step_results.get(key, {}))
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except Exception as _pe:
                logger.warning(f"[pipeline] 操作失败: {_pe}")
                raw = {}

            # raw already assigned by json.loads above
        elif hasattr(raw, '__dataclass_fields__'):
            raw = asdict(raw)

        done = bool(raw and raw != {} and raw.get("success") is not False)
        is_current = (s.value == current_stage or STAGE_LABELS.get(s, "") == current_stage)
        stages.append({
            "key": key,
            "label": ui["label"],
            "icon": ui["icon"],
            "done": done,
            "current": is_current,
            "data": raw if done else {},
        })
    return stages


@router.get("/{pipeline_id}/status")
async def status_pipeline_alias(pipeline_id: str):
    return await status_pipeline(pipeline_id)


@router.get("/status/{pipeline_id}")
async def status_pipeline(pipeline_id: str):
    try:
        rows = _execute_db("SELECT * FROM pipelines WHERE id=?", (pipeline_id,))
        if not rows:
            return {"success": False, "error": "pipeline not found"}

        row = dict(rows[0])
        step_results = json.loads(row.get("step_results", "{}") or "{}")
        current_stage = row.get("current_stage", "")
        status_val = row.get("status", "pending")

        # 查找视频URL
        # 写入路径：agent_composite 把成片写到 storage/{pipeline_id}/videos/final_{ts}_{pid}.mp4
        # glob 模式要覆盖：平铺目录 + pipeline_id 子目录 + 递归兜底
        video_url = ""
        try:
            safe_id = pipeline_id.replace(":", "_").replace("/", "_")
            patterns = [
                # 优先：pipeline_id 子目录（与 agent_composite 写入路径对齐）
                f"/www/wwwroot/storage/{safe_id}/videos/*.mp4",
                f"/www/wwwroot/storage/{safe_id[:20]}*/videos/*.mp4",
                # 其次：平铺 videos 目录（文件名含 pipeline_id）
                f"/www/wwwroot/storage/videos/*_{safe_id}.mp4",
                f"/www/wwwroot/storage/videos/*_{safe_id[:20]}*.mp4",
                # 兜底：storage 根目录（command.py 旧式 ord_id.mp4）
                f"/www/wwwroot/storage/*_{safe_id}.mp4",
                f"/www/wwwroot/storage/*_{safe_id[:20]}*.mp4",
                # 最终兜底：递归扫所有 videos 子目录，取最新
                f"/www/wwwroot/storage/**/videos/*.mp4",
            ]
            for pat in patterns:
                matches = glob.glob(pat, recursive=True)
                if matches:
                    # 取最新修改的文件
                    matches.sort(key=lambda p: os.path.getmtime(p), reverse=True)
                    video_url = local_to_url(matches[0])
                    break
        except Exception as _pe:
            logger.warning(f"[pipeline] failed: {_pe}")
            raw = {}

        if not video_url:
            # 从 step_results 找
            # 注意：orchestrator snapshot 存的是 {"results": {"composite": {...}}, "final_video": ...}
            # composite 嵌在 results 里，早期代码用 step_results.get("composite") 取不到。
            try:
                results_nested = step_results.get("results", {})
                # 优先用顶层 final_video（COMPOSITE post_process 写入的最终视频）
                top_final = step_results.get("final_video", "")
                if top_final:
                    # 本地路径 → 公网URL（已是URL则原样返回）
                    video_url = local_to_url(top_final)
                # 兜底：从 results.composite / results.video 取 output
                if not video_url:
                    for key in ['composite', 'video']:
                        # 兼容两种结构：顶层 step_results[key] 和嵌套 step_results["results"][key]
                        sr = step_results.get(key, results_nested.get(key, {}))
                        if isinstance(sr, dict):
                            url = sr.get("video_url", "") or sr.get("output", "") or sr.get("output_path", "") or sr.get("result_url", "")
                            if url:
                                video_url = local_to_url(url)
                                break
            except Exception as _pe:
                logger.warning(f"[pipeline] failed: {_pe}")
                raw = {}

        # 注册到媒体库
        if video_url and video_url.startswith(f"{BASE_URL}/storage/"):
            try:
                local_path = url_to_local_path(video_url)
                if os.path.exists(local_path):
                    from routers.media_router import save_and_register
                    fname = os.path.basename(local_path)
                    with open(local_path, "rb") as vf:
                        vdata = vf.read()
                    save_and_register(vdata, fname, "videos", fname.replace(".mp4", ""),
                                      row.get("project_id", ""), pipeline_id, row.get("user_id", ""))
            except Exception as _pe:
                logger.warning(f"[pipeline] failed: {_pe}")
                raw = {}

        # 构建消息
        if status_val == "completed":
            message = "短剧生成完成！"
        elif status_val in ("failed", "failed_permanent"):
            message = "生成失败：" + str(row.get("error", ""))[:100]
        elif status_val == "pending_retry":
            message = "自动重试中，请稍候..."
        elif status_val == "running":
            message = f"正在生成中... {row.get('progress', 0)}%"
        elif status_val == "pending":
            message = "排队等待中..."
        else:
            message = f"状态：{status_val or '未知'}"

        return {
            "success": True,
            "data": {
                "progress": row.get("progress", 0),
                "status": "paused" if status_val == "failed" else status_val,
                "message": message,
                "video_url": video_url,
                "current_stage": current_stage,
                "total_stages": row.get("total_stages", 13),
                "shots": _format_stages(step_results, current_stage),
                "error": row.get("error", ""),
            }
        }
    except Exception as e:
        logger.error(f"获取pipeline状态失败: {e}")
        return {"success": False, "error": str(e)[:200]}


@router.get("/script/stream")
async def script_stream(task_id: str = "", request: Request = None):
    """返回真实剧本的SSE流--从已完成的pipeline或script_only结果读取"""
    async def event_generator():
        script_text = ""
        title = "剧本"
        if task_id:
            script_text, title = _get_script_from_pipeline(task_id)
        
        if not script_text:
            lines = ["（暂无剧本数据）"]
        else:
            lines = [line for line in script_text.split('\n') if line.strip() or True]
        
        if title and title != "剧本":
            lines.insert(0, f"【剧本标题】《{title}》")
            lines.insert(1, "")
        
        for i, line in enumerate(lines):
            disconnected = False
            try:
                if request and hasattr(request, 'is_disconnected'):
                    disconnected = await request.is_disconnected()
            except Exception as _pe:
                logger.warning(f"[pipeline] failed: {_pe}")
                raw = {}
            if disconnected:
                break
            done = (i == len(lines) - 1)
            payload = {'line': line, 'task_id': task_id, 'progress': int((i+1)/max(len(lines),1)*100)}
            if done:
                payload['status'] = 'completed'
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.3 if line else 0.1)
        if not lines or not any(l.strip() for l in lines[1:] if isinstance(l, str)):
            yield f"data: {json.dumps({'line': '', 'task_id': task_id, 'done': True, 'status': 'completed'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        })


def _get_script_from_pipeline(task_id: str):
    """从pipeline/缓存中获取真实剧本"""
    try:
        rows = _execute_db(
            "SELECT script_text as script, genre as title, stage_outputs as stages FROM pipelines WHERE id=?",
            (task_id,)
        )
        if rows and rows[0]:
            row = rows[0]
            script = row.get("script") or ""
            title = row.get("title") or ""
            if script:
                return script, title
            # Try stages JSON for script data
            stages = row.get("stages") or ""
            if stages:
                import json as _j
                sd = _j.loads(stages) if isinstance(stages, str) else stages
                if isinstance(sd, dict):
                    s = sd.get("script") or sd.get("data", {}).get("script") or ""
                    t = sd.get("title") or sd.get("data", {}).get("title") or ""
                    return s, t
    except Exception as _pe: logger.warning(f"[pipeline] 操作失败: {_pe}")
    return "", "剧本"


@router.get("/list")
async def list_pipelines():
    try:
        rows = _execute_db(
            "SELECT id, project_id, status, progress, created FROM pipelines ORDER BY created DESC LIMIT 50")
        return {
            "success": True,
            "pipelines": [{"id": r["id"], "project_id": r["project_id"],
                           "status": r["status"], "progress": r["progress"],
                           "created": r["created"]} for r in (rows or [])]
        }
    except Exception as e:
        logger.error(f"列出pipeline失败: {e}")
        return {"success": False, "error": str(e)[:200]}


@router.delete("/delete/{pipeline_id}")
async def delete_pipeline(pipeline_id: str):
    try:
        _execute_db("DELETE FROM pipelines WHERE id=?", (pipeline_id,))
        return {"success": True, "deleted": True}
    except Exception as e:
        logger.error(f"删除pipeline失败: {e}")
        return {"success": False, "error": str(e)[:200]}


@router.post("/character-design")
async def character_design(request: Request):
    try:
        body = await request.json()
        name = body.get("name", "角色")
        appearance = body.get("appearance", "")
        personality = body.get("personality", "")
        gender = body.get("gender", "")
        genre = body.get("genre", body.get("style", ""))

        genre_hint = f"，{genre}风格" if genre else ""
        # 题材感知的服装提示
        is_modern = genre in ("现代", "都市", "urban", "悬疑")
        era_hint_cn = (
            "现代发型现代服饰，现代时尚妆容，" if is_modern
            else "古装造型，古代发髻发饰，传统汉服装扮，"
        )
        era_hint_en = (
            "modern fashion hairstyle, contemporary clothing modern hair,"
            if is_modern
            else "ancient Chinese hairstyle, traditional hanfu clothing,"
        )
        def _strip_face_detail(s):
            import re
            bans = ['苍白','煞白','惨白','蜡黄','铁青','深陷','凹陷','浮肿','红肿','歪斜',
                    '阴鸷','诡','狡黠','蛇蝎','魔焰','黑气','邪气','鬼气','戾气','虎狼',
                    '狰狞','可怕','丑陋','邪恶','凶恶','猥琐','狡诈','阴森','披散','蓬乱',
                    '阴寒','邪异']
            for w in bans:
                s = s.replace(w, '')
            s = re.sub(r'[，,。；;]{2,}', '，', s)
            return s.strip('，, ')
        clean_appearance = _strip_face_detail(appearance)
        clean_personality = _strip_face_detail(personality)
        
        is_i2i = body.get('ref_image', '') and (body.get('ref_image', '').startswith('data:') or body.get('ref_image', '').startswith('http') or body.get('ref_image', '').startswith('/storage'))

        from agents.agent_character import CharacterAgent
        ref_image_raw = body.get("ref_image", "")
        ref_image = ref_image_raw
        if ref_image and ref_image.startswith("data:"):
            try:
                import base64, hashlib, os
                _, b64 = ref_image.split(",", 1)
                raw = base64.b64decode(b64)
                h = hashlib.md5(raw).hexdigest()[:12]
                fdir = "/www/wwwroot/storage/figures/"
                os.makedirs(fdir, exist_ok=True)
                fpath = f"{fdir}ref_{h}.jpg"
                with open(fpath, "wb") as f:
                    f.write(raw)
                ref_image = "/storage/figures/" + os.path.basename(fpath)
            except Exception as e:
                logger.warning(f"data URL保存失败: {e}")

        # 统一走 beautify_face() 的质量体系
        char_agent = CharacterAgent()

        # 构建传递给beautify_face的ref_image（需要完整URL或空）
        beautify_ref = ""
        if ref_image and (ref_image.startswith("http") or ref_image.startswith("/storage")):
            beautify_ref = local_to_url(ref_image)

        beautify_result = char_agent.beautify_face(
            user_id="",
            char_name=body.get("name", "角色"),
            ref_image=beautify_ref,
            age=body.get("age", ""),
            gender=body.get("gender", ""),
            style=genre,
            description=clean_appearance + "，" + clean_personality
        )

        if beautify_result.success:
            image_url = beautify_result.data.get("figure_url", "")
        else:
            # beautify 失败时降级到原来的简单 prompt
            from services.model_client import UnifiedModel
            if is_i2i:
                prompt = ("保留面部轮廓，正面肖象，肩膀以上，纯色背景，影棚打光，8K真实人像。"
                          + gender + "角色，" + genre + "风格")
                result = UnifiedModel.image_to_image(prompt=prompt, reference_image=beautify_ref, size="1920x1920", timeout=300, strength=0.35)
            else:
                prompt = ("超写实真人写真，" + gender + "，" + clean_appearance + "。正面肖象特写，纯色背景。8K真实人像照片。")
                result = UnifiedModel.image(prompt=prompt, size="1920x1920", timeout=300)
            image_url = result.get("url", "")

        if not image_url:
            # 最终降级
            from services.model_client import UnifiedModel
            fallback_prompt = ("超写实真人写真，" + gender + "，" + clean_appearance + "。正面肖象特写，纯色背景。8K真实人像照片。")
            result = UnifiedModel.image(prompt=fallback_prompt, size="1920x1920", timeout=300)
            image_url = result.get("url", "")


        # 角色肖像后处理：OpenCV 眼周美颜
        if image_url:
            try:
                from services.face_beautify import beautify_portrait
                import tempfile, os, hashlib
                from routers.media_router import save_and_register
                # Download and beautify
                beautified_local = beautify_portrait(image_url, strength=0.8)
                with open(beautified_local, "rb") as f:
                    img_data = f.read()
                h = hashlib.md5(img_data).hexdigest()[:12]
                fname = "portrait_face_beautified_%s.jpg" % h
                # Save with proper metadata
                result_info = save_and_register(
                    img_data, fname, "figures",
                    name="肖像美颜后处理",
                )
                if result_info and result_info.get("url"):
                    image_url = result_info["url"]
                # Cleanup temp file
                try:
                    os.remove(beautified_local)
                except:
                    pass
            except Exception as e:
                logger.warning("Face beautification post-process failed (non-critical): %s" % str(e))

        return {"success": bool(image_url), "data": {"image_url": image_url}, "error": "" if image_url else "生成失败"}
    except Exception as e:
        return {"success": False, "data": None, "error": str(e)}


@router.post("/scene-generate")
async def scene_generate(request: Request):
    try:
        body = await request.json()
        scenes = body.get("scenes", [])
        ref_characters = body.get("ref_characters", [])  # 角色参考图URL列表
        drama_genre = body.get("genre", body.get("style", ""))  # 剧的类型（修仙/都市等）
        # 自动检测：从场景描述中提取剧的类型
        if not drama_genre and scenes:
            all_desc = " ".join([s.get("description","")+s.get("name","") for s in scenes[:3]]).lower()
            if any(k in all_desc for k in ("修仙","仙","灵","修","剑","丹","法术","法阵","元气","灵力","灵气","修炼","仙界","魔","妖","神")):
                drama_genre = "修仙"
            elif any(k in all_desc for k in ("古装","皇帝","宫","妃","朝","将","侯","王爷","簪","袍","古风","古代","唐朝","宋朝","秦","汉")):
                drama_genre = "古装"
            elif any(k in all_desc for k in ("武侠","剑客","掌门","门派","帮","侠","江湖")):
                drama_genre = "武侠"
        from services.model_client import UnifiedModel
        from agents.agent_scene import SceneAgent
        scene_agent = SceneAgent()
        # Deduplicate by scene name: same location = same image (coherent backgrounds across shots)
        results = []
        generated = {}  # scene_name -> image_url
        first_model = ""  # 同模型走到底，记录首个成功模型
        # ── 素材库优先复用 ──
        import sqlite3, os as _os
        _ml_db = sqlite3.connect(_os.path.join(_os.path.dirname(_os.path.dirname(__file__)), 'data', 'short_drama.db'))
        
        for scene in scenes:
            name = scene.get("name", scene.get("scene", "")).strip()
            desc = scene.get("description", "室内场景")
            
            # 同名场景已在此次批量中生成过，直接复用
            if name and name in generated:
                results.append({
                    "name": name,
                    "image_url": generated[name],
                    "status": "done",
                    "source": "cache"
                })
                continue
            
            # ═══ 查素材库：同风格的场景图直接复用 ═══
            reuse_url = ""
            if name:
                try:
                    _rows = _ml_db.execute(
                        "SELECT file_path FROM media_library WHERE media_type='scenes' AND file_size>15000 ORDER BY id DESC LIMIT 20"
                    ).fetchall()
                    # 简单匹配：场景名或描述关键词相似
                    keywords = name[:20] if name else ""
                    for _r in _rows:
                        _fp = _r[0] or ""
                                                # 2-字 n-gram 模糊匹配
                        if keywords and len(keywords) >= 2:
                            ngrams = [keywords[i:i+2] for i in range(len(keywords)-1)]
                            if any(ng in _fp for ng in ngrams):
                                reuse_url = _fp
                                break
                except Exception:
                    pass
            
            if reuse_url:
                logger.info(f"[Pipeline] ♻️ 素材库命中: {name[:30]} → {reuse_url}")
                if name:
                    generated[name] = reuse_url
                results.append({
                    "name": name or "",
                    "image_url": reuse_url,
                    "status": "done",
                    "source": "library"
                })
                continue
            
            # 用场景智能体的 rich prompt builder 保证风格一致+反动漫+反现代
            try:
                shot = {"description": desc, "scene": name or "", 
                        "lighting": "", "emotion": "", "weather": "",
                        "outfit": {}, "props": {}}
                prompt = scene_agent._build_rich_scene_prompt(shot, drama_genre)
            except Exception:
                prompt = (
                    f"写实电影质感，{desc}，电影级灯光，超高清画质，"
                    f"真人实拍风格，cinematic live action film photography, "
                    f"photorealistic real photography, real human face, "
                    f"真实人脸五官清晰面部完整"
                )
            url = ""
            # i2i: 有角色参考图时优先用图生图保留脸型
            if ref_characters and len(ref_characters) > 0:
                ref_img = ref_characters[0]
                if ref_img and (ref_img.startswith('http') or ref_img.startswith('/storage/')):
                    try:
                        r = UnifiedModel.image_to_image(
                            prompt=prompt,
                            reference_image=ref_img,
                            size="1920x1920",
                            timeout=120,
                            strength=0.25
                        )
                        url = r.get("url", "") if isinstance(r, dict) else ""
                    except Exception:
                        url = ""
            if not url:
                r = UnifiedModel.image(prompt=prompt, size="1920x1920", timeout=300, preferred=first_model)
                url = r.get("url", "")
                if url and not first_model:
                    first_model = r.get("model", "")
            if url and name:
                generated[name] = url
            results.append({
                "name": name or "",
                "image_url": url,
                "status": "done" if url else "error"
            })
        return {"success": True, "data": {"scenes": results}, "error": ""}
    except Exception as e:
        return {"success": False, "data": None, "error": str(e)}


@router.get("/audio/tracks")
async def audio_tracks(task_id: str = ""):
    try:
        base = "/www/wwwroot/storage"
        tracks = {"voice": {"has": False, "url": ""}, "bgm": {"has": False, "url": ""}, "sfx": {"has": False, "url": ""}}

        audio_files = glob.glob(f"{base}/audio/**/*.mp3", recursive=True) + glob.glob(f"{base}/audio/**/*.wav", recursive=True)
        if audio_files:
            tracks["voice"]["has"] = True
            tracks["voice"]["url"] = audio_files[0].replace("/www/wwwroot", "")

        bgm_files = glob.glob(f"{base}/bgm/**/*.mp3", recursive=True)
        if bgm_files:
            tracks["bgm"]["has"] = True
            tracks["bgm"]["url"] = bgm_files[0].replace("/www/wwwroot", "")

        sfx_files = glob.glob(f"{base}/sfx/**/*.mp3", recursive=True) + glob.glob(f"{base}/sfx/**/*.wav", recursive=True)
        if sfx_files:
            tracks["sfx"]["has"] = True
            tracks["sfx"]["url"] = sfx_files[0].replace("/www/wwwroot", "")

        return {"success": True, "data": tracks, "error": ""}
    except Exception as e:
        return {"success": False, "data": None, "error": str(e)}

# ── v6: 启动时自动续跑暂停的任务 ──
def _resume_paused_on_startup():
    """PM2重启后扫描paused任务并自动续跑（带超时保护，避免阻塞启动或引发崩溃）"""
    import concurrent.futures
    try:
        db = _get_conn()
        rows = db.execute(
            "SELECT DISTINCT project_id, pipeline_id FROM pipeline_progress WHERE status='completed' AND project_id IN (SELECT DISTINCT project_id FROM pipeline_progress WHERE status='completed' GROUP BY project_id HAVING COUNT(*) < 13) ORDER BY id DESC LIMIT 5"
        ).fetchall()
        if rows:
            logger.info(f"[Pipeline] 🔄 发现 {len(rows)} 个暂停任务，开始续跑...")
            for row in rows:
                pid = row[0]
                try:
                    # 每个任务限30秒超时，超时就跳过不阻塞启动
                    import asyncio
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    future = loop.create_task(resume_pipeline(project_id=pid))
                    loop.run_until_complete(asyncio.wait_for(future, timeout=30))
                    loop.close()
                    logger.info(f"[Pipeline] ✅ 续跑成功: project={pid}")
                except asyncio.TimeoutError:
                    logger.warning(f"[Pipeline] ⏰ 续跑超时(30s) project={pid}: 跳过")
                    try:
                        loop.close()
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(f"[Pipeline] ⚠️ 续跑失败 project={pid}: {e}")
                    try:
                        loop.close()
                    except Exception:
                        pass
        db.close()
    except Exception as e:
        logger.warning(f"[Pipeline] 启动续跑扫描失败: {e}")

# 延迟执行（等PM2完全启动）
# [已禁用] 启动时自动续跑会带着过期数据跑旧管线，改为用户提交时清理旧进度从头跑
# import threading
# threading.Thread(target=lambda: (__import__('time').sleep(3), _resume_paused_on_startup()), daemon=True).start()


@router.post("/shot/list")
async def shot_list(request: Request):
    """分镜列表/生成：调 storyboard agent 根据剧本+角色生成分镜"""
    try:
        body = await request.json()
        project_id = body.get("project_id", "")
        script_text = body.get("script_text", "")
        characters = body.get("characters", [])

        if not script_text:
            return {"success": False, "message": "剧本内容为空"}

        # 先查 DB 有没有已生成的分镜
        if project_id:
            try:
                row_rows = _execute_db("SELECT step_results FROM pipelines WHERE project_id=? ORDER BY created DESC LIMIT 1", (str(project_id),))
                row = row_rows[0] if row_rows else None
                if row and row.get("step_results"):
                    sr = json.loads(row["step_results"] or "{}")
                    results = sr.get("results", {}) if isinstance(sr.get("results"), dict) else {}
                    sb = results.get("storyboard", {})
                    if sb:
                        shots = sb.get("data", {}).get("shots", [])
                        if shots:
                            return {"success": True, "data": {"shots": shots}}
            except Exception:
                pass

        # 没有已生成的分镜 → 调 storyboard agent 生成
        import importlib
        from routers.agents import AGENT_MAP, _get_or_init_registry
        agent_path = AGENT_MAP.get("storyboard")
        if not agent_path:
            return {"success": False, "error": "storyboard agent 未注册"}
        mod_path, cls_name = agent_path.rsplit(".", 1)
        mod = importlib.import_module(mod_path)
        reg = _get_or_init_registry()
        agent = getattr(mod, cls_name)(tool_registry=reg, agent_name_for_tools="StoryboardAgent")

        genre = ""
        try:
            prow_rows = _execute_db("SELECT genre FROM projects WHERE id=?", (str(project_id),))
            prow = prow_rows[0] if prow_rows else None
            if prow:
                genre = prow.get("genre", "") or ""
        except Exception:
            pass

        import asyncio
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: agent.run(
            action="generate",
            script_text=script_text,
            characters=characters,
            scenes=[],
            genre=genre,
        ))

        if result.success:
            shots = result.data.get("shots", [])
            return {"success": True, "data": {"shots": shots}}
        else:
            return {"success": False, "message": result.error or "分镜生成失败"}
    except Exception as e:
        logger.error(f"[shot/list] error: {e}")
        return {"success": False, "message": f"分镜生成异常: {str(e)[:100]}"}



def _prefill_stage(project_id: str, pid: str, stage_name: str, reg):
    """补齐缺失的阶段（TTS/BGM/字幕），供合成前置检查调用"""
    import importlib
    from routers.agents import AGENT_MAP
    _sa = _STEP_AGENT_MAP.get(stage_name)
    if not _sa:
        return
    _ag_id, _act = _sa
    _ap = AGENT_MAP.get(_ag_id)
    if not _ap:
        return
    _mp, _cn = _ap.rsplit(".", 1)
    _mod = importlib.import_module(_mp)
    _agent = getattr(_mod, _cn)(tool_registry=reg, agent_name_for_tools=_cn)
    # 加载 shots + scene + 角色锁脸
    _sb = _execute_db("SELECT data FROM pipeline_progress WHERE project_id=? AND stage='storyboard' AND status='completed' ORDER BY id DESC LIMIT 1", (str(project_id),))
    _shots = json.loads(_sb[0]["data"] or "{}").get("shots", []) if _sb else []
    _sc = _execute_db("SELECT data FROM pipeline_progress WHERE project_id=? AND stage='scene' AND status='completed' ORDER BY id DESC LIMIT 1", (str(project_id),))
    _im = json.loads(_sc[0]["data"] or "{}").get("image_map", {}) if _sc else {}
    for _i, _s in enumerate(_shots):
        _u = _im.get(str(_i), "")
        if _u:
            _s["scene_image"] = _u
            _s["image_url"] = _u
    _pr = _execute_db("SELECT characters FROM projects WHERE id=?", (str(project_id),))
    if _pr:
        _chars = json.loads(_pr[0]["characters"] or "[]")
        for _s in _shots:
            _txt = _s.get("description", "") + _s.get("dialogue", "")
            for _ch in _chars:
                _cn2 = _ch.get("name", "")
                if _cn2 and _cn2 in _txt:
                    _pu = _ch.get("portrait_url", "") or _ch.get("figure_url", "")
                    if _pu:
                        _s["character_image"] = _pu
                        break
    _r = _agent.run(action=_act, shots=_shots, genre="都市", script_text="", project_id=str(project_id), pipeline_id=pid, characters=[])
    if _r and _r.success:
        _save_stage_progress(pipeline_id=pid, project_id=str(project_id), stage=stage_name, status="completed", data=_r.data or {}, error="")
        logger.info("[Step] 自动补齐 %s 完成" % stage_name)
    else:
        logger.warning("[Step] 自动补齐 %s 未成功: %s" % (stage_name, _r.error if _r else "无返回"))


# 单镜头视频生成（异步：提交即返回，后台生成，前端轮询拿结果）
@router.post("/single-video/{project_id}")
async def single_video(project_id: str, body: dict, request: Request):
    try:
        user_id = get_user_id(request)
        owner = _execute_db("SELECT user_id FROM projects WHERE id=?", (str(project_id),))
        if not owner:
            return {"success": False, "error": "项目不存在"}
        if str(owner[0]["user_id"]) != str(user_id) and user_id:
            return {"success": False, "error": "无权操作"}

        shot_index = body.get("shot_index")
        shot_data = body.get("shot", {})
        if shot_index is None or not isinstance(shot_index, int):
            return {"success": False, "error": "需要 shot_index 整数"}

        pid = _get_latest_pipeline_id(str(project_id)) or "pipe_%d_%s" % (int(time.time()), project_id)

        # 导演检查（缺了自动补）
        _dir_check = _execute_db("SELECT status, data FROM pipeline_progress WHERE project_id=? AND stage='director' ORDER BY id DESC LIMIT 1", (str(project_id),))
        if not _dir_check or _dir_check[0]["status"] != "completed":
            try:
                import importlib
                from routers.agents import AGENT_MAP, _get_or_init_registry
                _dp = AGENT_MAP.get("director")
                if _dp:
                    _dm, _dc = _dp.rsplit(".", 1)
                    _da = getattr(importlib.import_module(_dm), _dc)(tool_registry=_get_or_init_registry(), agent_name_for_tools="DirectorAgent")
                    _dr_rows = _execute_db("SELECT script, genre FROM projects WHERE id=?", (str(project_id),))
                    _dr_row = _dr_rows[0] if _dr_rows else None
                    _dr = _da.run(action="analyze_script",
                        script_text=(_dr_row["script"] if _dr_row and "script" in _dr_row.keys() else ""),
                        genre=(_dr_row["genre"] if _dr_row and "genre" in _dr_row.keys() else "都市"),
                        project_id=str(project_id))
                    if _dr and _dr.success:
                        _save_stage_progress(pipeline_id=pid, project_id=str(project_id), stage="director", status="completed", data=_dr.data or {}, error="")
                        _dir_check = _execute_db("SELECT status, data FROM pipeline_progress WHERE project_id=? AND stage='director' ORDER BY id DESC LIMIT 1", (str(project_id),))
            except Exception as _de:
                logger.warning("[single-video] 补导演失败: %s" % _de)
        _dir_data = json.loads(_dir_check[0]["data"] or "{}") if _dir_check and _dir_check[0]["status"] == "completed" else {}

        # 项目上下文
        proj_rows = _execute_db("SELECT * FROM projects WHERE id=?", (str(project_id),))
        if not proj_rows:
            return {"success": False, "error": "项目不存在"}
        proj = dict(proj_rows[0])
        genre = proj.get("genre", "") or "都市"
        script_text = proj.get("script", "") or ""
        title = proj.get("title", "") or ""
        characters = json.loads(proj.get("characters", "[]") or "[]")

        # 分镜
        sb_rows = _execute_db("SELECT id, data FROM pipeline_progress WHERE project_id=? AND stage='storyboard' AND status='completed' ORDER BY id DESC LIMIT 1", (str(project_id),))
        if not sb_rows:
            return {"success": False, "error": "尚未生成分镜"}
        sb_data = json.loads(sb_rows[0]["data"] or "{}")
        all_shots = sb_data.get("shots", [])
        if shot_index < 0 or shot_index >= len(all_shots):
            return {"success": False, "error": "镜头索引无效"}
        current_shot = all_shots[shot_index]
        if shot_data.get("description"):
            current_shot["description"] = shot_data["description"]
        if shot_data.get("scene_image"):
            current_shot["scene_image"] = shot_data["scene_image"]
            current_shot["image_url"] = shot_data.get("image_url", shot_data["scene_image"])

        # 统一注入 scene_image + character_image + character_images + tts_audio
        # 用单元素列表传入，保持与 step 端点同一套规则
        from agents.shot_media import inject_shot_media
        _single = [current_shot]
        inject_shot_media(_single, str(project_id), include_tts=True)
        current_shot = _single[0]
        logger.info("[single-video] 统一注入完成 shot %d" % shot_index)

        # 标记 running
        _save_stage_progress(pipeline_id=pid, project_id=str(project_id), stage="video", status="running", data={"generating_shot": shot_index}, error="")

        # 后台线程生成
        from agents.agent_video import VideoAgent
        from routers.agents import _get_or_init_registry
        agent = VideoAgent(tool_registry=_get_or_init_registry(), agent_name_for_tools="VideoAgent")

        _ctx = dict(shot=current_shot, pid=pid, proj_id=str(project_id), idx=shot_index,
            sb=sb_data, sb_id=sb_rows[0]["id"], shots=all_shots, genre=genre, script=script_text,
            chars=characters, da=_dir_data.get("analysis", {}), dt=_dir_data.get("tasks", {}), title=title)

        def _bg():
            try:
                r = agent.run(action="generate", shots=[_ctx["shot"]], genre=_ctx["genre"],
                    script_text=_ctx["script"], characters=_ctx["chars"],
                    director_analysis=_ctx["da"], director_tasks=_ctx["dt"],
                    title=_ctx["title"], pipeline_id=_ctx["pid"], project_id=_ctx["proj_id"])
                vu = _extract_video_url(r.data) if r and r.success else ""
                if vu:
                    _ctx["shot"]["video_url"] = vu
                    _ctx["shots"][_ctx["idx"]] = _ctx["shot"]
                    _execute_db("UPDATE pipeline_progress SET data=? WHERE id=?", (json.dumps(dict(_ctx["sb"], shots=_ctx["shots"]), ensure_ascii=False), _ctx["sb_id"]))
                    now = time.strftime("%Y-%m-%dT%H:%M:%S")
                    _ex = _execute_db("SELECT data FROM pipeline_progress WHERE project_id=? AND stage='video' ORDER BY id DESC LIMIT 1", (_ctx["proj_id"],))
                    _ev = json.loads(_ex[0]["data"] or "{}").get("videos", []) if _ex else []
                    _ev = [v for v in _ev if v.get("shot_index") != _ctx["idx"]]
                    _ev.append({"shot_index": _ctx["idx"], "result": {"video_url": vu}})
                    _execute_db("INSERT INTO pipeline_progress (project_id,pipeline_id,stage,status,data,started_at,finished_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(project_id,pipeline_id,stage) DO UPDATE SET status='completed',data=excluded.data,finished_at=excluded.finished_at", (_ctx["proj_id"], _ctx["pid"], "video", "completed", json.dumps({"videos": _ev}, ensure_ascii=False), now, now))
                    logger.info("[single-video] 后台成功 shot=%d" % _ctx["idx"])
                else:
                    _err = r.error if r else "无返回"
                    logger.error("[single-video] 后台失败 shot=%d: %s" % (_ctx["idx"], _err))
                    _save_stage_progress(pipeline_id=_ctx["pid"], project_id=_ctx["proj_id"], stage="video", status="failed", data={}, error=_err[:150])
            except Exception as _be:
                logger.error("[single-video] 后台异常: %s" % _be)
                _save_stage_progress(pipeline_id=_ctx["pid"], project_id=_ctx["proj_id"], stage="video", status="failed", data={}, error=str(_be)[:150])

        _executor.submit(_bg)
        return {"success": True, "data": {"status": "running", "shot_index": shot_index}, "message": "视频生成中"}

    except Exception as e:
        logger.error("[single-video] 异常: %s" % e)
        return {"success": False, "error": str(e)[:200]}
