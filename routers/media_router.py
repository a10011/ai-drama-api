from utils.image_processor import standardize_image
import time
"""
Media Library Router — 素材库 API
"""
import os, json, sqlite3, shutil
from datetime import datetime
from fastapi import APIRouter, Request
from pydantic import BaseModel
from utils.path_util import local_path_to_url

router = APIRouter(prefix="/api/v1/media", tags=["media"])
STORAGE = "/www/wwwroot/storage"
DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "app.db")

def _db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

# ===== List media =====

def _add_url(row):
    """给媒体项添加 url 字段"""
    d = dict(row)
    fp = d.get("file_path", "")
    if fp and (fp.startswith("/www/wwwroot") or fp.startswith("/storage")):
        d["url"] = local_path_to_url(fp)
    return d


@router.get("/library")
async def list_media(request: Request = None, project_id: str = "", media_type: str = "", page: int = 1, page_size: int = 50, filter: str = ""):
    conn = _db()
    where = []
    params = []
    # app.db 无 project_id 字段，忽略该过滤参数（保留签名兼容前端）
    if media_type:
        where.append("media_type = ?")
        params.append(media_type)

    user_id = get_user_id(request) if request else 0
    # app.db 的 media_library 无 project_id 字段，按 media_type + is_shared 过滤
    if filter == "private" and user_id > 0:
        where.append("is_shared = 0 AND user_id = ?")
        params.append(user_id)
    elif filter == "public":
        where.append("is_shared = 1")
    elif filter == "all" and user_id > 0:
        where.append("(is_shared = 1 OR user_id = ?)")
        params.append(user_id)
    elif user_id > 0 and not filter:
        # 默认：公共素材 + 自己的私有素材
        where.append("(is_shared = 1 OR user_id = ?)")
        params.append(user_id)
    else:
        where.append("is_shared = 1")

    offset = (page - 1) * page_size
    if where:
        wheresql = "WHERE " + " AND ".join(where)
    else:
        wheresql = ""
    total = conn.execute("SELECT COUNT(*) FROM media_library " + wheresql, params).fetchone()[0]
    rows = conn.execute(
        "SELECT id,file_path,file_name,media_type,name,size_bytes,width,height,duration,created_at,url,tags,is_shared FROM media_library " + wheresql + " ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [page_size, offset]
    ).fetchall()
    conn.close()
    return {
        "success": True,
        "data": {
            "items": [_add_url(r) for r in rows],
            "total": total,
            "page": page,
            "page_size": page_size
        }
    }

# ===== Media stats =====
@router.get("/librarystats")
async def media_stats(request: Request = None):
    conn = _db()
    rows = conn.execute("SELECT media_type, COUNT(*) as cnt FROM media_library GROUP BY media_type").fetchall()
    data = {r["media_type"]: r["cnt"] for r in rows}
    conn.close()
    return {"success": True, "data": data}


# ===== Get single media =====
@router.get("/library/{media_id}")
async def get_media(media_id: int):
    conn = _db()
    row = conn.execute("SELECT id,file_path,file_name,media_type,name,size_bytes,width,height,duration,created_at,url,tags,is_shared FROM media_library WHERE id = ?", (media_id,)).fetchone()
    conn.close()
    if not row:
        return {"success": False, "error": "not found"}
    return {"success": True, "data": dict(row)}

# ===== Delete media =====
@router.delete("/library/{media_id}")
async def delete_media(media_id: int, request: Request):
    conn = _db()
    row = conn.execute("SELECT id,file_path,file_name,media_type,name,size_bytes,width,height,duration,created_at,url,tags,is_shared FROM media_library WHERE id = ?", (media_id,)).fetchone()
    if not row:
        conn.close()
        return {"success": False, "error": "not found"}
    data = dict(row)
    uid = get_user_id(request)
    owner_id = data.get("user_id", 0)
    if owner_id > 0 and uid != owner_id:
        conn.close()
        return {"success": False, "error": "No permission to delete"}
    # Delete file
    file_path = data.get("file_path", "")
    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as ex_: logger.warning(f"[media_router]  {ex_}")
    conn.execute("DELETE FROM media_library WHERE id = ?", (media_id,))
    conn.commit()
    conn.close()
    return {"success": True, "data": {"deleted": media_id}}

