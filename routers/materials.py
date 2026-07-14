"""素材库 API — 增删查"""
import json, time, logging, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter, UploadFile, File
from typing import Optional, List

logger = logging.getLogger("api.materials")
router = APIRouter(prefix="/api/v1/materials", tags=["素材库"])

# 懒加载 materials 模块
# 素材代理
import sqlite3 as _sql

class _MaterialProxy:
    def _db(self):
        conn = _sql.connect("/www/wwwroot/api.mzsh.top/data/app.db")
        conn.row_factory = _sql.Row
        return conn
    
    def init(self):
        c = self._db()
        c.execute('CREATE TABLE IF NOT EXISTS media_library (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, type TEXT, tags TEXT, path TEXT, style TEXT, project_id TEXT, pipeline_id TEXT, user_id INTEGER, width INTEGER DEFAULT 0, height INTEGER DEFAULT 0, duration REAL DEFAULT 0, state TEXT DEFAULT "shared", is_shared INTEGER DEFAULT 1, use_count INTEGER DEFAULT 0, rating REAL DEFAULT 0, metadata TEXT, created REAL)')
        c.commit(); c.close()
    
    def search_materials(self, mtype="", scene="", tags=None, limit=50):
        c = self._db()
        w = ["is_shared=1"]; p = []
        if mtype: w.append("media_type=?"); p.append(mtype)
        if scene: w.append("tags LIKE ?"); p.append("%"+scene+"%")
        if tags:
            for t in tags: w.append("tags LIKE ?"); p.append("%"+t+"%")
        p.append(limit)
        rows = c.execute("SELECT * FROM media_library WHERE "+" AND ".join(w)+" ORDER BY created_at DESC LIMIT ?", p).fetchall()
        c.close()
        return [dict(r) for r in rows]
    
    def get_project_materials(self, project_id, mtype=""):
        c = self._db()
        if mtype:
            rows = c.execute("SELECT * FROM media_library WHERE project_id=? AND media_type=? ORDER BY created_at DESC", (str(project_id), mtype)).fetchall()
        else:
            rows = c.execute("SELECT * FROM media_library WHERE project_id=? ORDER BY created_at DESC", (str(project_id),)).fetchall()
        c.close()
        return [dict(r) for r in rows]

def _m():
    return _MaterialProxy()

@router.get("/init")
async def init_db():
    _m().init()
    return {"success": True, "message": "素材库已初始化"}

@router.get("/search")
async def search(q: str = "", type: str = "image", scene: str = "", tags: str = "", limit: int = 50):
    mat = _m()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    rows = mat.search_materials(type, scene, tag_list, limit)
    return {"success": True, "materials": rows, "count": len(rows)}

@router.get("/project/{project_id}")
async def project_materials(project_id: int, type: str = ""):
    rows = _m().get_project_materials(project_id, type)
    return {"success": True, "materials": rows}

@router.post("/upload")
async def upload_material(type: str, scene_type: str = "", tags: str = "",
                          project_id: int = 0, file: UploadFile = File(...)):
    mat = _m()
    data = await file.read()
    ext = os.path.splitext(file.filename or ".bin")[1] or ".bin"
    path = mat.save_file(data, ext)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    mid = mat.add_material(type, scene_type, tag_list, path, project_id, source="upload")
    return {"success": True, "material_id": mid, "path": path}

@router.get("/match")
async def match_material(type: str = "bgm", keywords: str = ""):
    kw_list = [k.strip() for k in keywords.split(",") if k.strip()]
    result = _m().search_materials(type, keywords, kw_list, 5)
    return {"success": True, "data": result}
    return {"success": False, "message": "未匹配到素材"}

# ═══ 简易扩展路由 ═══
@router.get("")
@router.get("/")
async def list_all(): return {"success": True, "data": []}

@router.get("/list")
async def list_paginated(): return {"success": True, "data": []}

@router.get("/categories")
async def get_categories(): return {"success": True, "data": []}
