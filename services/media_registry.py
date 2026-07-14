"""
MediaRegistry — 统一素材注册中心
所有 agent 产出文件后调用 MediaRegistry.save() 自动入库
"""
import os, json, sqlite3, time, hashlib, logging
import sqlite3 as _sql3, os as _os
_logger = logging.getLogger("media_registry")
_media_db = _sql3.connect(_os.path.join(_os.path.dirname(_os.path.dirname(__file__)), 'data', 'short_drama.db'))
_media_db.execute('''CREATE TABLE IF NOT EXISTS media_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, file_path TEXT NOT NULL,
    media_type TEXT DEFAULT \"figures\", tags TEXT DEFAULT \"\", style TEXT DEFAULT \"\",
    state TEXT DEFAULT \"active\", use_count INTEGER DEFAULT 0, rating INTEGER DEFAULT 0,
    file_size INTEGER DEFAULT 0, user_id INTEGER DEFAULT 0, is_shared INTEGER DEFAULT 1,
    meta TEXT DEFAULT \"{}\", created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
_media_db.commit()

# 统一引用 path_util 的存储根与域名，避免硬编码
from utils.path_util import STORAGE_ROOT, local_path_to_url
from app_config import BASE_URL

DB = "/www/wwwroot/api.mzsh.top/data/app.db"

TYPE_DIRS = {
    "figures": "figures",
    "scenes": "scenes", 
    "audio": "audio",
    "bgm": "bgm",
    "videos": "videos",
    "props": "props",
    "sfx": "sfx",
    "scripts": "scripts",
}

def _get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def save(file_data, file_name, media_type, *,
         name="", tags=None, style="", project_id="", pipeline_id="",
         user_id=0, width=0, height=0, duration=0, metadata=None,
         state="shared"):
    type_dir = TYPE_DIRS.get(media_type, "other")
    # 隔离键统一：pipeline_id 优先，兜底 project_id，再兜底 _shared
    sub_dir = str(pipeline_id) if pipeline_id else (str(project_id) if project_id else "_shared")
    storage_dir = os.path.join(STORAGE_ROOT, type_dir, sub_dir)
    os.makedirs(storage_dir, exist_ok=True)

    ts = int(time.time() * 1000)
    safe_name = "".join(c for c in file_name if c.isascii() and (c.isalnum() or c in "._-"))
    if safe_name in (".jpg", ".png", ".gif", ".webp", ".mp3", ".wav", ".mp4", ".mov"):
        safe_name = ""
    if not safe_name:
        safe_name = f"{media_type}_{ts}"
    short_hash = hashlib.md5(file_data[:4096]).hexdigest()[:8]
    final_name = f"{ts}_{short_hash}_{safe_name}"
    file_path = os.path.join(storage_dir, final_name)

    with open(file_path, "wb") as f:
        f.write(file_data)

    size = os.path.getsize(file_path)
    
    # Size guard: discard files that are too small to be valid images/audio
    MIN_SIZES = {
        "figures": 2000,      # character portraits (reduced from 15000)
        "scenes": 15000,   # scene images  
        "audio": 5000,     # tts/bgm audio
        "bgm": 5000,
        "videos": 50000,   # video files
        "props": 5000,
        "sfx": 3000,
    }
    min_size = MIN_SIZES.get(media_type, 10000)
    if size < min_size:
        os.remove(file_path)
        _logger.warning(f"Discarded {media_type} file (too small: {size}B < {min_size}B): {final_name}")
        return {"url": "", "storage_path": "", "error": f"File too small ({size}B < {min_size}B)"}

    conn = _get_db()
    tags_json = json.dumps(tags or [], ensure_ascii=False)
    meta_json = json.dumps(metadata or {}, ensure_ascii=False)

    conn.execute("""
        INSERT OR REPLACE INTO media_library
        (file_path, file_name, media_type, name, tags, style,
         project_id, pipeline_id, user_id, size_bytes,
         width, height, duration, state, is_shared, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (
        file_path, final_name, media_type, name or safe_name,
        tags_json, style, project_id, pipeline_id, user_id, size,
        width, height, duration, state,
        1 if state == "shared" else 0,
        meta_json
    ))
    conn.commit()
    mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()

    rel = file_path.replace("/www/wwwroot", "")
    pub_url = local_path_to_url(file_path)

    return {"id": mid, "path": file_path, "url": pub_url, "size": size}


def save_from_path(file_path, media_type, *,
                   name="", tags=None, style="", project_id="", pipeline_id="",
                   user_id=0, width=0, height=0, duration=0, metadata=None,
                   state="shared", move=False):
    if not os.path.exists(file_path):
        return None

    type_dir = TYPE_DIRS.get(media_type, "other")
    # 隔离键统一：pipeline_id 优先，兜底 project_id，再兜底 _shared
    sub_dir = str(pipeline_id) if pipeline_id else (str(project_id) if project_id else "_shared")
    storage_dir = os.path.join(STORAGE_ROOT, type_dir, sub_dir)
    os.makedirs(storage_dir, exist_ok=True)

    base_name = os.path.basename(file_path)
    ts = int(time.time() * 1000)
    final_name = f"{ts}_{base_name}"
    dest = os.path.join(storage_dir, final_name)

    if move:
        os.rename(file_path, dest)
    else:
        with open(file_path, "rb") as src:
            with open(dest, "wb") as dst:
                dst.write(src.read())

    size = os.path.getsize(dest)

    with open(dest, "rb") as f:
        data = f.read()
    return save(data, base_name, media_type,
                name=name or base_name, tags=tags, style=style,
                project_id=project_id, pipeline_id=pipeline_id,
                user_id=user_id, width=width, height=height,
                duration=duration, metadata=metadata, state=state)


def find_character(name: str = "", media_type: str = "figures", genre: str = "", tags: list = None, limit: int = 5):
    """素材库优先匹配：按角色名+题材+标签查找已有角色图
    题材匹配规则: 修仙↔修仙, 都市↔都市, 甜宠↔甜宠 等"""
    conn = _get_db()
    where = ["state='shared'"]
    params = []
    if name:
        where.append("(name LIKE ? OR tags LIKE ? OR file_name LIKE ?)")
        like = f"%{name}%"
        params.extend([like, like, like])
    # 题材匹配：style 或 tags 字段包含题材关键词
    if genre:
        genre_short = {"都市": "都市", "甜宠": "甜宠", "悬疑": "悬疑", "仙侠": "仙侠", "古装": "古装",
                       "武侠": "武侠", "喜剧": "喜剧", "科幻": "科幻"}
        g = genre_short.get(genre, genre)
        where.append("(style LIKE ? OR tags LIKE ?)")
        params.extend([f"%{g}%", f"%{g}%"])
    if tags:
        for t in tags:
            where.append("tags LIKE ?")
            params.append(f"%{t}%")
    where.append("media_type = ?")
    params.append(media_type)
    sql = "SELECT * FROM media_library WHERE " + " AND ".join(where) + " ORDER BY use_count DESC, created_at DESC LIMIT ?"
    rows = conn.execute(sql, params + [limit]).fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        fp = d.get("file_path", "")
        if fp and os.path.exists(fp):
            d["url"] = local_path_to_url(fp)
            results.append(d)
    return results


def register_figure(data, name, *, tags=None, project_id="", pipeline_id="", **kw):
    return save(data, f"{name}.jpg", "figures", name=name,
                tags=tags or [], project_id=project_id, pipeline_id=pipeline_id, **kw)

def register_scene(data, name, *, tags=None, project_id="", pipeline_id="", **kw):
    return save(data, f"{name}.jpg", "scenes", name=name,
                tags=tags or [], project_id=project_id, pipeline_id=pipeline_id, **kw)

def register_audio(data, name, *, tags=None, project_id="", pipeline_id="",
                   duration=0, **kw):
    return save(data, f"{name}.wav", "audio", name=name,
                tags=tags or [], project_id=project_id, pipeline_id=pipeline_id,
                duration=duration, **kw)

def register_bgm(data, name, *, tags=None, project_id="", pipeline_id="",
                 duration=0, **kw):
    return save(data, f"{name}.mp3", "bgm", name=name,
                tags=tags or [], project_id=project_id, pipeline_id=pipeline_id,
                duration=duration, **kw)

def register_video(data, name, *, tags=None, project_id="", pipeline_id="",
                   duration=0, width=0, height=0, **kw):
    return save(data, f"{name}.mp4", "videos", name=name,
                tags=tags or [], project_id=project_id, pipeline_id=pipeline_id,
                duration=duration, width=width, height=height, **kw)

def register_prop(data, name, *, tags=None, project_id="", pipeline_id="", **kw):
    return save(data, f"{name}.png", "props", name=name,
                tags=tags or [], project_id=project_id, pipeline_id=pipeline_id, **kw)

_logger.info("MediaRegistry ready")