"""模型用量追踪器 — 自动记录每次 AI 模型调用明细"""
import time
import json
import logging
import sqlite3
import os
from typing import Optional, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)

MODEL_PRICES = {
    "deepseek-chat": {
        "type": "llm",
        "price_input": 0.001,
        "price_output": 0.002
    },
    "qwen-max": {
        "type": "llm",
        "price_input": 0.004,
        "price_output": 0.012
    },
    "qwen-plus": {
        "type": "llm",
        "price_input": 0.001,
        "price_output": 0.002
    },
    "qwen-turbo": {
        "type": "llm",
        "price_input": 0.0005,
        "price_output": 0.001
    },
    "doubao": {
        "type": "llm",
        "price_input": 0.0008,
        "price_output": 0.002
    },
    "seedream": {
        "type": "image",
        "price": 0.02
    },
    "hidream": {
        "type": "image",
        "price": 0.01
    },
    "agnes": {
        "type": "image",
        "price": 0.001
    },
    "wanxiang": {
        "type": "image",
        "price": 0.005
    },
    "jimeng-v1": {
        "type": "image",
        "price": 0.008
    },
    "kling-v1": {
        "type": "video",
        "price_per_sec": 0.02
    },
    "kling-v1.6": {
        "type": "video",
        "price_per_sec": 0.03
    },
    "kling-v2-6": {
        "type": "video",
        "price_per_sec": 0.03
    },
    "seedance": {
        "type": "video",
        "price_per_sec": 0.04
    },
    "happyhorse-t2v": {
        "type": "video",
        "price_per_sec": 0.012
    },
    "happyhorse-i2v": {
        "type": "video",
        "price_per_sec": 0.012
    },
    "happyhorse-r2v": {
        "type": "video",
        "price_per_sec": 0.012
    },
    "happyhorse-video-edit": {
        "type": "video",
        "price_per_sec": 0.012
    },
    "wan2.7_t2v": {
        "type": "video",
        "price_per_sec": 0.02
    },
    "wan2.7_i2v": {
        "type": "video",
        "price_per_sec": 0.02
    },
    "edge-tts": {
        "type": "tts",
        "price_per_char": 0.0
    },
    "cosyvoice": {
        "type": "tts",
        "price_per_char": 2e-05
    },
    "cosyvoice-v1": {
        "type": "tts",
        "price_per_char": 2e-05
    },
    "music_api": {
        "type": "bgm",
        "price": 0.001
    },
    "local_bgm": {
        "type": "bgm",
        "price": 0.0
    },
    "subtitle": {
        "type": "subtitle",
        "price": 0.0
    },
    "bgm": {
        "type": "bgm",
        "price": 0.0
    },
    "composite": {
        "type": "composite",
        "price": 0.0
    }
}

def _get_usage_db() -> sqlite3.Connection:
    db_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
    os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(os.path.join(db_dir, "usage.db"))
    conn.row_factory = sqlite3.Row
    return conn

def ensure_table():
    """确保数据库表存在"""
    try:
        conn = _get_usage_db()
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS model_usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER DEFAULT 0,
            drama_id TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            model_name TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'bailian',
            model_type TEXT NOT NULL DEFAULT 'llm',
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            char_count INTEGER DEFAULT 0,
            image_count INTEGER DEFAULT 0,
            video_duration INTEGER DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'success',
            error_msg TEXT DEFAULT '',
            request_id TEXT DEFAULT '',
            cost REAL DEFAULT 0.0,
            duration_ms INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")
        c.execute("""CREATE INDEX IF NOT EXISTS idx_usage_model ON model_usage_logs(model_name)""")
        c.execute("""CREATE INDEX IF NOT EXISTS idx_usage_user ON model_usage_logs(user_id)""")
        c.execute("""CREATE INDEX IF NOT EXISTS idx_usage_time ON model_usage_logs(created_at)""")
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to ensure usage table: {e}")


