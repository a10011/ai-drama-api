#!/usr/bin/env python3
"""
会员 API Key 管理 — BYOK (Bring Your Own Key)
+ 用量计费
"""
import sqlite3
import json
import time
import logging
import os

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "short_drama.db")

logger = logging.getLogger(__name__)

# ── 支持的 Provider ──
# 平台使用费：每秒 6 分（￥0.06/s）
PLATFORM_FEE_PER_SECOND = 0.06

SUPPORTED_PROVIDERS = {
    "ark_volc": "火山方舟",
}

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_tables():
    """初始化 BYOK 和用量表"""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            api_key TEXT NOT NULL DEFAULT '',
            api_secret TEXT DEFAULT '',
            description TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at REAL DEFAULT (strftime('%s','now')),
            updated_at REAL DEFAULT (strftime('%s','now')),
            UNIQUE(user_id, provider)
        );

        CREATE TABLE IF NOT EXISTS usage_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            project_id TEXT,
            pipeline_id TEXT,
            provider TEXT,
            model TEXT,
            tokens INTEGER DEFAULT 0,
            duration_sec REAL DEFAULT 0,
            byok INTEGER DEFAULT 0,
            cost REAL DEFAULT 0,
            created_at REAL DEFAULT (strftime('%s','now'))
        );

        CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_records(user_id);
        CREATE INDEX IF NOT EXISTS idx_usage_time ON usage_records(created_at);
    """)
    conn.commit()
    conn.close()
    logger.info("[KeyManager] BYOK + 用量表初始化完成")

# ── Key 管理 ──
def get_user_keys(user_id: int) -> dict:
    """获取会员的全部 Key 配置"""
    conn = get_db()
    rows = conn.execute(
        "SELECT provider, api_key, api_secret, description FROM user_api_keys WHERE user_id=? AND is_active=1",
        (user_id,)
    ).fetchall()
    conn.close()
    return {r["provider"]: {"api_key": r["api_key"], "api_secret": r["api_secret"]} for r in rows}

def save_user_key(user_id: int, provider: str, api_key: str, api_secret: str = "", desc: str = ""):
    """保存/更新会员的 Key"""
    conn = get_db()
    conn.execute("""
        INSERT INTO user_api_keys (user_id, provider, api_key, api_secret, description, updated_at)
        VALUES (?,?,?,?,?, strftime('%s','now'))
        ON CONFLICT(user_id, provider)
        DO UPDATE SET api_key=excluded.api_key, api_secret=excluded.api_secret,
                      description=excluded.description, is_active=1,
                      updated_at=strftime('%s','now')
    """, (user_id, provider, api_key, api_secret, desc))
    conn.commit()
    conn.close()

def delete_user_key(user_id: int, provider: str):
    conn = get_db()
    conn.execute("DELETE FROM user_api_keys WHERE user_id=? AND provider=?", (user_id, provider))
    conn.commit()
    conn.close()

# ── Key 路由 ──
def get_effective_key(user_id: int, provider: str, system_key_func=None):
    """
    获取有效 Key：
    1. 优先用会员自己的 Key
    2. 没有则用系统 Key
    """
    conn = get_db()
    row = conn.execute(
        "SELECT api_key, api_secret FROM user_api_keys WHERE user_id=? AND provider=? AND is_active=1",
        (user_id, provider)
    ).fetchone()
    conn.close()

    if row and row["api_key"]:
        return {
            "key": row["api_key"],
            "secret": row["api_secret"],
            "byok": True
        }

    # 回退到系统 Key
    if system_key_func:
        sys_key = system_key_func()
        return {"key": sys_key, "secret": "", "byok": False}

    return {"key": "", "secret": "", "byok": False}


def get_user_keys_dict(user_id: int) -> dict:
    """获取会员的全部 Key，按 provider 映射"""
    conn = get_db()
    rows = conn.execute(
        "SELECT provider, api_key FROM user_api_keys WHERE user_id=? AND is_active=1",
        (user_id,)
    ).fetchall()
    conn.close()
    return {r["provider"]: r["api_key"] for r in rows}

# ── 用量记录 ──
def record_usage(user_id: int, project_id: str, pipeline_id: str,
                 provider: str, model: str, tokens: int = 0,
                 duration_sec: float = 0, byok: bool = False):
    """记录一次 API 调用用量"""
    conn = get_db()
    conn.execute("""
        INSERT INTO usage_records (user_id, project_id, pipeline_id, provider, model, tokens, duration_sec, byok)
        VALUES (?,?,?,?,?,?,?,?)
    """, (user_id, project_id, pipeline_id, provider, model, tokens, duration_sec, 1 if byok else 0))
    conn.commit()
    conn.close()

def get_user_usage(user_id: int, since: float = 0) -> dict:
    """查询会员用量汇总"""
    conn = get_db()
    rows = conn.execute("""
        SELECT provider, byok,
               COUNT(*) as calls,
               SUM(tokens) as total_tokens,
               SUM(duration_sec) as total_duration
        FROM usage_records
        WHERE user_id=? AND created_at>?
        GROUP BY provider, byok
    """, (user_id, since)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

if __name__ == "__main__":
    init_tables()
    print("BYOK + 用量表初始化完成 ✅")
