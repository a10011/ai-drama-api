```python
"""pipeline.py — 生产级重写版本"""
import os
import time
import json
import logging
import sqlite3
import concurrent.futures
import threading
import asyncio
import re
import glob
import traceback
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from services.pipeline_cache import save_stage_result
from utils.auth_util import get_user_id
from db import fetchone, execute

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/pipeline", tags=["短剧流水线"])

# ─── 常量 ──────────────────────────────────────────────
DB_DIR = "/www/wwwroot/api.mzsh.top/data"
DB_PATH = os.path.join(DB_DIR, "projects.db")
AGENT_BASE_URL = "http://127.0.0.1:8000/api/v1/agents/execute"
HTTP_TIMEOUT = 1800.0
MAX_WORKERS = 16
MAX_RETRIES = 3
RETRY_BACKOFF = 2

os.makedirs(DB_DIR, exist_ok=True)

# ─── 枚举 ──────────────────────────────────────────────
class StageStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

class PipelineStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

# ─── 数据类 ────────────────────────────────────────────
@dataclass
class StageResult:
    success: bool = False
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration: float = 0.0

@dataclass
class PipelineRecord:
    id: str
    project_id: str
    script_text: str = ""
    genre: str = ""
    status: PipelineStatus = PipelineStatus.PENDING
    progress: int = 0
    total_stages: int = 10
    current_stage: str = ""
    error: str = ""
    step_results: Dict[str, Any] = field(default_factory=dict)
    stage_outputs: Dict[str, Any] = field(default_factory=dict)
    created: float = 0.0
    updated: float = 0.0
    user_id: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PipelineRecord":
        if row is None:
            return cls(id="", project_id="")
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            script_text=row.get("script_text", ""),
            genre=row.get("genre", ""),
            status=PipelineStatus(row.get("status", "pending")),
            progress=row.get("progress", 0),
            total_stages=row.get("total_stages", 10),
            current_stage=row.get("current_stage", ""),
            error=row.get("error", ""),
            step_results=json.loads(row.get("step_results", "{}") or "{}"),
            stage_outputs=json.loads(row.get("stage_outputs", "{}") or "{}"),
            created=row.get("created", 0.0),
            updated=row.get("updated", 0.0),
            user_id=row.get("user_id", ""),
        )

@dataclass
class AgentConfig:
    agent_id: str
    action: str

@dataclass
class StageConfig:
    key: str
    label: str
    icon: str
    agent: AgentConfig
    dependencies: List[str] = field(default_factory=list)
    timeout: int = 300
    retries: int = 2

# ─── 配置 ──────────────────────────────────────────────
STAGE_CONFIGS: Dict[str, StageConfig] = {
    "导演分析": StageConfig(
        key="director_result", label="导演分析", icon="🎬",
        agent=AgentConfig("director", "analyze_script"),
        timeout=120, retries=2
    ),
    "剧本创作": StageConfig(
        key="script_result", label="剧本创作", icon="📝",
        agent=AgentConfig("script", "create"),
        dependencies=["director_result"],
        timeout=300, retries=2
    ),
    "角色设计": StageConfig(
        key="character_result", label="角色设计", icon="🎭",
        agent=AgentConfig("character", "extract"),
        dependencies=["script_result"],
        timeout=300, retries=2
    ),
    "分镜生成": StageConfig(
        key="storyboard_result", label="分镜生成", icon="🎞️",
        agent=AgentConfig("storyboard", "generate"),
        dependencies=["script_result", "director_result"],
        timeout=300, retries=2
    ),
    "场景生成": StageConfig(
        key="scene_result", label="场景生成", icon="🏔️",
        agent=AgentConfig("scene", "batch_generate"),
        dependencies=["storyboard_result", "character_result"],
        timeout=180, retries=2
    ),
    "配音合成": StageConfig(
        key="tts_result", label="配音合成", icon="🎙️",
        agent=AgentConfig("tts", "generate"),
        dependencies=["storyboard_result", "character_result"],
        timeout=300, retries=2
    ),
    "字幕生成": StageConfig(
        key="subtitle_result", label="字幕生成", icon="💬",
        agent=AgentConfig("subtitle", "generate"),
        dependencies=["tts_result"],
        timeout=60, retries=2
    ),
    "BGM配乐": StageConfig(
        key="bgm_result", label="BGM配乐", icon="🎵",
        agent=AgentConfig("bgm", "match"),
        dependencies=["script_result"],
        timeout=120, retries=2
    ),
    "视频生成": StageConfig(
        key="video_result", label="视频生成", icon="🎬",
        agent=AgentConfig("video", "generate"),
        dependencies=["scene_result", "character_result", "storyboard_result", "tts_result"],
        timeout=1800, retries=1
    ),
    "视频合成": StageConfig(
        key="composite_result", label="视频合成", icon="📺",
        agent=AgentConfig("composite", "composite"),
        dependencies=["scene_result", "character_result", "video_result", "tts_result", "subtitle_result", "bgm_result"],
        timeout=300, retries=1
    ),
}

GENRE_MOOD_MAP: Dict[str, str] = {
    "古装": "典雅", "都市": "中性", "仙侠": "空灵", "悬疑": "紧张",
    "喜剧": "欢快", "甜宠": "温馨", "科幻": "未来感", "奇幻": "梦幻"
}

STAGE_ORDER = [
    "导演分析", "剧本创作", "角色设计", "分镜生成",
    "场景生成", "配音合成", "字幕生成", "BGM配乐",
    "视频生成", "视频合成"
]

# ─── 全局资源 ──────────────────────────────────────────
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS)
_http_session: Optional[httpx.Client] = None
_db_lock = threading.Lock()

def get_http_session() -> httpx.Client:
    global _http_session
    if _http_session is None:
        _http_session = httpx.Client(timeout=httpx.Timeout(HTTP_TIMEOUT), limits=httpx.Limits(max_keepalive_connections=10, max_connections=50))
    return _http_session

# ─── 数据库工具 ────────────────────────────────────────
def _init_db():
    try:
        with sqlite3.connect(DB_PATH, timeout=10) as conn:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS pipelines (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    script_text TEXT,
                    genre TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    progress INTEGER DEFAULT 0,
                    total_stages INTEGER DEFAULT 10,
                    current_stage TEXT DEFAULT '',
                    error TEXT DEFAULT '',
                    step_results TEXT DEFAULT '{}',
                    stage_outputs TEXT DEFAULT '{}',
                    created REAL DEFAULT 0,
                    updated REAL DEFAULT 0,
                    user_id TEXT DEFAULT ''
                )
            """)
            c.execute("PRAGMA table_info(projects)")
            cols = [row[1] for row in c.fetchall()]
            if "pipeline_steps" not in cols:
                c.execute("ALTER TABLE projects ADD COLUMN pipeline_steps TEXT DEFAULT '[]'")
            if "progress" not in cols:
                c.execute("ALTER TABLE projects ADD COLUMN progress INTEGER DEFAULT 0")
            conn.commit()
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        raise

_init_db()

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
        logger.error(f"数据库操作失败: {query[:50]}... 错误: {e}")
        raise

# ─── Pydantic 模型 ─────────────────────────────────────
class PipelineStart(BaseModel):
    project_id: str = "default"
    script_text: str = ""
    genre: str = ""
    title: str = ""
    characters: List[Dict[str, Any]] = []
    max_shots: int = 8
    resume_pipeline_id: str = ""

    @field_validator("project_id", mode="before")
    @classmethod
    def coerce_project_id(cls, v):
        return str(v) if v is not None else "default"

class StartResponse(BaseModel):
    success: bool = True
    pipeline_id: str = ""
    project_id: str = ""
    message: str = ""

# ─── 核心逻辑 ──────────────────────────────────────────
def _get_director_task(director_result: Dict[str, Any], task_key: str) -> str:
    try:
        if not director_result:
            return ""
        data = director_result.get("data", {})
        if isinstance(data, dict):
            tasks = data.get("tasks", {})
            task = tasks.get(task_key, "")
            return str(task)[:800]
    except Exception as e:
        logger.warning(f"获取导演任务失败: {e}")
    return ""

def _build_params(stage: str, genre: str, script_text: str = "", **stage_data) -> Dict[str, Any]:
    try:
        if stage == "导演分析":
            script_result = stage_data.get("script_result", {}).get("data", {})
            script_content = script_result.get("script", script_result.get("outline", script_text[:4000]))
            if isinstance(script_content, str) and len(script_content) > 0:
                return {
                    "script": script_content[:4000],
                    "title": script_result.get("title", ""),
                    "characters": script_result.get("characters", [])[:10],
                    "scenes": script_result.get("scenes", [])[:5],
                    "beat_analysis": "请分析剧本节拍(beats)，标注每个节拍的importance(high/medium/low)。high=关键剧情需要场景图+配音; medium=常规推进只需配音+文生视频; low=空镜转场直接文生视频。high占20-30%, medium占50-60%, low占15-25%。"
                }
            return {
                "script": script_text[:4000],
                "beat_analysis": "请分析剧本节拍(beats)，标注每个节拍的importance(high/medium/low)。high=关键剧情需要场景图+配音; medium=常规推进只需配音+文生视频; low=空镜转场直接文生视频。"
            }
        elif stage == "剧本创作":
            polish_only = stage_data.get("polish_only", False)
            user_script = stage_data.get("user_script", "")
            user_title = stage_data.get("user_title", "")
            if polish_only and user_script and len(str(user_script).strip()) > 50:
                return {"script_text": str(user_script)[:8000], "genre": genre or "都市", "title": user_title, "polish_only": True}
            director_notes = stage_data.get("director_result", {}).get("data", {}).get("emotion_summary", "")
            enhanced = (str(script_text) or str(user_script))[:200]
            if director_notes:
                enhanced += f"\n\n【导演分析参考】\n{director_notes[:1000]}"
            return {"premise": enhanced, "genre": genre or "都市"}
        elif stage == "角色设计":
            script = stage_data.get("script_result", {}).get("data", {}).get("script", script_text)
            director_task = _get_director_task(stage_data.get("director_result", {}), "character_design")
            refined_script = stage_data.get("director_result", {}).get("data", {}).get("refined_script", {})
            refined_chars = refined_script.get("characters", []) if isinstance(refined_script, dict) else []
            return {"script_text": str(script)[:4000], "genre": genre or "都市", "director_task": director_task, "refined_characters": refined_chars}
        elif stage == "分镜生成":
            script = stage_data.get("script_result", {}).get("data", {}).get("script", script_text)
            chars = stage_data.get("character_result", {}).get("data", {}).get("characters", [])
            director_task = _get_director_task(stage_data.get("director_result", {}), "storyboard_generation")
            refined_script = stage_data.get("director_result", {}).get("data", {}).get("refined_script", {})
            if isinstance(refined_script, dict):
                refined_chars = refined_script.get("characters", [])
                refined_scenes = refined_script.get("scenes", [])
            else:
                refined_chars, refined_scenes = [], []
            director_data = stage_data.get("director_result", {}).get("data", {})
            beats = director_data.get("beats", director_data.get("beat_analysis", []))
            beat_notes = ""
            if beats:
                blines = []
                for b in beats[:15]:
                    bn = b.get("beat_num", b.get("number", b.get("id", "")))
                    bd = b.get("description", str(b)[:120])
                    bi = b.get("importance", b.get("weight", "medium"))
                    blines.append(f"节拍{bn}: {bd} [重要度:{bi}]")
                beat_notes = "\n导演节拍分析（用于为每个分镜分配importance等级）：\n" + "\n".join(blines)
            return {"script": str(script)[:3000], "genre": genre or "都市", "characters": chars, "director_task": director_task, "refined_characters": refined_chars, "refined_scenes": refined_scenes, "director_beats": beat_notes}
        elif stage == "场景生成":
            shots = stage_data.get("storyboard_result", {}).get("data", {}).get("shots", [])
            high_shots = [s for s in shots if s.get("importance", "medium") == "high"]
            if not high_shots:
                high_shots = [s for s in shots[:3]]
            genre_val = genre or "都市"
            director_task = _get_director_task(stage_data.get("director_result", {}), "scene_generation")
            return {"shots": high_shots, "all_shots": shots, "genre": genre_val, "director_task": director_task}
        elif stage == "配音合成":
            shots = stage_data.get("storyboard_result", {}).get("data", {}).get("shots", [])
            voiced_shots = [s for s in shots if s.get("importance", "medium") != "low"]
            if not voiced_shots:
                voiced_shots = shots[:8]
            characters = stage_data.get("character_result", {}).get("data", {}).get("characters", [])
            character_voices = {c.get("name", ""): {"voice": "longwan", "gender": c.get("gender", "女")} for c in characters}
            director_task = _get_director_task(stage_data.get("director_result", {}), "tts_voice")
            return {"shots": voiced_shots, "all_shots": shots, "character_voices": character_voices, "director_task": director_task}
        elif stage == "字幕生成":
            shots = stage_data.get("storyboard_result", {}).get("data", {}).get("shots", [])
            dialogues = []
            for s in shots:
                if s.get("importance", "medium") == "low":
                    continue
                d = s.get("dialogue", s.get("subtitle", s.get("text", "")))
                if d and str(d).strip():
                    dialogues.append(str(d).strip())
            ts = [{"start": i * 3, "end": i * 3 + 2} for i in range(len(dialogues))]
            return {"script": " ".join(dialogues)[:300], "timestamps": ts}
        elif stage == "BGM配乐":
            shots = stage_data.get("storyboard_result", {}).get("data", {}).get("shots", [])
            director_task = _get_director_task(stage_data.get("director_result", {}), "bgm_music")
            return {"mood": GENRE_MOOD_MAP.get(genre, "中性"), "scenes": [{"index": i, "name": s.get("shot_type", "场景"), "mood": s.get("mood", "中性")} for i, s in enumerate(shots[:5])], "director_task": director_task}
        elif stage == "视频生成":
            shots = stage_data.get("storyboard_result", {}).get("data", {}).get("shots", [])
            scene_raw = stage_data.get("scene_result", {})
            if isinstance(scene_raw, dict) and not scene_raw.get("success"):
                logger.warning("[stage] 场景生成未成功，跳过视频生成")
                return {"shots": [], "skip": True, "reason": "场景生成失败"}
            if isinstance(scene_raw, dict):
                scene_data = scene_raw.get("data", {})
                if isinstance(scene_data, dict) and scene_data.get("success"):
                    scene_data = scene_data.get("data", {})
            else:
                scene_data = {}
            if scene_data.get("image_map"):
                img_map = scene_data["image_map"]
                for i, s in enumerate(shots):
                    url = img_map.get(str(i), img_map.get(i, ""))
                    if url:
                        s["scene_image"] = url
                        if not s.get("scene_description") and s.get("description"):
                            s["scene_description"] = s["description"]
                        if not s.get("prompt") and s.get("description"):
                            s["prompt"] = s["description"][:200]
            elif scene_data.get("image_url"):
                for s in shots:
                    s["scene_image"] = scene_data["image_url"]
            for i, s in enumerate(shots):
                if not s.get("scene_image"):
                    color = ["0x0A1628", "0x1a1a2e", "0x16213e", "0x0f3460"][i % 4]
                    s["scene_image"] = ""
                    s["_fallback_color"] = color
            tts_raw = stage_data.get("tts_result", {})
            if isinstance(tts_raw, dict):
                tts_data = tts_raw.get("data", {})
                voice_files = tts_data.get("audio_files", []) if isinstance(tts_data, dict) else []
                for i, s in enumerate(shots):
                    for vf in voice_files:
                        if isinstance(vf, dict) and vf.get("shot_index") == i:
                            s["tts_audio"] = vf.get("audio_url", vf.get("url", ""))
                            break
            title = stage_data.get("script_result", {}).get("data", {}).get("title", "")
            characters = stage_data.get("character_result", {}).get("data", {}).get("characters", [])
            director_data = stage_data.get("director_result", {}).get("data", {})
            stack = stage_data.get("stack_locked", "")
            return {"shots": shots, "genre": genre, "title": title, "characters": characters, "director_analysis": director_data, "stack_locked": stack}
        elif stage in ("视频合成", "漫画合成", "转场合成", "导出成片"):
            shots = stage_data.get("storyboard_result", {}).get("data", {}).get("shots", [])
            tts_data = stage_data.get("tts_result", {}).get("data", {})
            bgm_data = stage_data.get("bgm_result", {}).get("data", {})
            bgm_files_from_agent = bgm_data.get("bgm_files", [])
            subtitle_data = stage_data.get("subtitle_result", {}).get("data", {})
            video_data = stage_data.get("video_result", {}).get("data", {}).get("videos", [])
            voice_files = tts_data.get("audio_files", [])
            bgm_files = bgm_data.get("bgm_tracks", bgm_data.get("files", []))
            sub_files = subtitle_data.get("subtitles", [])
            output_fn = f"/www/wwwroot/storage/videos/output_{stage_data.get('pid', 'unknown')}_{int(time.time())}.mp4"
            audio_by_shot = {a.get("shot_index", i): a.get("audio_url", "") for i, a in enumerate(voice_files)}
            clips = []
            for i, s in enumerate(shots):
                clip = {
                    "shot_index": i,
                    "desc": s.get("description", ""),
                    "subtitle": s.get("dialogue", s.get("text", "")),
                    "duration_sec": float(s.get("duration_sec", s.get("duration", 5))),
                }
                audio_url = audio_by_shot.get(i, "")
                if audio_url:
                    clip["audio"] = audio_url
                if i < len(video_data) and isinstance(video_data[i], dict):
                    vinfo = video_data[i].get("result", {})
                    if isinstance(vinfo, dict) and vinfo.get("video_url"):
                        clip["video"] = vinfo["video_url"]
                clips.append(clip)
            bgm_file = ""
            if bgm_files_from_agent:
                bgm_file = bgm_files_from_agent[0]
            elif bgm_files and isinstance(bgm_files, list):
                first_bgm = bgm_files[0]
                if isinstance(first_bgm, dict):
                    bgm_file = first_bgm.get("url", first_bgm.get("bgm_url", ""))
                elif isinstance(first_bgm, str):
                    bgm_file = first_bgm
            srt_file = ""
            if sub_files and isinstance(sub_files, list):
                if isinstance(sub_files[0], dict):
                    srt_file = sub_files[0].get("srt", sub_files[0].get("file", ""))
            if bgm_file and clips:
                clips[0]["bgm"] = bgm_file
            if srt_file:
                clips[0]["srt_file"] = srt_file
            return {"clips": clips, "shots": shots, "output_path": output_fn}
        elif stage == "文字排版":
            shots = stage_data.get("storyboard_result", {}).get("data", {}).get("shots", [])
            return {"script": " ".join([s.get("description", "") for s in shots[:3]])[:300], "style": "default"}
    except Exception as e:
        logger.error(f"构建参数失败 (stage={stage}): {e}")
    return {}

def _call_agent(stage: str, genre: str, script_text: str = "", pid: str = "", **stage_data) -> StageResult:
    try:
        config = STAGE_CONFIGS.get(stage)
        if not config:
            logger.warning(f"[Pipeline] 未找到stage '{stage}'对应的智能体配置，跳过")
            return StageResult(success=False, error=f"未找到智能体配置: {stage}")

        agent_id = config.agent.agent_id
        action = config.agent.action
        params = _build_params(stage, genre, script_text, **stage_data)
        
        if stage == "剧本创作" and params.get("script_text"):
            action = "polish"
        
        payload = {"agent_id": agent_id, "action": action, "params": params}
        logger.info(f"[Pipeline] 调用智能体: agent={agent_id} action={action} stage={stage}")

        session = get_http_session()
        start_time = time.time()
        resp = session.post(AGENT_BASE_URL, json=payload, timeout=HTTP_TIMEOUT)
        result = resp.json()
        duration = time.time() - start_time

        if result.get("success"):
            logger.info(f"[Pipeline] ✓ {stage} 完成 (耗时: {duration:.1f}s)")
            return StageResult(success=True, data=result.get("data", {}), duration=duration)
        else:
            err = result.get("error", "未知错误")
            logger.warning(f"[Pipeline] ✗ {stage} 失败: {err}")
            return StageResult(success=False, error=err, duration=duration)
    except httpx.TimeoutException:
        logger.warning(f"[Pipeline] ✗ {stage} 超时（{HTTP_TIMEOUT}s）")
        return StageResult(success=False, error=f"智能体调用超时: {stage}")
    except httpx.ConnectError:
        logger.warning(f"[Pipeline] ✗ {stage} 连接失败（agent服务可能未启动）")
        return StageResult(success=False, error=f"智能体服务未连接: {stage}")
    except Exception as e:
        logger.warning(f"[Pipeline] ✗ {stage} 异常: {e}")
        return StageResult(success=False, error=str(e)[:200])

def _call_agent_with_retry(stage: str, genre: str, script_text: str = "", pid: str = "", max_retries: int = 3, **stage_data) -> StageResult:
    last_error = ""
    for attempt in range(max_retries):
        try:
            result = _call_agent(stage, genre, script_text, pid, **stage_data)
            if result.success:
                return result
            last_error = result.error
        except Exception as e:
            last_error = str(e)
        
        if attempt < max_retries - 1:
            delay = RETRY_BACKOFF ** attempt
            logger.info(f"[DAG] {stage} 第{attempt+1}次失败({last_error})，{delay}s后重试...")
            time.sleep(delay)
    
    logger.error(f"[DAG] {stage} 失败({max_retries}次): {last_error}")
    return StageResult(success=False, error=last_error)

def _update_pipeline_progress(pid: str, progress: int, current_stage: str = "", status: PipelineStatus = PipelineStatus.RUNNING, error: str = ""):
    try:
        query = "UPDATE pipelines SET status=?, progress=?, current_stage=?, updated=?, error=? WHERE id=?"
        _execute_db(query, (status.value, progress, current_stage, time.time(), error, pid))
    except Exception as e:
        logger.warning(f"更新pipeline进度失败: {e}")

def _update_project_step(project_id: str, stage_label: str, progress: int, status: str = "busy", log: str = ""):
    try:
        rows = _execute_db("SELECT pipeline_steps FROM projects WHERE id=?", (project_id,))
        if rows:
            steps = json.loads(rows[0][0] or "[]")
            idx = next((j for j, s in enumerate(steps) if s.get("label") == stage_label), None)
            if idx is not None:
                steps[idx]["status"] = status
                steps[idx]["progress"] = progress
                if log:
                    steps[idx]["log"] = log
            else:
                icon = STAGE_CONFIGS.get(stage_label, StageConfig(key="", label=stage_label, icon="❓", agent=AgentConfig("", ""))).icon
                steps.append({"icon": icon, "label": stage_label, "desc": "", "status": status, "progress": progress, "duration": "", "log": log or f"{stage_label}..."})
            _execute_db("UPDATE projects SET pipeline_steps=?, progress=? WHERE id=?", 
                       (json.dumps(steps, ensure_ascii=False), max(progress, int(steps[-1].get("progress", 0)) if steps else progress), project_id))
    except Exception as e:
        logger.warning(f"更新项目步骤失败: {e}")

def _save_step_results(pid: str, stage_results: Dict[str, Any], project_id: str = ""):
    try:
        # Backfill scene images to storyboard shots
        sb_data = stage_results.get("storyboard_result", {}).get("data", {})
        sc_data = stage_results.get("scene_result", {}).get("data", {})
        if isinstance(sb_data, dict) and isinstance(sc_data, dict):
            shots = sb_data.get("shots", [])
            scene_imgs = sc_data.get("images", []) or []
            img_map = sc_data.get("image_map", {}) or {}
            if shots and (scene_imgs or img_map):
                for shot in shots:
                    if isinstance(shot, dict) and not shot.get("image_url"):
                        shot_num = shot.get("shot_num", shot.get("shot_index", -1))
                        for img in scene_imgs:
                            if isinstance(img, dict):
                                si = img.get("shot_index", -1)
                                if si == shot_num - 1 or si == shot_num:
                                    shot["image_url"] = img.get("image_url", img.get("url", ""))
                                    break
                        if not shot.get("image_url"):
                            for k in [str(shot_num - 1), str(shot_num)]:
                                if k in img_map:
                                    shot["image_url"] = img_map[k]
                                    break
        
        _execute_db("UPDATE pipelines SET step_results=? WHERE id=?", 
                   (json.dumps(stage_results, ensure_ascii=False, default=str), pid))
    except Exception as e:
        logger.warning(f"保存阶段结果失败: {e}")

def _run_pipeline(pid: str, project_id: str, genre: str, characters: List[Dict], max_shots: int, script_text: str = "", resume_from: Optional[Dict] = None, stack_locked: str = ""):
    stage_results: Dict[str, StageResult] = {}
    resume_stages_completed: set = set()
    
    if resume_from:
        for k, v in resume_from.items():
            if isinstance(v, dict) and v.get("success"):
                stage_results[k] = StageResult(success=True, data=v.get("data", {}))
                resume_stages_completed.add(k)
        logger.info(f"[DAG] 续传模式: 已完成 {len(resume_stages_completed)} 阶段: {resume_stages_completed}")
    
    def is_stage_done(key: str) -> bool:
        if key not in stage_results:
            return False
        val = stage_results[key]
        return val.success and bool(val.data)
    
    def run_stage(stage: str, **extra_kw) -> StageResult:
        if is_stage_done(STAGE_CONFIGS[stage].key):
            logger.info(f"[DAG] 跳过已完成阶段: {stage}")
            return stage_results[STAGE_CONFIGS[stage].key]
        
        config = STAGE_CONFIGS[stage]
        _update_pipeline_progress(pid, 0, stage)
        _update_project_step(project_id, stage, 20)
        logger.info(f"[DAG] 阶段: {stage} 开始")
        
        result = _call_agent_with_retry(stage, genre, script_text, pid, max_retries=config.retries, **extra_kw)
        stage_results[config.key] = result
        _save_step_results(pid, stage_results, project_id)
        
        if result.success:
            _update_project_step(project_id, stage, 100, "done", f"{stage}完成")
        else:
            _update_project_step(project_id, stage, 100, "error", f"{stage}失败: {result.error[:100]}")
        
        return result
    
    try:
        # 阶段1: 导演分析
        run_stage("导演分析")
        _update_pipeline_progress(pid, 10)
        
        # 阶段2: 剧本创作
        if script_text and len(str(script_text).strip()) > 0:
            try:
                rows = _execute_db("SELECT title FROM projects WHERE id=?", (project_id,))
                project_title = rows[0][0] if rows else ""
            except:
                project_title = ""
            
            try:
                session = get_http_session()
                resp = session.post(AGENT_BASE_URL, json={
                    "agent_id": "script", "action": "polish",
                    "params": {"script_text": script_text, "title": project_title, "genre": genre or ""}
                }, timeout=300)
                rdata = resp.json()
                r = rdata.get("data", {}) if isinstance(rdata, dict) else {}
                if r and r.get("script"):
                    result = StageResult(success=True, data=r)
                else:
                    raise ValueError("polish返回空结果")
            except Exception as e:
                logger.warning(f"[DAG] polish异常: {e}，直接使用原始剧本")
                result = StageResult(success=True, data={"script": script_text, "outline": script_text[:300], "title": project_title, "characters": [], "scenes": []})
            
            stage_results["script_result"] = result
            _save_step_results(pid, stage_results, project_id)
        else:
            run_stage("剧本创作", director_result=stage_results.get("director_result", StageResult()).data)
        
        _update_pipeline_progress(pid, 15)
        
        # 阶段3: 角色设计 + 分镜生成（并行）
        _update_pipeline_progress(pid, 15, "角色设计")
        _update_pipeline_progress(pid, 15, "分镜生成")
        
        futures = {}
        if not is_stage_done("character_result"):
            futures["character"] = _executor.submit(run_stage, "角色设计", 
                script_result=stage_results.get("script_result", StageResult()).data,
                director_result=stage_results.get("director_result", StageResult()).data)
        
        if not is_stage_done("storyboard_result"):
            futures["storyboard"] = _executor.submit(run_stage, "分镜生成",
                script_result=stage_results.get("script_result", StageResult()).data,
                director_result=stage_results.get("director_result", StageResult()).data)
        
        for name, fut in futures.items():
            try:
                fut.result(timeout=360)
            except Exception as e:
                logger.warning(f"[DAG] 阶段3.{name} 失败: {e}")
        
        _update_pipeline_progress(pid, 25)
        
        # 角色肖像生成（后台）
        try:
            _executor.submit(_gen_char_portraits, stage_results, characters, project_id, genre)
        except:
            pass
        
        # 阶段4: 场景生成 + 配音合成 + BGM配乐（并行）
        _update_pipeline_progress(pid, 25, "场景生成")
        _update_pipeline_progress(pid, 25, "配音合成")
        _update_pipeline_progress(pid, 25, "BGM配乐")
        
        futures = {}
        for stage in ["场景生成", "配音合成", "BGM配乐"]:
            if not is_stage_done(STAGE_CONFIGS[stage].key):
                futures[stage] = _executor.submit(run_stage, stage,
                    storyboard_result=stage_results.get("storyboard_result", StageResult()).data,
                    character_result=stage_results.get("character_result", StageResult()).data,
                    script_result=stage_results.get("script_result", StageResult()).data)
        
        for name, fut in futures.items():
            try:
                fut.result(timeout=360)
            except Exception as e:
                logger.warning(f"[DAG] 阶段4.{name} 失败: {e}")
        
        _update_pipeline_progress(pid, 40)
        
        # 阶段5: 字幕生成 + 视频生成（并行）
        _update_pipeline_progress(pid, 40, "字幕生成")
        _update_pipeline_progress(pid, 40, "视频生成")
        
        futures = {}
        if not is_stage_done("subtitle_result"):
            futures["subtitle"] = _executor.submit(run_stage, "字幕生成",
                tts_result=stage_results.get("tts_result", StageResult()).data)
        
        if not is_stage_done("video_result"):
            futures["video"] = _executor.submit(run_stage, "视频生成",
                scene_result=stage_results.get("scene_result", StageResult()).data,
                character_result=stage_results.get("character_result", StageResult()).data,
                storyboard_result=stage_results.get("storyboard_result", StageResult()).data,
                tts_result=stage_results.get("tts_result", StageResult()).data)
        
        for name, fut in futures.items():
            timeout = 1800 if name == "video" else 660
            try:
                fut.result(timeout=timeout)
            except Exception as e:
                logger.warning(f"[DAG] 阶段5.{name} 失败: {e}")
        
        _update_pipeline_progress(pid, 70)
        
        # 阶段6: 视频合成
        _update_pipeline_progress(pid, 70, "视频合成")
        result = run_stage("视频合成",
            scene_result=stage_results.get("scene_result", StageResult()).data,
            character_result=stage_results.get("character_result", StageResult()).data,
            video_result=stage_results.get("video_result", StageResult()).data,
            tts_result=stage_results.get("tts_result", StageResult()).data,
            subtitle_result=stage_results.get("subtitle_result", StageResult()).data,
            bgm_result=stage_results.get("bgm_result", StageResult()).data)
        
        if result.success:
            comp_data = result.data
            comp_output = comp_data.get("output_path", "")
            if comp_output and not comp_data.get("video_url"):
                fname = os.path.basename(comp_output)
                subdir = "videos" if "videos" in comp_output else ""
                web_url = f"https://ai.mzsh.top/storage/{subdir}/{fname}" if subdir else f"https://ai.mzsh.top/storage/{fname}"
                comp_data["video_url"] = web_url
                stage_results["composite_result"] = StageResult(success=True, data=comp_data)
                _save_step_results(pid, stage_results, project_id)
        
        _update_pipeline_progress(pid, 100, status=PipelineStatus.COMPLETED)
        
    except Exception as e:
        logger.error(f"[DAG Pipeline] 整体失败: {e}\n{traceback.format_exc()}")
        _update_pipeline_progress(pid, 0, status=PipelineStatus.FAILED, error=str(e)[:200])

def _gen_char_portraits(stage_results: Dict[str, StageResult], characters: List[Dict], project_id: str, genre: str):
    try:
        user_chars = list(characters or [])
        try:
            rows = _execute_db("SELECT characters FROM projects WHERE id=?", (project_id,))
            if rows and rows[0][0]:
                db_chars = json.loads(rows[0][0]) or []
                db_names = {str(c.get("name", "")).strip() for c in user_chars}
                for dc in db_chars:
                    dn = str(dc.get("name", "")).strip()
                    if dn and dn not in db_names:
                        user_chars.append(dc)
                        db_names.add(dn)
        except:
            pass
        
        char_result = stage_results.get("character_result", StageResult())
        llm_chars = char_result.data.get("characters", [])
        
        merged = list(user_chars)
        merged_names = {str(c.get("name", "")).strip() for c in merged}
        for lc in llm_chars:
            ln = str(lc.get("name", "")).strip()
            if ln and ln not in merged_names:
                merged.append(lc)
                merged_names.add(ln)
        
        if not merged:
            merged = llm_chars
        
        logger.info(f"[DAG] 角色肖像: 用户{len(user_chars)}个+LLM{len(llm_chars)}个→合并{len(merged)}个")
        
        char_images = {}
        session = get_http_session()
        
        for ci, ch in enumerate(merged[:6]):
            char_name = ch.get("name", f"角色{ci+1}")
            user_photo = str(ch.get("photo", "") or "").strip()
            has_photo = user_photo and (user_photo.startswith("http") or user_photo.startswith("/storage"))
            gen = ch.get("gender", "女")
            age = ch.get("age", "青年")
            
            desc_parts = []
            for k in ["personality", "appearance", "role_type", "trait", "description", "look", "character"]:
                v = ch.get(k, "")
                if v and len(str(v).strip()) > 1:
                    desc_parts.append(str(v).strip())
            person_desc = "，".join(desc_parts[:3]) if desc_parts else f"{gen}性{age}"
            
            genre_str = genre or ""
            style_hints = {"武侠": "古装武侠，佩剑，古风", "仙侠": "修仙古装，飘逸，仙气", "古装": "古装汉服"}
            style_hint = ""
            for gk, gv in style_hints.items():
                if gk in genre_str:
                    style_hint = "，" + gv
                    break
            
            prompt = f"一位中国真人，{person_desc}，{gen}性，{age}，面部特写肖像，肩膀以上构图，脸部占据画面主体，正脸直视镜头{style_hint}，电影级光影，皮肤质感真实细腻，8K超高清，真人照片，不是卡通不是动漫不是3D | photorealistic Chinese person, close-up face portrait, head and shoulders framing, face fills the composition, looking directly at camera, studio lighting, 8K, real human photo, NOT cartoon NOT anime NOT 3D"
            
            logger.info(f"[DAG] 角色肖像[{ci+1}/{len(merged[:6])}]: {char_name} {'(图生图)' if has_photo else '(文生图)'}")
            
            img_url = ""
            for attempt in range(3):
                try:
                    if has_photo:
                        r2 = session.post(AGENT_BASE_URL, json={
                            "agent_id": "character", "action": "generate_figure",
                            "params": {"character": ch, "genre": genre_str, "reference_image": user_photo, "prompt_hint": prompt}
                        }, timeout=150)
                    else:
                        r2 = session.post(AGENT_BASE_URL, json={
                            "agent_id": "scene", "action": "generate_image",
                            "params": {"scene_prompt": prompt}
                        }, timeout=120)
                    img_data = r2.json().get("data", {})
                    img_url = img_data.get("image_url", img_data.get("url", img_data.get("portrait_url", "")))
                    if img_url:
                        char_images[char_name] = img_url
                        logger.info(f"[DAG] 角色{char_name}肖像 OK (尝试{attempt+1})")
                        break
                    else:
                        logger.warning(f"[DAG] 角色{char_name}第{attempt+1}次返回空URL")
                except Exception as e2:
                    logger.warning(f"[DAG] 角色{char_name}第{attempt+1}次失败: {e2}")
            
            if not img_url:
                logger.error(f"[DAG] 角色{char_name}肖像全部3次尝试均失败")
        
        if char_images:
            if stage_results.get("character_result", StageResult()).data:
                stage_results["character_result"].data["char_images"] = char_images
            
            try:
                existing_chars = char_result.data.get("characters", [])
                if existing_chars:
                    updated = []
                    for ch in existing_chars:
                        ch_cp = dict(ch)
                        cn = ch.get("name", "")
                        if cn in char_images:
                            ch_cp["portrait_url"] = char_images[cn]
                        updated.append(ch_cp)
                    _execute_db("UPDATE projects SET characters=? WHERE id=?", 
                               (json.dumps(updated, ensure_ascii=False), project_id))
            except Exception as e_db:
                logger.warning(f"[DAG] 保存角色肖像DB失败: {e_db}")
    except Exception as e:
        logger.warning(f"[DAG] 角色肖像全部失败: {e}")

# ─── API 端点 ──────────────────────────────────────────
@router.post("/start")
async def start_pipeline(body: PipelineStart, request: Request):
    try:
        user_id = get_user_id(request)
        resume_data = None
        
        if body.resume_pipeline_id:
            try:
                rows = _execute_db("SELECT step_results, script_text, genre, project_id FROM pipelines WHERE id=?", (body.resume_pipeline_id,))
                if rows:
                    old_row = rows[0]
                    sr_raw = old_row[0] or "{}"
                    resume_data = json.loads(sr_raw) if isinstance(sr_raw, str) else sr_raw
                    logger.info(f"[Pipeline] 续传: 加载 {body.resume_pipeline_id} 的 {len(resume_data)} 个阶段结果")
                    if not body.script_text and old_row[1]:
                        body.script_text = old_row[1]
                    if not body.genre and old_row[2]:
                        body.genre = old_row[2]
            except Exception as e:
                logger.warning(f"[Pipeline] 加载续传数据失败: {e}")
        
        if (not body.genre or body.genre.strip() == "") and body.title.strip():
            hint = body.title.strip()
            for kw in ["古装", "古代", "仙侠", "武侠", "宫斗", "玄幻"]:
                if kw in hint:
                    body.genre = kw
                    logger.info(f"[Pipeline] 从标题推导流派: {hint} → {kw}")
                    break
        
        pid = f"pipe_{int(time.time())}_{body.project_id}"
        
        # 创建 pipeline 记录
        _execute_db(
            "INSERT INTO pipelines (id, project_id, script_text, genre, status, created, updated, user_id) VALUES (?,?,?,?,'pending',?,?,?)",
            (pid, body.project_id, body.script_text[:1000], body.genre, time.time(), time.time(), user_id)
        )
        
        # 获取或创建项目
        rows = _execute_db("SELECT id, title FROM projects WHERE title=? OR id=?", (body.project_id, body.project_id))
        
        # 读取项目模型栈锁定
        stack_locked = ""
        try:
            rows2 = _execute_db("SELECT stack_locked, preferred_video_stack FROM projects WHERE id=?", (str(project_db_id),))
            if rows2:
                stack_locked = rows2[0][0] or ""
                if stack_locked:
                    logger.info(f"[Pipeline] 继承模型栈: {stack_locked}")
        except:
            pass
        
        if not rows:
            all_stages = STAGE_ORDER
            steps = [{"icon": STAGE_CONFIGS.get(s, StageConfig(key="", label=s, icon="", agent=AgentConfig("", ""))).icon, 
                      "label": s, "desc": "", "status": "idle", "progress": 0, "duration": "", "log": ""} for s in all_stages]
            _execute_db(
                "INSERT INTO projects (title, script, pipeline_steps, progress, status, user_id) VALUES (?,?,?,0,'active',?)",
                (body.title or body.genre or "短剧", body.script_text[:500], json.dumps(steps, ensure_ascii=False), user_id)
            )
            project_db_id = _execute_db("SELECT last_insert_rowid()")[0][0]
        else:
            project_db_id = rows[0]["id"]
        
        # 自动复用旧管线结果
        if not resume_data and project_db_id:
            try:
                prev_rows = _execute_db(
                    "SELECT id, step_results FROM pipelines WHERE project_id=? AND status='completed' ORDER BY created DESC LIMIT 1",
                    (str(project_db_id),)
                )
                if prev_rows:
                    prev_data = json.loads(prev_rows[0][1]) if isinstance(prev_rows[0][1], str) else (prev_rows[0][1] or {})
                    resume_data = {}
                    for k in ["character_result", "scene_result", "tts_result", "bgm_result"]:
                        if k in prev_data and prev_data[k]:
                            resume_data[k] = prev_data[k]
                    if resume_data:
                        logger.info("[Pipeline] 自动复用旧管线结果: " + str(list(resume_data.keys())))
            except Exception as e:
                logger.warning("[Pipeline] 自动复用失败: " + str(e))
        
        # 启动后台任务
        def wrapped_run():
            try:
                _run_pipeline(pid, str(project_db_id), body.genre or body.title, body.characters, body.max_shots, body.script_text, resume_from=resume_data, stack_locked=stack_locked)
            except Exception as exc:
                logger.error(f"[Pipeline] _run_pipeline crashed: {exc}\n{traceback.format_exc()}")
                try:
                    _execute_db("UPDATE pipelines SET status='failed', error=?, updated=? WHERE id=?", (str(exc)[:500], time.time(), pid))
                except:
                    pass
        
        _executor.submit(wrapped_run)
        
        return StartResponse(success=True, pipeline_id=pid, project_id=str(project_db_id), message="流水线已启动（后台运行）")
    
    except Exception as e:
        logger.error(f"[Pipeline] 启动失败: {e}\n{traceback.format_exc()}")
        return StartResponse(success=False, message=f"启动失败: {str(e)[:200]}")

def _format_stages(step_results: Dict[str, Any], current_stage: str) -> List[Dict]:
    stages = []
    for key, config in STAGE_CONFIGS.items():
        data = step_results.get(config.key, {}) or {}
        done = bool(data and data != {})
        is_current = key == current_stage
        stages.append({
            "key": config.key,
            "label": config.label,
            "icon": config.icon,
            "done": done,
            "current": is_current,
            "data": data
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
        
        row = rows[0]
        record = PipelineRecord.from_row(row)
        step_results = record.step_results
        
        # 查找视频URL
        video_url = ""
        try:
            safe_id = pipeline_id.replace(":", "_").replace("/", "_")
            patterns = [
                f"/www/wwwroot/storage/videos/*_{safe_id}.mp4",
                f"/www/wwwroot/storage/videos/*_{safe_id[:20]}*.mp4",
                f"/www/wwwroot/storage/*_{safe_id}.mp4",
                f"/www/wwwroot/storage/*_{safe_id[:20]}*.mp4",
            ]
            matches = []
            for pat in patterns:
                matches += glob.glob(pat)
            if matches:
                fname = os.path.basename(matches[0])
                subdir = os.path.basename(os.path.dirname(matches[0]))
                if subdir and subdir != 'storage':
                    video_url = f"https://ai.mzsh.top/storage/{subdir}/{fname}"
                else:
                    video_url = f"https://ai.mzsh.top/storage/{fname}"
        except Exception as e:
            logger.warning(f"查找视频文件失败: {e}")
        
        if not video_url:
            try:
                for key in ['composite_result', 'video_result']:
                    sr = step_results.get(key, {})
                    if isinstance(sr, dict):
                        inner = sr.get("data", sr)
                        url = inner.get("video_url", "") or inner.get("output", "") or inner.get("output_path", "") or inner.get("result_url", "")
                        if url:
                            if url.startswith('/www/wwwroot/'):
                                url = url.replace('/www/wwwroot', 'https://ai.mzsh.top')
                            video_url = url
                            break
            except:
                pass
        
        # 注册到媒体库
        if video_url and video_url.startswith("https://ai.mzsh.top/storage/"):
            try:
                local_path = video_url.replace("https://ai.mzsh.top", "/www/wwwroot")
                if os.path.exists(local_path):
                    from routers.media_router import save_and_register
                    fname = os.path.basename(local_path)
                    with open(local_path, "rb") as vf:
                        vdata = vf.read()
                    save_and_register(vdata, fname, "videos", fname.replace(".mp4", ""),
                        record.project_id, pipeline_id, record.user_id)
            except Exception as e:
                logger.warning(f"注册媒体失败: {e}")
        
        # 构建消息
        status_val = record.status.value if isinstance(record.status, PipelineStatus) else record.status
        if status_val == "completed":
            message = "短剧生成完成！"
        elif status_val == "failed":
            message = "生成失败：" + str(record.error)[:100]
        elif status_val == "running":
            message = f"正在生成中... {record.progress}%"
        elif status_val == "pending":
            message = "排队等待中..."
        else:
            message = f"状态：{status_val or '未知'}"
        
        return {
            "success": True,
            "data": {
                "progress": record.progress if isinstance(record.progress, (int, float)) else 0,
                "status": status_val,
                "message": message,
                "video_url": video_url,
                "current_stage": record.current_stage,
                "total_stages": record.total_stages,
                "shots": _format_stages(step_results, record.current_stage),
                "error": record.error,
            }
        }
    except Exception as e:
        logger.error(f"获取pipeline状态失败: {e}")
        return {"success": False, "error": str(e)[:200]}

@router.get("/script/stream")
async def script_stream(task_id: str = "", request: Request = None):
    async def event_generator():
        lines = [
            "【剧本标题】《龙腾九天》",
            "",
            "【第一幕·归途】",
            "场景：宏伟的宫殿大殿，金碧辉煌",
            "主角龙傲天立于殿前，目光如炬",
            "",
            "龙傲天（内心独白）：十年了，我终于回来了...",
            "",
            "【第二幕·月下】",
            "场景：御花园，月明星稀",
            "神秘女子缓步走来，面纱轻掩",
            "",
            "神秘女子：龙公子，别来无恙？",
            "龙傲天（惊讶）：是你？！",
            "",
            "【第三幕·密室】",
            "场景：密室，烛光摇曳",
            "两人相对而坐，桌上摊开一张泛黄的地图",
            "",
            "神秘女子：这就是藏宝图的另一半",
            "龙傲天：原来你一直...",
            "",
            "【尾声】",
            "",
            "—— 未完待续 ——",
        ]
        for line in lines:
            disconnected = False
            try:
                if request and hasattr(request, 'is_disconnected'):
                    disconnected = await request.is_disconnected()
            except Exception:
                pass
            if disconnected:
                break
            yield f"data: {json.dumps({'line': line, 'task_id': task_id}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.5)
        yield f"data: {json.dumps({'line': '', 'task_id': task_id, 'done': True}, ensure_ascii=False)}\n\n"
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

@router.get("/list")
async def list_pipelines():
    try:
        rows = _execute_db("SELECT id, project_id, status, progress, created FROM pipelines ORDER BY created DESC LIMIT 50")
        return {
            "success": True,
            "pipelines": [{"id": r["id"], "project_id": r["project_id"], "status": r["status"], "progress": r["progress"], "created": r["created"]} for r in rows]
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
        task_id = body.get("task_id", "")
        name = body.get("name", "角色")
        appearance = body.get("appearance", "")
        personality = body.get("personality", "")
        gender = body.get("gender", "")
        
        prompt = f"电影级真人写真，{gender}，{appearance}，{personality}。面部特写，肩膀以上，脸占画面主体。专业影棚灯光，高清画质。NOT cartoon NOT anime NOT 3D NOT illustration."
        
        from services.model_client import UnifiedModel
        result = UnifiedModel.image(prompt=prompt, preferred="qwen", size="1024x1024", timeout=60)
        image_url = result.get("image_url", "")
        if not image_url:
            result = UnifiedModel.image(prompt=prompt, preferred="seedream", size="1024x1024", timeout=60)
            image_url = result.get("image_url", "")
        
        return {"success": bool(image_url), "data": {"image_url": image_url, "task_id": task_id}, "error": "" if image_url else "生成失败"}
    except Exception as e:
        return {"success": False, "data": None, "error": str(e)}

@router.post("/scene-generate")
async def scene_generate(request: Request):
    try:
        body = await request.json()
        scenes = body.get("scenes", [])
        from services.model_client import UnifiedModel
        results = []
        for scene in scenes:
            prompt = f"电影级场景，{scene.get('description', '室内场景')}，专业影视灯光，超高清画质。NOT cartoon NOT anime NOT 3D."
            r = UnifiedModel.image(prompt=prompt, preferred="qwen", size="1024x1024", timeout=60)
            results.append({"name": scene.get("name", ""), "image_url": r.get("image_url", ""), "status": "done" if r.get("image_url") else "error"})
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
```