"""
Hermes Safety Audit — 模型风控拦截日志
记录所有模型 API 返回的拦截/违规事件，供人工审核
"""
import json
import time
import sqlite3
import os
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("SAFETY_DB_PATH", os.path.join(os.path.dirname(__file__), "data", "safety_block_logs.db"))


def _get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS safety_block_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_id TEXT DEFAULT '',
            agent TEXT DEFAULT '',       -- script|character|scene|audio|video
            content_type TEXT NOT NULL,   -- script|portrait|scene_image|video|audio|bgm|lip_sync
            action TEXT NOT NULL,         -- block|replace|warn
            provider TEXT DEFAULT '',     -- deepseek|seedream|seedance|cosyvoice|doubao-music
            model TEXT DEFAULT '',
            original TEXT DEFAULT '',     -- 原始内容/提示词（截断）
            replaced TEXT DEFAULT '',     -- 替换后内容（清洗时）
            reason TEXT DEFAULT '',       -- 高危词|IP角色|暴力|色情|政治|其他
            error_code TEXT DEFAULT '',
            error_msg TEXT DEFAULT '',
            user_id INTEGER DEFAULT 0,
            created_at REAL DEFAULT (strftime('%s','now')),
            reviewed INTEGER DEFAULT 0,
            review_note TEXT DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sbl_pipeline ON safety_block_logs(pipeline_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sbl_content ON safety_block_logs(content_type, action)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sbl_time ON safety_block_logs(created_at)
    """)
    conn.commit()
    return conn


def log_event(
    content_type: str,
    action: str,
    provider: str = "",
    model: str = "",
    original: str = "",
    replaced: str = "",
    reason: str = "",
    error_code: str = "",
    error_msg: str = "",
    pipeline_id: str = "",
    agent: str = "",
    user_id: int = 0,
) -> int:
    """记录一条风控事件"""
    # 截断长文本
    MAX_LEN = 500
    if len(original) > MAX_LEN:
        original = original[:MAX_LEN] + "..."
    if len(replaced) > MAX_LEN:
        replaced = replaced[:MAX_LEN] + "..."

    conn = _get_db()
    cur = conn.execute(
        """INSERT INTO safety_block_logs
        (pipeline_id, agent, content_type, action, provider, model,
         original, replaced, reason, error_code, error_msg, user_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (pipeline_id, agent, content_type, action, provider, model,
         original, replaced, reason, error_code, error_msg, user_id)
    )
    eid = cur.lastrowid
    conn.commit()
    conn.close()
    logger.warning(f"[SafetyAudit] #{eid} {content_type}.{action} | {reason} | {error_msg[:60]}")
    return eid


def get_logs(content_type: str = "", pipeline_id: str = "",
             action: str = "", limit: int = 50, offset: int = 0) -> list:
    """查风控日志"""
    conn = _get_db()
    conn.row_factory = sqlite3.Row
    where = []
    params = []
    if content_type:
        where.append("content_type=?")
        params.append(content_type)
    if pipeline_id:
        where.append("pipeline_id=?")
        params.append(pipeline_id)
    if action:
        where.append("action=?")
        params.append(action)
    sql = "SELECT * FROM safety_block_logs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params += [limit, offset]
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats() -> dict:
    """风控统计概览"""
    conn = _get_db()
    total = conn.execute("SELECT COUNT(*) FROM safety_block_logs").fetchone()[0]
    by_type = conn.execute(
        "SELECT content_type, action, COUNT(*) as c FROM safety_block_logs GROUP BY content_type, action ORDER BY c DESC"
    ).fetchall()
    unrev = conn.execute("SELECT COUNT(*) FROM safety_block_logs WHERE reviewed=0").fetchone()[0]
    conn.close()
    return {
        "total": total,
        "unreviewed": unrev,
        "by_type_action": [dict(r) for r in by_type],
    }


# 初始化
_get_db()
print("Safety audit DB ready")
