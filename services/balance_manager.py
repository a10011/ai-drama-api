#!/usr/bin/env python3
"""余额管理：冻结 → 实际扣费 → 多退少补
[优化] 统一使用 app_db 全局连接池（WAL 模式），避免每个请求新建 SQLite 连接。
"""

import json, time, logging
logger = logging.getLogger(__name__)

# 使用 app_db 的全局连接池（线程安全 + WAL）
from app_db import get_conn

# 各环节单价（¥）
UNIT_PRICE = {
    "scene_image": 0.06,
    "portrait_image": 0.06,
    "tts": 0.002,
    "bgm": 0.50,
    "video_r2v": 0.50,
    "videoretalk": 0.30,
    "llm": 0.10,
}


def _db():
    """复用 app_db 全局连接（WAL 模式，线程安全）"""
    return get_conn()


def get_balance(user_id: int) -> dict:
    """获取余额：{total, available, frozen}"""
    db = _db()
    r = db.execute("SELECT balance, frozen FROM user_balance WHERE user_id=?", (user_id,)).fetchone()
    if not r:
        return {"total": 0, "available": 0, "frozen": 0, "balance": 0}
    total = r["balance"] or 0
    frozen = r["frozen"] or 0
    return {"total": total, "available": total - frozen, "frozen": frozen, "balance": total}


def freeze(user_id: int, project_id: str, amount: float) -> dict:
    """冻结余额"""
    db = _db()
    bal = get_balance(user_id)
    if bal["available"] < amount:
        return {"success": False, "message": f"余额不足，可用¥{bal['available']:.2f}，需¥{amount:.2f}"}

    db.execute("UPDATE user_balance SET frozen = frozen + ? WHERE user_id=?", (amount, user_id))
    db.execute("""INSERT INTO balance_freeze (user_id, project_id, frozen_amount, status, created_at)
                  VALUES (?, ?, ?, 'frozen', ?)""", (user_id, str(project_id), amount, time.time()))
    db.commit()
    logger.info(f"[Balance] 冻结¥{amount:.2f} user={user_id} project={project_id}")
    return {"success": True, "message": f"已冻结¥{amount:.2f}"}


def record_cost(user_id: int, project_id: str, cost_type: str, count: int = 1):
    """记录实际花费（每生成一个素材调一次）"""
    price = UNIT_PRICE.get(cost_type, 0) * count
    if price <= 0:
        return

    db = _db()
    r = db.execute(
        "SELECT id, detail FROM balance_freeze WHERE project_id=? AND status='frozen' ORDER BY id DESC LIMIT 1",
        (str(project_id),)).fetchone()
    if r:
        detail = json.loads(r["detail"] or "{}")
        detail[cost_type] = detail.get(cost_type, 0) + count
        db.execute("UPDATE balance_freeze SET actual_cost = actual_cost + ?, detail = ? WHERE id=?",
                   (price, json.dumps(detail, ensure_ascii=False), r["id"]))
        db.commit()
        logger.info(f"[Balance] 记录¥{price:.2f}({cost_type}×{count}) project={project_id}")


def settle(user_id: int, project_id: str):
    """结算：多退少补"""
    db = _db()
    r = db.execute(
        "SELECT id, frozen_amount, actual_cost FROM balance_freeze WHERE project_id=? AND status='frozen' ORDER BY id DESC LIMIT 1",
        (str(project_id),)).fetchone()

    if not r:
        return

    frozen = r["frozen_amount"]
    actual = r["actual_cost"]
    diff = frozen - actual  # 正=退 负=补

    if diff > 0:
        db.execute("UPDATE user_balance SET balance = balance + ?, frozen = frozen - ? WHERE user_id=?",
                   (diff, frozen, user_id))
        logger.info(f"[Balance] 退还¥{diff:.2f} user={user_id} project={project_id} (冻结¥{frozen} 实花¥{actual:.2f})")
    elif diff < 0:
        db.execute("UPDATE user_balance SET frozen = frozen - ? WHERE user_id=?", (frozen, user_id))
        db.execute("UPDATE user_balance SET balance = balance + ? WHERE user_id=?", (diff, user_id))
        logger.info(f"[Balance] 补扣¥{abs(diff):.2f} user={user_id} project={project_id}")
    else:
        db.execute("UPDATE user_balance SET frozen = frozen - ? WHERE user_id=?", (frozen, user_id))
        logger.info(f"[Balance] 结算刚好¥{actual:.2f} user={user_id} project={project_id}")

    db.execute("UPDATE balance_freeze SET status='settled', settled_at=? WHERE id=?",
               (time.time(), r["id"]))
    db.commit()


def cancel_freeze(user_id: int, project_id: str):
    """取消冻结（全额退）"""
    db = _db()
    r = db.execute(
        "SELECT frozen_amount FROM balance_freeze WHERE project_id=? AND status='frozen' ORDER BY id DESC LIMIT 1",
        (str(project_id),)).fetchone()
    if r:
        db.execute("UPDATE user_balance SET frozen = frozen - ? WHERE user_id=?", (r["frozen_amount"], user_id))
        db.execute("UPDATE balance_freeze SET status='cancelled' WHERE project_id=? AND status='frozen'",
                   (str(project_id),))
        db.commit()
        logger.info(f"[Balance] 取消冻结¥{r['frozen_amount']} user={user_id} project={project_id}")
