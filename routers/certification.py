"""原创证明 API"""
from fastapi import APIRouter, Request
import logging, json

from services.certification import (
    generate_certificate, get_certificate, get_project_logs
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/cert", tags=["certification"])

@router.post("/generate/{project_id}")
async def create_certificate(project_id: str, request: Request):
    user_id = getattr(request.state, "user_id", 0)
    body = {}
    try:
        raw = await request.body()
        if raw:
            body = json.loads(raw)
    except Exception:
        body = {}
    try:
        cert = generate_certificate(
            project_id=project_id,
            user_id=user_id,
            title=body.get("title", ""),
            genre=body.get("genre", ""),
            video_url=body.get("video_url", "")
        )
        return {"success": True, "data": cert}
    except Exception as e:
        logger.error(f"[Cert] Generate failed: {e}")
        return {"success": False, "error": str(e)}

@router.get("/{project_id}")
async def get_project_certificate(project_id: str):
    try:
        cert = get_certificate(project_id)
        if not cert:
            return {"success": False, "error": "暂无证书，请先生成"}
        return {"success": True, "data": cert}
    except Exception as e:
        return {"success": False, "error": str(e)}

@router.get("/{project_id}/logs")
async def get_logs(project_id: str):
    try:
        logs = get_project_logs(project_id)
        return {"success": True, "data": logs}
    except Exception as e:
        return {"success": False, "error": str(e)}