def log_usage(
    model_name: str,
    provider: str = "bailian",
    model_type: str = "llm",
    status: str = "success",
    error_msg: str = "",
    user_id: int = 0,
    drama_id: str = "",
    session_id: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
    char_count: int = 0,
    image_count: int = 0,
    video_duration: int = 0,
    request_id: str = "",
    duration_ms: int = 0,
) -> int:
    """记录一次模型调用"""
    try:
        ensure_table()
        cost = 0.0
        price_info = MODEL_PRICES.get(model_name, {})
        if price_info:
            if price_info.get("type") == "llm":
                p_in = price_info.get("price_input", 0)
                p_out = price_info.get("price_output", 0)
                cost = (prompt_tokens / 1000 * p_in) + (completion_tokens / 1000 * p_out)
            elif price_info.get("type") == "image":
                cost = price_info.get("price", 0) * max(image_count, 1)
            elif price_info.get("type") == "tts":
                cost = price_info.get("price_per_char", 0) * char_count
            elif price_info.get("type") == "video":
                cost = price_info.get("price", 0)
        
        conn = _get_usage_db()
        c = conn.cursor()
        c.execute("""INSERT INTO model_usage_logs 
            (user_id, drama_id, session_id, model_name, provider, model_type,
             prompt_tokens, completion_tokens, total_tokens, char_count,
             image_count, video_duration, status, error_msg, request_id,
             cost, duration_ms)
            VALUES (?,?,?,?,?,?, ?,?,?,?, ?,?,?,?,?, ?,?)""",
            (user_id, drama_id, session_id, model_name, provider, model_type,
             prompt_tokens, completion_tokens, total_tokens, char_count,
             image_count, video_duration, status, error_msg, request_id,
             cost, duration_ms))
        conn.commit()
        row_id = c.lastrowid
        conn.close()
        return row_id
    except Exception as e:
        logger.error(f"Failed to log usage: {e}")
        return 0


class UsageTracker:
    """上下文管理器，自动记录模型调用开始和结束"""
    
    def __init__(self, model_name: str, provider: str = "bailian", 
                 model_type: str = "llm", user_id: int = 0, drama_id: str = ""):
        self.model_name = model_name
        self.provider = provider
        self.model_type = model_type
        self.user_id = user_id
        self.drama_id = drama_id
        self.start_time = 0
        self.log_id = 0
        
    def __enter__(self):
        self.start_time = time.time()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = int((time.time() - self.start_time) * 1000)
        if exc_type:
            self._record(status="error", error_msg=str(exc_val)[:500], duration_ms=duration)
        return False
    
    def _record(self, **kwargs):
        self.log_id = log_usage(
            model_name=self.model_name,
            provider=self.provider,
            model_type=self.model_type,
            user_id=self.user_id,
            drama_id=self.drama_id,
            duration_ms=kwargs.pop("duration_ms", 0),
            **kwargs
        )


def query_usage(model_name: str = None, user_id: int = None, drama_id: str = None,
                start_date: str = None, end_date: str = None,
                limit: int = 100, offset: int = 0) -> dict:
    """查询用量统计"""
    ensure_table()
    conn = _get_usage_db()
    c = conn.cursor()
    
    where = []
    params = []
    if model_name:
        where.append("model_name = ?")
        params.append(model_name)
    if user_id:
        where.append("user_id = ?")
        params.append(user_id)
    if drama_id:
        where.append("drama_id = ?")
        params.append(drama_id)
    if start_date:
        where.append("created_at >= ?")
        params.append(start_date)
    if end_date:
        where.append("created_at <= ?")
        params.append(end_date)
    
    w = " AND ".join(where) if where else "1=1"
    where_clause = "WHERE " + w
    
    c.execute("""SELECT model_name, model_type, provider,
                  COUNT(*) as calls,
                  SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as success_calls,
                  SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as error_calls,
                  SUM(total_tokens) as total_tokens,
                  SUM(char_count) as total_chars,
                  SUM(image_count) as total_images,
                  SUM(cost) as total_cost,
                  SUM(duration_ms) as total_duration_ms
           FROM model_usage_logs """ + where_clause + """
           GROUP BY model_name ORDER BY calls DESC""", params)
    by_model = [dict(r) for r in c.fetchall()]
    
    c.execute("SELECT SUM(cost) as total_cost, COUNT(*) as total_calls FROM model_usage_logs " + where_clause, params)
    summary = dict(c.fetchone())
    
    c.execute("SELECT * FROM model_usage_logs " + where_clause + " ORDER BY id DESC LIMIT ? OFFSET ?",
              params + [limit, offset])
    recent = [dict(r) for r in c.fetchall()]
    
    conn.close()
    
    return {
        "summary": summary,
        "by_model": by_model,
        "recent": recent,
        "total": len(recent)
    }


def init_usage_db():
    """初始化数据库（幂等调用）"""
    ensure_table()
    logger.info("Model usage tracking table ready")