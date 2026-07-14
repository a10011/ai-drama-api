from fastapi import APIRouter
import json
router = APIRouter(prefix="/api/v1/video/composite", tags=["composite"])

@router.get("/progress/{project_id}")
async def composite_progress(project_id: str):
    try:
        from routers.pipeline import _execute_db
        rows = _execute_db("SELECT status,data,error FROM pipeline_progress WHERE project_id=? AND stage='composite' ORDER BY id DESC LIMIT 1", (str(project_id),))
        if not rows:
            return {"success": True, "data": {"status": "pending", "progress": 0}}
        r = rows[0]
        d = json.loads(r["data"] or "{}")
        return {"success": True, "data": {"status": r["status"], "progress": d.get("progress", 0), "video_url": d.get("video_url", ""), "error": r["error"] or ""}}
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}