# ===== 用户上传角色图到素材库 =====
from fastapi import UploadFile, File, Form
from utils.auth_util import get_user_id

import logging
logger = logging.getLogger(__name__)
@router.post("/library/upload")
async def upload_media(request: Request):
    from fastapi import Request as _R
    import json as _j
    content_type = request.headers.get('content-type','missing')
    logger.info(f"[UPLOAD] content-type={content_type}")
    """用户上传图片到素材库
    - 默认放入会员私有素材库 (is_shared=0)
    - share_to_library=True 时公开共享到公共素材库
    """
    try:
        form = await request.form()
        file = form.get("file")
        if not file or not hasattr(file, "filename"):
            return {"success": False, "error": "请选择文件"}
        data = await file.read()
        if not data:
            return {"success": False, "error": "文件为空"}
        name = str(form.get("name") or "")
        media_type = str(form.get("media_type") or "figures")
        tags = str(form.get("tags") or "")
        style = str(form.get("style") or "")
        share_raw = str(form.get("share_to_library") or "false")
        share_to_library = share_raw.lower() in ("true", "1")
        tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        uid = get_user_id(request)
        from services.media_registry import save
        # 标准化人物图片
        try:
            data = standardize_image(data) if media_type == "figure" else data
        except Exception as ex_: logger.warning(f"[media_router]  {ex_}")
        result = save(
            data, file.filename or "upload.jpg", media_type,
            name=name or str(file.filename or ""),
            tags=tag_list, style=style,
            user_id=uid if uid else 0,
            state="shared" if share_to_library else "private"
        )
        return {"success": True, "data": result}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}



# ===== 简单上传测试（绕过验证）=====
@router.post("/library/upload2")
async def upload_media_v2(request: Request):
    try:
        form = await request.form()
        file = form.get("file")
        if not file:
            return {"success": False, "error": "no file"}
        data = await file.read()
        fn = getattr(file, 'filename', 'u.jpg') or 'u.jpg'
        name = str(form.get("name", "") or "")
        actor_name = str(form.get("actor_name", "") or "")
        actor_gender = str(form.get("actor_gender", "") or "")
        tags = str(form.get("tags", "") or "")
        if actor_name or actor_gender:
            tags_parts = [t for t in tags.split(",") if t.strip()] if tags else []
            if actor_name: tags_parts.append("actor_name:" + actor_name)
            if actor_gender: tags_parts.append("actor_gender:" + actor_gender)
            tags = ",".join(tags_parts)
        share_raw = str(form.get("share_to_library", "") or "false")
        uid = get_user_id(request)
        from services.media_registry import save
        result = save(
            data, fn, str(form.get("media_type", "") or "figures"),
            name=name or fn,
            tags=[t.strip() for t in tags.split(",") if t.strip()] if tags else [],
            style=str(form.get("style", "") or ""),
            user_id=uid if uid else 0,
            state="shared" if share_raw.lower() in ("true","1") else "private"
        )
        return {"success": True, "data": result}
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"success": False, "error": str(e)}


# ===== 分享到素材库（需签协议）=====
class ShareRequest(BaseModel):
    agreed: bool = True  # 用户勾选同意协议

