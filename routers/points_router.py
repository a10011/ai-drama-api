"""
积分查询 - 2026-06-22
"""
from fastapi import APIRouter, Request
from utils.auth_util import get_user_id
from services.usage_tracker import query_usage

router = APIRouter(prefix="/api/v1/points", tags=["积分"])

@router.get("")
async def get_points(request: Request):
    """获取用户积分余额和历史"""
    user_id = get_user_id(request)
    try:
        history = query_usage(user_id=user_id, limit=20)
        balance = sum(h.get("tokens", 0) for h in history) if history else 0
        return {"success": True, "balance": balance, "history": history or []}
    except Exception as e:
        return {"success": True, "balance": 0, "history": []}
