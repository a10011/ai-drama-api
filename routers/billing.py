import json
import os
import time, logging, time, sqlite3
from pydantic import BaseModel
from utils.auth_util import get_user_id
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from services.balance_manager import get_balance, settle

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/billing", tags=["billing"])

@router.get("/balance")
async def balance(request: Request):
    user_id = get_user_id(request)
    bal = get_balance(user_id)
    return JSONResponse({"success": True, "data": bal})

@router.get("/freeze/{project_id}")
async def query_freeze(project_id: str, request: Request):
    user_id = getattr(request.state, "user_id", 0) or request.headers.get("X-User-Id", "0")
    import sqlite3
    db = sqlite3.connect(os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "short_drama.db")))
    db.row_factory = sqlite3.Row
    r = db.execute(
        "SELECT * FROM balance_freeze WHERE project_id=? ORDER BY id DESC LIMIT 1",
        (project_id,)).fetchone()
    db.close()
    if r:
        return JSONResponse({"success": True, "data": dict(r)})
    return JSONResponse({"success": True, "data": None})

@router.get("/settle/{project_id}")
async def settle_endpoint(project_id: str, request: Request):
    user_id = getattr(request.state, "user_id", 0) or request.headers.get("X-User-Id", "0")
    settle(int(user_id) if str(user_id).isdigit() else 0, project_id)
    return JSONResponse({"success": True, "message": "已结算"})

# ── 充值 ──
class RechargeRequest(BaseModel):
    amount: float
    method: str = "\u6a21\u62df\u5145\u503c"

DB = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "..", "data", "short_drama.db"))

def _get_or_create_balance(uid):
    db = None
    try:
        db = sqlite3.connect(DB)
        db.row_factory = sqlite3.Row
        r = db.execute("SELECT * FROM user_balance WHERE user_id=?", (uid,)).fetchone()
        if not r:
            db.execute("INSERT INTO user_balance (user_id, balance, total_charged, total_spent, frozen, created, updated) VALUES (?,0,0,0,0,?,?)",
                       (uid, time.time(), time.time()))
            db.commit()
            r = db.execute("SELECT * FROM user_balance WHERE user_id=?", (uid,)).fetchone()
        return dict(r) if r else {"user_id": uid, "balance": 0, "frozen": 0, "total_charged": 0, "total_spent": 0}
    finally:
        if db:
            db.close()

@router.post("/recharge")
async def billing_recharge(request: Request, body: RechargeRequest):
    user_id = get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="\u8bf7\u5148\u767b\u5f55")
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="\u5145\u503c\u91d1\u989d\u5fc5\u987b\u5927\u4e8e0")
    bal = _get_or_create_balance(user_id)
    db = None
    try:
        db = sqlite3.connect(DB)
        db.execute("UPDATE user_balance SET balance=balance+?, total_charged=total_charged+?, updated=? WHERE user_id=?",
                   (body.amount, body.amount, time.time(), user_id))
        db.commit()
    finally:
        if db:
            db.close()
    bal = _get_or_create_balance(user_id)
    logger.info(f"\u5145\u503c user_id={user_id} amount={body.amount} method={body.method}")
    return {"success": True, "data": {"balance": round(bal["balance"], 2), "charged": body.amount}}


# ── 计费：UNIT_PRICE（硬编码，不依赖 helpers） ──
UNIT_PRICE = {
    "script_generation": 2.0,
    "character_portrait": 1.0,
    "scene_image": 0.5,
    "video_generation": 0.3,
    "tts_dubbing": 0.5,
    "bgm": 1.0,
    "composite": 3.0,
}

def _estimate_cost(script_text: str = "", character_count: int = 0,
                    scene_count: int = 0, video_duration_per_scene: int = 5) -> dict:
    script_cost = UNIT_PRICE["script_generation"]
    character_cost = round(UNIT_PRICE["character_portrait"] * max(character_count, 1), 2)
    scene_cost = round(UNIT_PRICE["scene_image"] * max(scene_count, 1), 2)
    n = max(character_count, scene_count, 1)
    video_cost = round(UNIT_PRICE["video_generation"] * n * video_duration_per_scene, 2)
    tts_cost = round(UNIT_PRICE["tts_dubbing"] * n, 2)
    bgm_cost = UNIT_PRICE["bgm"]
    composite_cost = UNIT_PRICE["composite"]
    total = round(script_cost + character_cost + scene_cost + video_cost + tts_cost + bgm_cost + composite_cost, 2)
    return {
        "estimated_cost_yuan": total,
        "breakdown": {
            "script_generation": script_cost,
            "character_portraits": character_cost,
            "scene_images": scene_cost,
            "video_generation": video_cost,
            "tts_dubbing": tts_cost,
            "bgm": bgm_cost,
            "composite": composite_cost,
            "total": total,
        },
        "detail": {
            "character_count": max(character_count, 1),
            "scene_count": max(scene_count, 1),
            "video_duration_per_scene": video_duration_per_scene,
            "unit_prices": UNIT_PRICE,
        }
    }

def _deduct_balance(user_id: int, amount: float) -> bool:
    db = None
    try:
        db = sqlite3.connect(DB)
        r = db.execute("SELECT balance FROM user_balance WHERE user_id=?", (user_id,)).fetchone()
        if not r or r[0] < amount:
            return False
        db.execute("UPDATE user_balance SET balance=balance-?, total_spent=total_spent+?, updated=? WHERE user_id=?", (amount, amount, time.time(), user_id))
        db.commit()
        return True
    except Exception:
        if db:
            db.rollback()
        return False
    finally:
        if db:
            db.close()

def check_and_deduct_for_project(user_id: int, script_text: str = "",
                                  character_count: int = 0, scene_count: int = 0,
                                  video_duration_per_scene: int = 5) -> dict:
    cost = _estimate_cost(script_text, character_count, scene_count, video_duration_per_scene)
    estimated = cost["estimated_cost_yuan"]
    bal = _get_or_create_balance(user_id)
    balance = bal["balance"]
    if balance < estimated:
        return {
            "ok": False,
            "estimated_cost": estimated,
            "balance": round(balance, 2),
            "shortfall": round(estimated - balance, 2),
            "message": f"余额不足，需要 ¥{estimated}，当前余额 ¥{round(balance, 2)}，还差 ¥{round(estimated - balance, 2)}",
        }
    ok = _deduct_balance(user_id, estimated)
    if not ok:
        return {
            "ok": False,
            "estimated_cost": estimated,
            "balance": round(balance, 2),
            "shortfall": estimated,
            "message": "扣款失败",
        }
    return {
        "ok": True,
        "estimated_cost": estimated,
        "balance": round(balance - estimated, 2),
        "shortfall": 0,
        "message": "扣款成功",
    }
