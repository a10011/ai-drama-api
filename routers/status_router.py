"""
status_router.py — 1 个 API 返回全部状态
GET /api/v1/status/{project_id} → {premise, title, progress, current_stage, log, characters, shots, videos, done, error, _updated}
"""
import json, logging
from fastapi import APIRouter
from routers.context_agent import read

logger = logging.getLogger("api.status")
router = APIRouter(prefix="/api/v1/status", tags=["状态"])

@router.get("/{project_id}")
def get_status(project_id: str):
    ctx = read(project_id)
    if not ctx:
        return {"success": False, "error": "project not found", "data": None}
    return {
        "success": True,
        "data": {
            "premise": ctx.get("premise", ""),
            "title": ctx.get("title", ""),
            "genre": ctx.get("genre", ""),
            "progress": ctx.get("progress", 0),
            "current_stage": ctx.get("current_stage", ""),
            "log": ctx.get("log", ""),
            "director_analysis": ctx.get("director_analysis", {}),
            "characters": ctx.get("characters", []),
            "shots": ctx.get("shots", []),
            "videos": ctx.get("videos", []),
            "audio": ctx.get("tts_audio", []),
            "subtitles": ctx.get("subtitles", []),
            "bgm": ctx.get("bgm", {}),
            "done": ctx.get("done", False),
            "error": ctx.get("error"),
            "updated": ctx.get("_updated", 0),
        }
    }
