"""
Agent 记忆引擎
- 精确匹配：同用户+同角色/场景，直接复用结果
- 风格匹配：同类型不同内容，复用 Prompt
- 反思日志：每次跑完记录总结
"""
import json
import time
import hashlib
import sqlite3
import os
import logging

logger = logging.getLogger(__name__)

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "agent_memory.db")


def _get_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            agent_type TEXT NOT NULL,
            memory_key TEXT NOT NULL,
            memory_value TEXT NOT NULL,
            tags TEXT DEFAULT '',
            score REAL DEFAULT 0.0,
            created_at REAL DEFAULT (strftime('%s','now')),
            updated_at REAL DEFAULT (strftime('%s','now')),
            UNIQUE(user_id, agent_type, memory_key)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS agent_reflections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            agent_type TEXT NOT NULL,
            reflection_key TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL DEFAULT (strftime('%s','now'))
        )
    """)
    conn.commit()
    return conn


class AgentMemory:
    """Agent 记忆引擎"""
    
    def __init__(self, user_id: int, agent_type: str):
        self.user_id = user_id
        self.agent_type = agent_type
    
    def _make_key(self, *parts) -> str:
        raw = ":".join(str(p) for p in parts)
        return hashlib.md5(raw.encode()).hexdigest()
    
    def lookup(self, *key_parts):
        """精确查找记忆"""
        mkey = self._make_key(*key_parts)
        conn = _get_db()
        row = conn.execute(
            "SELECT memory_value, tags, score FROM agent_memory WHERE user_id=? AND agent_type=? AND memory_key=?",
            (self.user_id, self.agent_type, mkey)
        ).fetchone()
        conn.close()
        if row:
            return {
                "value": json.loads(row["memory_value"]),
                "tags": row["tags"],
                "score": row["score"],
            }
        return None
    
    def save(self, value: dict, *key_parts, tags: str = ""):
        """保存记忆"""
        mkey = self._make_key(*key_parts)
        conn = _get_db()
        conn.execute("""
            INSERT INTO agent_memory(user_id, agent_type, memory_key, memory_value, tags, updated_at)
            VALUES (?,?,?,?,?, strftime('%s','now'))
            ON CONFLICT(user_id, agent_type, memory_key)
            DO UPDATE SET memory_value=excluded.memory_value, tags=excluded.tags, 
                          updated_at=strftime('%s','now')
        """, (self.user_id, self.agent_type, mkey, json.dumps(value, ensure_ascii=False), tags))
        conn.commit()
        conn.close()
    
    def reflect(self, reflection_key: str, content: str):
        """写反思日志"""
        conn = _get_db()
        conn.execute(
            "INSERT INTO agent_reflections(user_id, agent_type, reflection_key, content) VALUES (?,?,?,?)",
            (self.user_id, self.agent_type, reflection_key, content)
        )
        conn.commit()
        conn.close()
    
    def find_similar(self, tags: str, limit: int = 5) -> list:
        """按标签查找相似记忆"""
        conn = _get_db()
        rows = conn.execute(
            "SELECT memory_value, tags, score FROM agent_memory WHERE user_id=? AND agent_type=? AND tags LIKE ? ORDER BY score DESC LIMIT ?",
            (self.user_id, self.agent_type, f"%{tags}%", limit)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