@router.post("/library/{media_id}/share")
async def share_to_library(media_id: int, req: ShareRequest, request: Request):
    """用户将私有素材分享到公共素材库，需同意授权协议"""
    if not req.agreed:
        return {"success": False, "error": "请先同意授权协议"}
    uid = get_user_id(request)
    if not uid:
        return {"success": False, "error": "请先登录"}
    conn = _db()
    row = conn.execute("SELECT id,file_path,file_name,media_type,name,size_bytes,width,height,duration,created_at,url,tags,is_shared FROM media_library WHERE id = ? AND user_id = ?", 
                       (media_id, uid)).fetchone()
    if not row:
        conn.close()
        return {"success": False, "error": "素材不存在或无权操作"}
    conn.execute(
        "UPDATE media_library SET state='shared', is_shared=1, metadata=json_set(COALESCE(metadata,'{}'), '$.shared_at', ?, '$.license_agreed', ?) WHERE id = ?",
        (datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S'), 'true', media_id)
    )
    conn.commit()
    conn.close()
    return {"success": True, "data": {"id": media_id, "state": "shared", "message": "已分享到公共素材库，授权协议已生效"}}


@router.get("/license/agreement")
async def get_license_agreement():
    """获取授权协议全文"""
    return {
        "success": True,
        "data": {
            "title": "AI面短剧 素材库授权协议",
            "version": "2.1",
            "content": """
本协议由您（以下简称「用户」）与福建面子信息科技有限公司（以下简称「平台」）
就素材分享及授权使用事宜共同签署。

第一条·授权性质
用户将素材（包括但不限于图片、照片、肖像、角色形象等）分享至「AI面短剧」
公共素材库，即视为用户以不可撤销、无期限、全球范围、免许可费的方式，
全权授权平台及平台全体用户使用该素材。
授权范围包括但不限于：复制、修改、改编、翻译、嵌入、汇编、公开展示、
信息网络传播、商业利用及再许可。

第二条·限定用途
授权素材的使用严格限定于合法合规的短剧视频创作及相关衍生业务：
  1）AI短剧角色形象生成、角色形象化展示与剧情嵌入
  2）短视频内容的制作、发布、分发与信息网络传播
  3）平台内协作创作、导演工作流、场景搭建与成品展示
  4）前述用途的推广宣传、包装设计及商业化运营
平台及用户不得将授权素材用于以下禁止用途：
诈骗、色情、赌博、暴力恐怖、政治敏感、侵犯第三方合法权益等任何违法违规活动。

第三条·撤回与赔偿责任
素材一经分享，用户不得以任何理由单方面撤回或限制已授权素材的继续使用。
若用户单方面要求平台删除、下架已分享的素材，并因此导致基于该素材
创作的内容（包括短剧视频、角色形象、场景画面等）受到影响的：
  1）用户须全额赔偿受影响方已投入的全部制作成本（含但不限于API调用费用、
     算力费用、人力成本、第三方服务费用、宣发费用等）
  2）赔偿因下架/替换内容导致的平台商誉损失及用户投诉处理费用
  3）独立承担由此引发的全部法律费用、仲裁费用及第三方索赔款项
平台有权在收到足额赔偿前先行冻结用户账户余额及相关资产。

第四条·权利保证
用户声明并保证：对所分享的素材拥有完整、合法、无瑕疵的民事权利，
包括但不限于肖像权、著作权、商标权、专利及所有相关知识产权。
如因用户分享的素材侵犯任何第三方合法权益（包括但不限于人格权、知识产权、
商业秘密等）而引发法律纠纷、索赔、行政调查或刑事追诉，
由用户独立承担全部法律责任及经济赔偿，平台不承担任何连带责任。

第五条·平台管理权
基于福建面子信息科技有限公司的经营自主权，平台保留无需另行通知，
随时对素材实施下架、屏蔽、限制访问或永久删除等管理措施的权利，
适用于以下情形：
  1）素材内容违反适用法律法规或公序良俗
  2）收到第三方权利人的有效侵权通知或司法文书
  3）平台通过技术手段判定素材存在安全风险或合规隐患
  4）平台经营策略调整或产品功能变更
平台不因上述管理行为对用户或任何第三方承担赔偿责任。

第六条·争议解决
因本协议引起的或与之相关的任何争议，双方应首先友好协商解决。
协商不成的，任何一方可将争议提交至平台住所地有管辖权的人民法院诉讼解决。
本协议的订立、生效、解释、履行及争议解决均适用中华人民共和国法律。

第七条·协议修改
平台有权根据业务发展、法律合规要求或经营需要修改本协议条款，
修改后将在平台公告栏及站内消息公示不少于7日。
公示期满后用户继续使用素材库即视为接受修改后的协议。
如用户不同意修改，应在公示期内联系平台客服撤回所有已分享素材，
逾期未撤回的视为接受。

勾选同意即视为用户已全文阅读、充分理解并不可撤销地接受本协议全部条款。
"""
        }
    }


# ===== Cleanup project media =====
class CleanupRequest(BaseModel):
    project_id: str
    keep_ids: list = []

@router.post("/cleanup")
async def cleanup_media(req: CleanupRequest, request: Request):
    """Delete all project media except keep_ids"""
    conn = _db()
    rows = conn.execute(
        "SELECT id,file_path,file_name,media_type,name,size_bytes,width,height,duration,created_at,url,tags,is_shared FROM media_library WHERE project_id = ?", (req.project_id,)
    ).fetchall()
    
    deleted = []
    kept = []
    for row in rows:
        d = dict(row)
        if d["id"] in req.keep_ids:
            kept.append(d["id"])
        else:
            fp = d.get("file_path", "")
            if fp and os.path.exists(fp):
                try:
                    os.remove(fp)
                except Exception as ex_: logger.warning(f"[media_router]  {ex_}")
            conn.execute("DELETE FROM media_library WHERE id = ?", (d["id"],))
            deleted.append(d["id"])
    
    conn.commit()
    conn.close()
    return {
        "success": True,
        "data": {"deleted": len(deleted), "kept": len(kept), "deleted_ids": deleted}
    }

# ===== Register media (called by agents) =====
class RegisterRequest(BaseModel):
    file_path: str
    file_name: str = ""
    media_type: str = "other"
    name: str = ""
    project_id: str = ""
    pipeline_id: str = ""
    user_id: int = 0
    width: int = 0
    height: int = 0
    duration: float = 0
    metadata: dict = {}

@router.post("/register")
async def register_media(req: RegisterRequest, request: Request):
    conn = _db()
    fp = req.file_path
    if not os.path.exists(fp):
        conn.close()
        return {"success": False, "error": f"file not found: {fp}"}
    
    size = os.path.getsize(fp)
    fname = req.file_name or os.path.basename(fp)
    
    try:
        conn.execute("""
            INSERT OR REPLACE INTO media_library 
            (file_path, file_name, media_type, name, project_id, pipeline_id, user_id, size_bytes, width, height, duration, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (
            fp, fname, req.media_type, req.name, req.project_id, req.pipeline_id,
            req.user_id, size, req.width, req.height, req.duration,
            json.dumps(req.metadata, ensure_ascii=False)
        ))
        conn.commit()
        mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    except:
        mid = -1
    conn.close()
    
    # Generate public URL
    pub_url = local_path_to_url(fp)

    return {"success": True, "data": {"id": mid, "url": pub_url, "size": size}}

# ===== Helper: save file and register =====
def save_and_register(file_data: bytes, file_name: str, media_type: str, name: str = "",
                      project_id: str = "", pipeline_id: str = "", user_id: int = 0,
                      width: int = 0, height: int = 0, duration: float = 0,
                      metadata: dict = None) -> dict:
    """Save a file to storage and register in media_library"""
    type_dir = os.path.join(STORAGE, media_type)
    os.makedirs(type_dir, exist_ok=True)
    
    # Generate unique filename
    ts = int(time.time() * 1000)
    safe_name = "".join(c for c in file_name if c.isalnum() or c in "._-")
    if not safe_name:
        safe_name = f"{media_type}_{ts}"
    fp = os.path.join(type_dir, f"{ts}_{safe_name}")
    
    with open(fp, "wb") as f:
        f.write(file_data)
    
    # Register in DB
    conn = sqlite3.connect(DB)
    size = os.path.getsize(fp)
    conn.execute("""
        INSERT OR REPLACE INTO media_library 
        (file_path, file_name, media_type, name, project_id, pipeline_id, user_id, size_bytes, width, height, duration, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
    """, (
        fp, file_name, media_type, name, project_id, pipeline_id,
        user_id, size, width, height, duration,
        json.dumps(metadata or {}, ensure_ascii=False)
    ))
    conn.commit()
    mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    
    return {"id": mid, "path": fp, "url": local_path_to_url(fp), "size": size}
