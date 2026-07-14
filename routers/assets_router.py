import time, logging, uuid, os
from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from app_db import fetchone, fetchall, execute
from utils.path_util import local_path_to_url, url_to_local_path
logger = logging.getLogger("api.assets")
router = APIRouter(prefix="/api/v1/assets", tags=["\u7d20\u6750\u5e93"])

@router.post("/upload")
async def upload_asset(request: Request, file: UploadFile = File(...)):
    user_id = getattr(request.state, "user_id", 0)
    asset_id = uuid.uuid4().hex[:16]
    content = await file.read()
    ext = file.filename.split(".")[-1] if "." in file.filename else "bin"
    fname = f"{asset_id}_{int(time.time())}.{ext}"
    save_dir = f"/www/wwwroot/storage/user_assets/{user_id}"
    os.makedirs(save_dir, exist_ok=True)
    with open(f"{save_dir}/{fname}", 'wb') as f: f.write(content)
    url = local_path_to_url(f"{save_dir}/{fname}")
    mime = file.content_type or "application/octet-stream"
    atype = "image" if "image" in mime else "video" if "video" in mime else "file"
    execute("INSERT INTO user_assets(id,user_id,asset_type,url,filename,file_size,mime_type,created_at)VALUES(?,?,?,?,?,?,?,?)",
        (asset_id, user_id, atype, url, file.filename, len(content), mime, time.time()))
    return {"success": True, "data": {"id": asset_id, "url": url, "filename": file.filename, "asset_type": atype}}

@router.get("/list")
async def list_assets(request: Request, asset_type: str = "", page: int = 1, page_size: int = 30):
    user_id = getattr(request.state, "user_id", 0) if hasattr(request, "state") else 0
    w, p = "WHERE user_id=?", [user_id]
    if asset_type: w += " AND asset_type=?"; p.append(asset_type)
    off = (page - 1) * page_size
    rows = fetchall("SELECT * FROM user_assets " + w + " ORDER BY created_at DESC LIMIT ? OFFSET ?", p + [page_size, off])
    cnt = fetchone("SELECT COUNT(*) as c FROM user_assets " + w, p)
    return {"success": True, "data": {"list": [dict(r) for r in rows], "total": cnt["c"] if cnt else 0, "page": page, "page_size": page_size}}

@router.get("/{asset_id}")
async def get_asset(request: Request, asset_id: str):
    user_id = getattr(request.state, "user_id", 0)
    row = fetchone("SELECT * FROM user_assets WHERE id=? AND user_id=?", (asset_id, user_id))
    if not row: raise HTTPException(404)
    return {"success": True, "data": dict(row)}

@router.delete("/{asset_id}")
async def delete_asset(request: Request, asset_id: str):
    user_id = getattr(request.state, "user_id", 0)
    row = fetchone("SELECT url FROM user_assets WHERE id=? AND user_id=?", (asset_id, user_id))
    if not row: raise HTTPException(404)
    fpath = url_to_local_path(row["url"])
    if os.path.exists(fpath): os.remove(fpath)
    execute("DELETE FROM user_assets WHERE id=? AND user_id=?", (asset_id, user_id))
    return {"success": True, "data": {"id": asset_id}}
