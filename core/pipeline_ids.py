import os
#!/usr/bin/env python3
"""
完整 ID 体系 + 资产追踪表
"""
import sqlite3, os, time, threading

DB = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "short_drama.db"))
_lock = threading.Lock()


def init():
    conn = sqlite3.connect(DB)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pipeline_assets (
            id TEXT PRIMARY KEY,
            pipeline_id TEXT NOT NULL,
            agent TEXT NOT NULL,
            asset_type TEXT NOT NULL,
            asset_url TEXT DEFAULT '',
            file_path TEXT DEFAULT '',
            file_size INTEGER DEFAULT 0,
            meta TEXT DEFAULT '{}',
            created_at REAL DEFAULT (strftime('%s','now')),
            FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
        );
        CREATE INDEX IF NOT EXISTS idx_assets_pipeline ON pipeline_assets(pipeline_id);
        CREATE INDEX IF NOT EXISTS idx_assets_agent ON pipeline_assets(agent);

        CREATE TABLE IF NOT EXISTS seq_ids (
            prefix TEXT PRIMARY KEY,
            seq INTEGER DEFAULT 0
        );
    """)
    conn.commit()
    conn.close()


def next_id(prefix: str = "PRJ") -> str:
    """生成 ID: PRJ-20260705-00001"""
    import datetime
    today = datetime.datetime.now().strftime("%Y%m%d")
    with _lock:
        conn = sqlite3.connect(DB)
        conn.execute(
            "INSERT INTO seq_ids (prefix, seq) VALUES (?, 1) ON CONFLICT(prefix) DO UPDATE SET seq=seq+1",
            (f"{prefix}_{today}",)
        )
        row = conn.execute("SELECT seq FROM seq_ids WHERE prefix=?", (f"{prefix}_{today}",)).fetchone()
        conn.commit()
        conn.close()
        seq = row[0] if row else 1
    return f"{prefix}-{today}-{seq:05d}"


def register_asset(pipeline_id: str, agent: str, asset_type: str, url: str = "", file_path: str = "", meta: dict = None):
    """登记一条资产记录"""
    asset_id = next_id("AST")
    conn = sqlite3.connect(DB)
    conn.execute(
        "INSERT INTO pipeline_assets (id, pipeline_id, agent, asset_type, asset_url, file_path, meta) VALUES (?,?,?,?,?,?,?)",
        (asset_id, pipeline_id, agent, asset_type, url, file_path, (meta and str(meta) or "{}"))
    )
    conn.commit()
    conn.close()
    return asset_id


def get_assets(pipeline_id: str, agent: str = None):
    """查管线资产"""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    if agent:
        rows = conn.execute("SELECT * FROM pipeline_assets WHERE pipeline_id=? AND agent=? ORDER BY created_at", (pipeline_id, agent)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM pipeline_assets WHERE pipeline_id=? ORDER BY created_at", (pipeline_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# 初始化
init()
