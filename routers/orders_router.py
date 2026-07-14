"""订单管理路由 — 2026-06-27"""
import json, time, logging, os, sqlite3
from fastapi import APIRouter, Request, HTTPException
from app_db import fetchone, fetchall, execute
from utils.auth_util import get_user_id

logger = logging.getLogger("api.orders")
router = APIRouter(prefix="/api/v1/orders", tags=["orders"])

# ===== 辅助函数 =====
def _order_row(row):
    """sqlite3.Row → dict，billing_log JSON 转 list"""
    d = dict(row)
    try:
        d['billing_log'] = json.loads(d.get('billing_log', '[]'))
    except (json.JSONDecodeError, TypeError):
        d['billing_log'] = []
    return d

# ===== 会员中心：我的订单 =====
@router.get("/my")
async def my_orders(request: Request):
    user_id = get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="请先登录")
    rows = fetchall(
        "SELECT * FROM orders WHERE user_id=? ORDER BY created DESC LIMIT 50",
        (user_id,)
    )
    return {"success": True, "data": [_order_row(r) for r in (rows or [])]}

# ===== 订单详情 =====
@router.get("/{order_id}")
async def order_detail(order_id: str, request: Request):
    user_id = get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="请先登录")
    row = fetchone("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, user_id))
    if not row:
        raise HTTPException(status_code=404, detail="订单不存在")
    return {"success": True, "data": _order_row(row)}

# ===== 费用统计 =====
@router.get("/stats/summary")
async def order_stats(request: Request):
    user_id = get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="请先登录")
    
    import datetime
    now = time.time()
    month_start = datetime.datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    week_start = now - 7 * 86400
    
    total = fetchone("SELECT COALESCE(SUM(amount),0) as total, COUNT(*) as count FROM orders WHERE user_id=?", (user_id,))
    month = fetchone("SELECT COALESCE(SUM(amount),0) as total FROM orders WHERE user_id=? AND created>=?", (user_id, month_start))
    week = fetchone("SELECT COALESCE(SUM(amount),0) as total FROM orders WHERE user_id=? AND created>=?", (user_id, week_start))
    completed = fetchone("SELECT COUNT(*) as count FROM orders WHERE user_id=? AND status='completed'", (user_id,))
    
    return {"success": True, "data": {
        "total_amount": round(total['total'], 2) if total else 0,
        "total_count": total['count'] if total else 0,
        "month_amount": round(month['total'], 2) if month else 0,
        "week_amount": round(week['total'], 2) if week else 0,
        "completed_count": completed['count'] if completed else 0,
    }}

# ===== 更新订单状态（Pipeline 完成后回调） =====
@router.post("/{order_id}/status")
async def update_order_status(order_id: str, body: dict, request: Request = None):
    # 会员数据隔离：检查归属
    if request:
        user_id = get_user_id(request)
        if user_id > 0:
            row = fetchone("SELECT user_id FROM orders WHERE id=?", (order_id,))
            if not row:
                raise HTTPException(status_code=404, detail="订单不存在")
            if row["user_id"] and row["user_id"] != user_id:
                raise HTTPException(status_code=403, detail="无权操作")
    status = body.get("status", "completed")
    execute("UPDATE orders SET status=?, updated=? WHERE id=?", (status, time.time(), order_id))
    return {"success": True}

# ===== 手动结算（2026-06-27） =====
@router.post("/{order_id}/settle")
async def orders_settle(order_id: str, request: Request):
    """手动触发结算，多退少补"""
    user_id = get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="请先登录")
    # 校验订单归属
    order = fetchone("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, user_id))
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
    try:
        from routers.billing import settle_order
        result = settle_order(order_id)
        return result
    except Exception as e:
        logger.exception(f"手动结算失败 order_id={order_id}")
        raise HTTPException(status_code=500, detail=f"结算失败: {str(e)}")
