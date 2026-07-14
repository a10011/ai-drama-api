import logging
from fastapi import APIRouter, Request
from app_db import fetchone

logger = logging.getLogger("api.v2")
router = APIRouter(prefix="/v2/balance", tags=["v2"])

COST = {"script":5,"director":3,"character":20,"storyboard":10,"scene":15,"tts":5,"subtitle":2,"bgm":3,"video":50,"composite":5}

@router.post("/estimate")
async def v2_estimate(body: dict):
    cc = body.get("character_count", 0)
    total = sum(COST.values()) + cc * COST["character"]
    return {"success": True, "data": {"total_points": total}}

@router.get("/check")
async def v2_balance(request: Request):
    user_id = getattr(request.state, "user_id", 0)
    row = fetchone("SELECT points FROM users WHERE id=?", (user_id,)) if user_id else None
    return {"success": True, "data": {"balance": row["points"] if row else 0}}
