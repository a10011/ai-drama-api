"""
Pipeline V2 API — 多智能体 MQ 模式
"""
import json
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
        if auth.startswith("Bearer"):
            uid = 1

    # 全局锁已禁用
    project_id = next_id("PRJ")
    pipeline_id = next_id("PL")

    # 写入 pipelines
    db_proj_id = 0
    try:
        import sqlite3
        conn = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
        conn.execute(
            "INSERT INTO pipelines (id, project_id, user_id, status, created, updated) VALUES (?,?,?,?,?,?)",
            (pipeline_id, project_id, uid, "queued", time.time(), time.time()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[V2] pipeline DB 失败: {e}")

    # 写入 projects（用原始 auto-increment id）
    try:
        import sqlite3
        conn = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
        cur = conn.execute(
            "INSERT INTO projects (title, genre, status, progress, user_id, created, updated, script) VALUES (?,?,?,?,?,?,?,?)",
            (req.title, req.genre, "processing", 0, uid, time.time(), time.time(), req.script_text or req.synopsis or ""),
        )
        db_proj_id = cur.lastrowid
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[V2] project DB 失败: {e}")

    # 续集：如果提供已有project_id，复用角色数据
    existing_chars = []
    if req.episode > 1 and req.project_id:
        try:
            conn2 = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
            rows = conn2.execute(
                "SELECT characters FROM episodes WHERE project_id=? AND episode_num=?", 
                (str(req.project_id), req.episode - 1)
            ).fetchall()
            if rows:
                existing_chars = json.loads(rows[0][0] or "[]")
                logger.info(f"[V2] 第{req.episode}集: 复用第{req.episode-1}集{len(existing_chars)}个角色")
            conn2.close()
        except Exception as e:
            logger.warning(f"[V2] 续集角色复用失败: {e}")
    
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
        conn = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
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
        counts = {"script": 16, "director": 2, "character": 6, "storyboard": 3, "scene": 6, "video": 3, "composite": 2}  # audio removed, video=音画同出
    workers = [
        ("script", ScriptAgent),
        ("director", DirectorAgent),
        ("character", CharacterAgent),
        ("storyboard", StoryboardAgent),
        ("scene", SceneAgent),
        ("video", VideoAgent),      # 音画同出
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


@router.get("/project/{project_code}")
async def v2_project_detail(project_code: str):
    """V2 项目详情（按 project_id 查，含全部资产）"""
    try:
        import sqlite3
        conn = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, pipeline_id, title, genre, status, progress, created, updated FROM projects WHERE id=?",
            (project_code,)
        ).fetchone()
        if not row:
            conn.close()
            return {"success": False, "error": "项目不存在"}
        data = dict(row)
        assets_rows = conn.execute("SELECT id, agent, asset_type, asset_url FROM pipeline_assets WHERE pipeline_id=?", (data["pipeline_id"],)).fetchall()
        conn.close()
        data["assets"] = [{"asset_id": a["id"], "agent": a["agent"], "type": a["asset_type"], "url": a["asset_url"]} for a in assets_rows]
        data["assets_count"] = len(data["assets"])
        return {"success": True, "data": data}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/project/{project_code}/download")
async def v2_download(project_code: str):
    """V2 项目下载（返回视频直链）"""
    try:
        import sqlite3
        conn = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
        row = conn.execute(
            "SELECT id, title FROM projects WHERE id=? AND pipeline_id IS NOT NULL",
            (project_code,)
        ).fetchone()
        conn.close()
        if not row:
            return {"success": False, "error": "尚未完成或没有视频"}
        return {"success": True, "project_code": row[0], "title": row[1], "video_url": ""}
    except Exception as e:
        return {"success": False, "error": str(e)}
