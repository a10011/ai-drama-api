"""
Pipeline V2 API — 多智能体 MQ 模式
所有 SQL 与实际表结构严格一致
"""
import json
import os
import time
import logging
from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional

from core.scheduler import start_pipeline
from core.agent_base_v3 import start_worker
from core.pipeline_ids import next_id, register_asset, get_assets
from agents_v2.script_agent import ScriptAgent
from agents_v2.character_agent import CharacterAgent
from agents_v2.scene_agent import SceneAgent
from agents_v2.video_agent import VideoAgent
from agents_v2.director_agent import DirectorAgent
from agents_v2.storyboard_agent import StoryboardAgent
from agents_v2.composite_agent import CompositeAgent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/pipeline", tags=["pipeline-v2"])


class PipelineStartRequest(BaseModel):
    title: str = ""
    genre: str = ""
    synopsis: str = ""
    script_text: str = ""
    user_id: int = 0
    episode: int = 1
    project_id: str = ""
    characters: list = []


@router.post("/start")
async def start(req: PipelineStartRequest, request: Request):
    uid = req.user_id
    if uid <= 0:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
            from app_db import fetchone
            row = fetchone("SELECT id FROM users WHERE token=?", (token,))
            if row:
                uid = row["id"]
            else:
                return {"success": False, "error": "未授权，请先登录"}

    # 如果前端已传入 project_id（从 step_pipeline 转发），直接复用
    if req.project_id:
        project_id = req.project_id
    else:
        project_id = next_id("PRJ")
    pipeline_id = next_id("PL")

    # 写入 pipelines（表结构: id, project_id, script_text, genre, status, progress, total_stages, current_stage, error, step_results, stage_outputs, created, updated, user_id）
    try:
        import sqlite3
        conn = sqlite3.connect(os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "short_drama.db")))
        conn.execute(
            "INSERT INTO pipelines (id, project_id, status, created, updated, user_id) VALUES (?,?,?,?,?,?)",
            (pipeline_id, project_id, "queued", time.time(), time.time(), uid),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[V2] pipeline DB 失败: {e}")

    # 写入 projects（表结构: id, title, script, genre, progress, status, characters, pipeline_steps, created, updated, user_id）
    # 如果 project_id 是前端已有的（字符串如 10011302），不要重复 INSERT
    if not req.project_id:
        try:
            import sqlite3
            conn = sqlite3.connect(os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "short_drama.db")))
            conn.execute(
                "INSERT INTO projects (title, genre, status, progress, user_id, created, updated, script) VALUES (?,?,?,?,?,?,?,?)",
                (req.title, req.genre, "processing", 0, uid, time.time(), time.time(), req.script_text or req.synopsis or ""),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[V2] project DB 失败: {e}")

    # 续集：如果提供已有project_id，复用角色数据
    existing_chars = []
    if req.episode > 1 and req.project_id:
        try:
            conn2 = sqlite3.connect(os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "short_drama.db")))
            rows = conn2.execute(
                "SELECT characters FROM projects WHERE id=? LIMIT 1", 
                (str(req.project_id),)
            ).fetchall()
            if rows and rows[0][0]:
                existing_chars = json.loads(rows[0][0])
                logger.info(f"[V2] 第{req.episode}集: 复用{len(existing_chars)}个角色")
            conn2.close()
        except Exception as e:
            logger.warning(f"[V2] 角色复用失败: {e}")
    
    start_pipeline(pipeline_id, uid, {
        "title": req.title,
        "genre": req.genre,
        "synopsis": req.synopsis,
        "script_text": req.script_text,
        "project_id": project_id,
        "episode": req.episode,
        "existing_characters": existing_chars,
        "characters": req.characters if req.characters else existing_chars,
    })

    return {
        "success": True,
        "project_id": project_id,
        "pipeline_id": pipeline_id,
        "script_text": req.script_text,
        "message": "管线已投递到 MQ",
    }


