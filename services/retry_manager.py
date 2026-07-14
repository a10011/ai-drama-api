"""错误分类与追踪 — 绝不盲重试
- 超时/网络断 → 不知道服务端有没有处理 → 不重试，留 order_id 查
- 内容违规/拦截 → 记录错误原因，等 Agent 修 prompt
- 鉴权错误 → 标记人工介入
- 限流 → 过会儿重试
- 明确的服务端5xx → 可以重试
"""
import sqlite3
import json
import time
import logging
from typing import Optional, Dict, Any
from enum import Enum

logger = logging.getLogger("error_classifier")

DB = "/www/wwwroot/api.mzsh.top/data/short_drama.db"


class ErrorCategory(Enum):
    """错误分类"""
    CONTENT_BLOCK = "content_block"       # 内容违规/拦截 — 需要修 prompt
    TIMEOUT = "timeout"                   # 超时 — 不知道有没有处理，不重试
    AUTH_ERROR = "auth_error"            # 鉴权/Key 错误 — 需要更新 Key
    RATE_LIMIT = "rate_limit"            # 限流/429 — 可以等会儿重试
    SERVER_ERROR = "server_error"        # 服务端5xx — 服务端问题，可以重试
    NETWORK_ERROR = "network_error"      # 网络错误 — 请求没到服务端，可以重试
    UNKNOWN = "unknown"                  # 无法分类 — 不重试


def classify_error(error_msg: str) -> ErrorCategory:
    """根据错误信息分类"""
    if not error_msg:
        return ErrorCategory.UNKNOWN

    err = error_msg.lower()

    # 内容违规/拦截 — 火山方舟 etl 返回的
    if any(k in err for k in [
        "block", "refused", "rejected", "violation", "inappropriate",
        "sensitive", "risky", "not allowed", "content policy",
        "内容违规", "敏感词", "安全审核", "不合规", "被拦截",
        "inappropriate_content", "content_filter", "moderation",
        "risk control", "blocked by"
    ]):
        return ErrorCategory.CONTENT_BLOCK

    # 超时 — 请求发出去了，不知道服务端有没有处理
    if any(k in err for k in [
        "timeout", "time out", "timed out", "connection timeout",
        "read timed out", "连接超时"
    ]):
        return ErrorCategory.TIMEOUT

    # 鉴权错误
    if any(k in err for k in [
        "auth", "unauthorized", "forbidden", "invalid key", "api_key",
        "apikey", "invalid credential", "token", "permission denied",
        "鉴权", "认证失败", "无权限", "密钥错误"
    ]):
        return ErrorCategory.AUTH_ERROR

    # 限流
    if any(k in err for k in [
        "rate", "limit", "quota", "throttle", "too many requests",
        "429", "限流", "频率限制"
    ]):
        return ErrorCategory.RATE_LIMIT

    # 服务端错误
    if any(k in err for k in [
        "500", "502", "503", "504", "internal server error",
        "service unavailable", "bad gateway", "server error"
    ]):
        return ErrorCategory.SERVER_ERROR

    # 网络错误 — 请求没到服务端
    if any(k in err for k in [
        "connection refused", "connection reset", "connection aborted",
        "no route to host", "network unreachable", "connection error",
        "dns lookup", "name resolution"
    ]):
        return ErrorCategory.NETWORK_ERROR

    return ErrorCategory.UNKNOWN


def should_retry(category: ErrorCategory, attempt: int = 0) -> bool:
    """判断是否应该重试"""
    if category == ErrorCategory.RATE_LIMIT:
        return attempt < 3  # 限流等一会儿，最多3次
    if category == ErrorCategory.SERVER_ERROR:
        return attempt < 2  # 服务端5xx，最多2次
    if category == ErrorCategory.NETWORK_ERROR:
        return attempt < 2  # 网络错误，最多2次
    return False  # 其他情况一律不重试


def get_retry_delay(category: ErrorCategory, attempt: int) -> int:
    """获取重试延迟（秒）"""
    if category == ErrorCategory.RATE_LIMIT:
        return min(30 * (attempt + 1), 120)  # 30s → 60s → 90s
    return min(5 * (attempt + 1), 30)  # 5s → 10s → 15s


# ── 错误追踪表 ──

def init_table():
    """初始化错误追踪表"""
    conn = sqlite3.connect(DB, timeout=10)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS model_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pipeline_id TEXT NOT NULL DEFAULT '',
            agent TEXT NOT NULL DEFAULT '',
            model_name TEXT NOT NULL DEFAULT '',
            content_type TEXT NOT NULL DEFAULT '',  -- portrait|video|audio|script
            error_msg TEXT NOT NULL DEFAULT '',
            error_category TEXT NOT NULL DEFAULT '',
            order_id TEXT NOT NULL DEFAULT '',
            provider_task_id TEXT NOT NULL DEFAULT '',  -- Seedance task_id
            has_retried INTEGER DEFAULT 0,
            retry_count INTEGER DEFAULT 0,
            resolved INTEGER DEFAULT 0,
            resolved_at REAL DEFAULT 0,
            created_at REAL DEFAULT (strftime('%s','now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_err_time ON model_errors(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_err_pipe ON model_errors(pipeline_id)")
    conn.commit()
    conn.close()


def log_error(
    pipeline_id: str = "",
    agent: str = "",
    model_name: str = "",
    content_type: str = "",
    error_msg: str = "",
    order_id: str = "",
    provider_task_id: str = "",
) -> int:
    """记录模型错误到追踪表"""
    category = classify_error(error_msg).value
    init_table()
    conn = sqlite3.connect(DB, timeout=10)
    cur = conn.execute(
        """INSERT INTO model_errors
        (pipeline_id, agent, model_name, content_type, error_msg, error_category, order_id, provider_task_id)
        VALUES (?,?,?,?,?,?,?,?)""",
        (pipeline_id, agent, model_name, content_type, error_msg[:500],
         category, order_id, provider_task_id)
    )
    eid = cur.lastrowid
    conn.commit()
    conn.close()
    logger.warning(f"[ErrorTracker] #{eid} {content_type}.{category} | {error_msg[:80]}")
    return eid


def get_pipeline_errors(pipeline_id: str, limit: int = 20) -> list:
    """查某条管线的全部错误"""
    init_table()
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM model_errors WHERE pipeline_id=? ORDER BY created_at DESC LIMIT ?",
        (pipeline_id, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_resolved(eid: int):
    """标记错误已处理"""
    init_table()
    conn = sqlite3.connect(DB, timeout=10)
    conn.execute("UPDATE model_errors SET resolved=1, resolved_at=? WHERE id=?",
                 (time.time(), eid))
    conn.commit()
    conn.close()


def count_unresolved() -> int:
    """统计未解决的错误"""
    init_table()
    conn = sqlite3.connect(DB, timeout=10)
    n = conn.execute("SELECT COUNT(*) FROM model_errors WHERE resolved=0").fetchone()[0]
    conn.close()
    return n


# ── 注意：没有任何自动重试循环 ──
# 错误只记录，不把同样的 prompt 重新提交
# Agent 收到错误后自行决定如何处理

init_table()


def enqueue(call_type, model_name, kwargs=None, priority=0, max_retries=3, pipeline_id=""):
    pass

def get_next_interval(retry_count=0, base=30):
    return base * (2 ** retry_count)
