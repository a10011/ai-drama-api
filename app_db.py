import sqlite3, os, threading, logging

logger = logging.getLogger("api.db")

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'short_drama.db')
_conn = None
_lock = threading.Lock()

def get_conn():
    global _conn
    with _lock:
        if _conn is None:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute('PRAGMA journal_mode=WAL')
            _conn.execute('PRAGMA foreign_keys=ON')
        return _conn

def init_db():
    conn = get_conn()
    conn.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE,
        password_hash TEXT, email TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, title TEXT,
        genre TEXT, status TEXT DEFAULT 'draft', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS pipeline_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, stage TEXT,
        status TEXT DEFAULT 'pending', data TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()

def execute(sql, params=()):
    conn = get_conn()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur.lastrowid

def fetchone(sql, params=()):
    try:
        row = get_conn().execute(sql, params).fetchone()
        return dict(row) if row else None
    except Exception:
        # [安全修复] 不再静默吞掉 DB 错误，记录日志便于排查
        logger.exception("fetchone failed: sql=%s", sql)
        return None

def fetchall(sql, params=()):
    try:
        rows = get_conn().execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        # [安全修复] 不再静默吞掉 DB 错误，记录日志便于排查
        logger.exception("fetchall failed: sql=%s", sql)
        return []

project_steps = {
    '导演分析': 'director',
    '剧本创作': 'script',
    '角色设计': 'character',
    '分镜生成': 'storyboard',
    '场景生成': 'scene',
    '配音合成': 'tts',
    '字幕生成': 'subtitle',
    'BGM配乐': 'bgm',
    '视频生成': 'video',
    '视频合成': 'composite',
}