@router.get("/assets/{pipeline_id}")
async def list_assets(pipeline_id: str, agent: str = None):
    assets = get_assets(pipeline_id, agent)
    embed = []
    for a in assets:
        embed.append({
            "asset_id": a["id"],
            "type": a["asset_type"],
            "agent": a["agent"],
            "url": a["asset_url"],
            "meta": a["meta"],
        })
    return {"success": True, "data": embed, "total": len(embed)}


@router.get("/status/{pipeline_id}")
async def status(pipeline_id: str):
    try:
        import sqlite3
        conn = sqlite3.connect(os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "short_drama.db")))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, project_id, status, created, updated FROM pipelines WHERE id=?",
            (pipeline_id,)
        ).fetchone()
        conn.close()
        if row:
            return {"success": True, "data": dict(row)}
        return {"success": False, "error": "未找到管线"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def start_all_workers(counts: dict = None):
    if counts is None:
        # [P1] Worker 数改为环境变量配置，默认值降低防 API 费用失控
        import os as _os
        counts = {
            "script": int(_os.environ.get("WORKER_SCRIPT", "4")),
            "director": int(_os.environ.get("WORKER_DIRECTOR", "2")),
            "character": int(_os.environ.get("WORKER_CHARACTER", "3")),
            "storyboard": int(_os.environ.get("WORKER_STORYBOARD", "2")),
            "scene": int(_os.environ.get("WORKER_SCENE", "3")),
            "video": int(_os.environ.get("WORKER_VIDEO", "2")),
            "composite": int(_os.environ.get("WORKER_COMPOSITE", "1")),
        }
    workers = [
        ("script", ScriptAgent),
        ("director", DirectorAgent),
        ("character", CharacterAgent),
        ("storyboard", StoryboardAgent),
        ("scene", SceneAgent),
        ("video", VideoAgent),
        ("composite", CompositeAgent),
    ]
    total = 0
    for name, cls in workers:
        n = counts.get(name, 5)
        for _ in range(n):
            start_worker(cls)
            total += 1
        logger.info(f"[V2] {name}: {n} Worker")
    logger.info(f"[V2] 总计 {total} 个 Worker 启动完成")
    return total


@router.get("/project/{project_id}")
async def v2_project_detail(project_id: str):
    """V2 项目详情（按 project_id 查，含全部资产）"""
    try:
        import sqlite3
        conn = sqlite3.connect(os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "short_drama.db")))
        conn.row_factory = sqlite3.Row
        # projects 表没有 pipeline_id 列，用 project_id 关联 pipelines 表
        row = conn.execute(
            "SELECT p.id, p.title, p.genre, p.status, p.progress, p.created, p.updated FROM projects p WHERE p.id=?",
            (project_id,)
        ).fetchone()
        if not row:
            conn.close()
            return {"success": False, "error": "项目不存在"}
        data = dict(row)
        # 查 pipeline_id
        pipe = conn.execute("SELECT id FROM pipelines WHERE project_id=?", (project_id,)).fetchone()
        if pipe:
            data["pipeline_id"] = pipe[0]
            # 查资产
            try:
                assets_rows = conn.execute("SELECT id, agent, asset_type, asset_url FROM pipeline_assets WHERE pipeline_id=?", (pipe[0],)).fetchall()
                data["assets"] = [{"asset_id": a[0], "agent": a[1], "type": a[2], "url": a[3]} for a in assets_rows]
                data["assets_count"] = len(data["assets"])
            except:
                data["assets"] = []
                data["assets_count"] = 0
        else:
            data["pipeline_id"] = ""
            data["assets"] = []
            data["assets_count"] = 0
        conn.close()
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/project/{project_id}/download")
async def v2_download(project_id: str):
    """V2 项目下载"""
    try:
        import sqlite3
        conn = sqlite3.connect(os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "short_drama.db")))
        row = conn.execute(
            "SELECT p.id, p.title FROM projects p JOIN pipelines pl ON pl.project_id = p.id WHERE p.id=? AND pl.status='completed'",
            (project_id,)
        ).fetchone()
        conn.close()
        if not row:
            return {"success": False, "error": "尚未完成或没有视频"}
        return {"success": True, "project_id": row[0], "title": row[1], "video_url": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}
