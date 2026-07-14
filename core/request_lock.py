"""持久请求锁 — 每个模型调用都有唯一 ID，提交前锁、提交后解锁，绝不重复提交"""
import sqlite3
import json
import time
import os
import uuid
import logging

logger = logging.getLogger("request_lock")

DB = "/www/wwwroot/api.mzsh.top/data/short_drama.db"


def _get_conn():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS request_locks (
            request_id TEXT PRIMARY KEY,
            pipeline_id TEXT NOT NULL,
            user_id INTEGER DEFAULT 0,
            agent TEXT NOT NULL DEFAULT '',
            model_name TEXT NOT NULL DEFAULT '',
            content_type TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'submitted',
            order_id TEXT DEFAULT '',
            sent_at REAL NOT NULL,
            completed_at REAL DEFAULT 0,
            result_url TEXT DEFAULT '',
            error_msg TEXT DEFAULT '',
            provider_task_id TEXT DEFAULT ''
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rl_pipeline ON request_locks(pipeline_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rl_status ON request_locks(status)")
    conn.commit()
    return conn


def generate_request_id(pipeline_id: str = "", agent: str = "", seq: int = 0) -> str:
    """生成唯一请求 ID: REQ-{pipeline_id}-{agent}-{seq}-{random6}"""
    r6 = uuid.uuid4().hex[:6]
    if pipeline_id and agent:
        return f"REQ-{pipeline_id}-{agent}-{seq}-{r6}"
    return f"REQ-{int(time.time()*1000)}-{r6}"


def acquire_lock(request_id: str, pipeline_id: str = "", agent: str = "",
                 model_name: str = "", content_type: str = "",
                 order_id: str = "", user_id: int = 0) -> bool:
    """尝试获取请求锁。成功返回 True，失败（已存在）返回 False。"""
    conn = _get_conn()
    try:
        cur = conn.execute(
            """INSERT OR IGNORE INTO request_locks
            (request_id, pipeline_id, user_id, agent, model_name, content_type, order_id, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (request_id, pipeline_id, user_id, agent, model_name, content_type, order_id, time.time())
        )
        conn.commit()
        acquired = cur.rowcount > 0
        if not acquired:
            # 已经存在，查当前状态
            existing = conn.execute(
                "SELECT status, provider_task_id FROM request_locks WHERE request_id=?",
                (request_id,)
            ).fetchone()
            status = dict(existing)["status"] if existing else "unknown"
            logger.warning(f"[RequestLock] ❌ 重复提交: {request_id} 状态={status}")
        return acquired
    except Exception as e:
        logger.error(f"[RequestLock] acquire_lock error: {e}")
        return False
    finally:
        conn.close()


def complete_lock(request_id: str, success: bool, result_url: str = "",
                  error_msg: str = "", provider_task_id: str = "") -> bool:
    """标记请求完成（成功或失败）"""
    conn = _get_conn()
    try:
        status = "completed" if success else "failed"
        conn.execute(
            """UPDATE request_locks SET status=?, completed_at=?, result_url=?, error_msg=?,
               provider_task_id=COALESCE(NULLIF(?, ''), provider_task_id)
             WHERE request_id=?""",
            (status, time.time(), result_url, error_msg[:500], provider_task_id, request_id)
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"[RequestLock] complete_lock error: {e}")
        return False
    finally:
        conn.close()


def update_provider_task(request_id: str, provider_task_id: str, model_name: str = "") -> bool:
    """记录异步任务的 provider task_id（用于 Seedance 等）"""
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE request_locks SET provider_task_id=?, model_name=? WHERE request_id=?",
            (provider_task_id, model_name, request_id)
        )
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"[RequestLock] update_provider_task error: {e}")
        return False
    finally:
        conn.close()


def get_lock_status(request_id: str) -> dict:
    """查询请求锁状态"""
    conn = _get_conn()
    try:
        row = conn.execute("SELECT * FROM request_locks WHERE request_id=?", (request_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def get_pipeline_locks(pipeline_id: str) -> list:
    """查某条管线的全部请求锁"""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM request_locks WHERE pipeline_id=? ORDER BY sent_at",
            (pipeline_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

def get_pipeline_requests_by_status(pipeline_id: str, status: str) -> list:
    """查某条管线指定状态的请求（如 'submitted'=超时未回）"""
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM request_locks WHERE pipeline_id=? AND status=? ORDER BY sent_at",
        (pipeline_id, status)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_incomplete_pipelines() -> list:
    """查所有未完成的管线（用于重启后恢复）"""
    import sqlite3 as _sc
    conn = _sc.connect(DB, timeout=10)
    rows = conn.execute(
        "SELECT DISTINCT pipeline_id FROM request_locks WHERE status IN ('submitted') "
        "UNION "
        "SELECT id FROM pipelines WHERE status LIKE 'running%'"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


# 初始化
_get_conn().close()
logger.info("RequestLock ready")
