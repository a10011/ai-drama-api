"""补充缺失的前端 API 路由 — 短剧 V2 全流程 + 会员/个人中心"""
import json, time, logging, sqlite3, os, hashlib, bcrypt
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from app_db import fetchone, fetchall, execute
from utils.auth_util import get_user_id
from services.usage_tracker import query_usage

logger = logging.getLogger("api.missing")
router = APIRouter(prefix="/api/v1", tags=["补充路由"])

# ═══════════════════════════════════════════════════════════
# 1. billing/usage — 前端 ProfilePage & Membership 需要
# ═══════════════════════════════════════════════════════════
@router.get("/billing/usage")
async def billing_usage(request: Request):
    user_id = get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")
    
    now = time.time()
    day_start = now - 86400
    week_start = now - 7 * 86400
    
    db = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
    db.row_factory = sqlite3.Row
    
    # 今日生成次数
    daily_gen = db.execute(
        "SELECT COUNT(*) as cnt FROM pipeline_progress WHERE project_id IN "
        "(SELECT id FROM projects WHERE user_id=?) AND stage='composite' AND status='completed' AND started_at > ?",
        (user_id, time.strftime("%Y-%m-%d", time.localtime(day_start)))
    ).fetchone()["cnt"]
    
    # 今日视频解析次数
    daily_analysis = db.execute(
        "SELECT COUNT(*) as cnt FROM pipeline_progress WHERE project_id IN "
        "(SELECT id FROM projects WHERE user_id=?) AND stage='director' AND status='completed' AND started_at > ?",
        (user_id, time.strftime("%Y-%m-%d", time.localtime(day_start)))
    ).fetchone()["cnt"]
    
    # 本月总生成
    month_start = time.strftime("%Y-%m-01", time.localtime())
    monthly_gen = db.execute(
        "SELECT COUNT(*) as cnt FROM pipeline_progress WHERE project_id IN "
        "(SELECT id FROM projects WHERE user_id=?) AND stage='composite' AND status='completed' AND started_at >= ?",
        (user_id, month_start)
    ).fetchone()["cnt"]
    
    db.close()
    
    # 会员限额
    tier_row = db.execute("SELECT tier FROM users WHERE id=?", (user_id,)).fetchone()
    tier = tier_row["tier"] if tier_row else "free"
    limits = {
        "free": {"daily_generations": 3, "daily_analysis": 2},
        "pro": {"daily_generations": 999, "daily_analysis": 999},
        "enterprise": {"daily_generations": 999, "daily_analysis": 999},
    }
    lim = limits.get(tier, limits["free"])
    
    return {
        "success": True,
        "data": {
            "daily_generations": daily_gen,
            "daily_analysis": daily_analysis,
            "monthly_generations": monthly_gen,
            "limits": lim,
        }
    }

# ═══════════════════════════════════════════════════════════
# 2. pipeline/status/{id} — V2 管线状态查询（ResultPage 需要）
# ═══════════════════════════════════════════════════════════
@router.get("/pipeline/status/{project_id}")
async def v2_pipeline_status(project_id: str, request: Request):
    user_id = get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")
    
    # 检查项目归属
    row = fetchone("SELECT id FROM projects WHERE id=? AND user_id=?", (project_id, user_id))
    if not row:
        raise HTTPException(status_code=404, detail="项目不存在")
    
    # 查 V2 pipelines 表
    conn = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
    conn.row_factory = sqlite3.Row
    pipe = conn.execute(
        "SELECT id, project_id, status, created, updated FROM pipelines WHERE project_id=? ORDER BY created DESC LIMIT 1",
        (project_id,)
    ).fetchone()
    
    # 查 pipeline_progress 表
    stages = conn.execute(
        "SELECT stage, status, data, error, retry_count, started_at, finished_at FROM pipeline_progress WHERE project_id=? ORDER BY id",
        (project_id,)
    ).fetchall()
    
    # 查 projects 表获取视频URL
    proj = conn.execute(
        "SELECT project_code, video_url, title, genre, status FROM projects WHERE id=?",
        (project_id,)
    ).fetchone()
    
    conn.close()
    
    stage_list = []
    for s in stages:
        data = {}
        try:
            data = json.loads(s["data"]) if s["data"] else {}
        except:
            pass
        stage_list.append({
            "stage": s["stage"],
            "status": s["status"],
            "data": data,
            "error": s["error"] or "",
            "retry_count": s["retry_count"],
            "started_at": s["started_at"],
            "finished_at": s["finished_at"],
        })
    
    return {
        "success": True,
        "data": {
            "project_id": project_id,
            "title": proj["title"] if proj else "",
            "genre": proj["genre"] if proj else "",
            "status": proj["status"] if proj else "draft",
            "video_url": proj["video_url"] if proj else "",
            "final_url": proj["video_url"] if proj else "",
            "pipeline_id": pipe["id"] if pipe else "",
            "pipeline_status": pipe["status"] if pipe else "pending",
            "stages": stage_list,
            "created": pipe["created"] if pipe else 0,
            "updated": pipe["updated"] if pipe else 0,
        }
    }

# ═══════════════════════════════════════════════════════════
# 3. pipeline/continue-episode — 续写下一集
# ═══════════════════════════════════════════════════════════
@router.post("/pipeline/continue-episode")
async def continue_episode(request: Request):
    user_id = get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")
    
    body = await request.json()
    project_id = body.get("project_id", "")
    script_text = body.get("script_text", "")
    
    if not project_id or not script_text:
        return JSONResponse({"success": False, "error": "缺少参数"}, status_code=400)
    
    # 检查归属
    row = fetchone("SELECT id FROM projects WHERE id=? AND user_id=?", (project_id, user_id))
    if not row:
        return JSONResponse({"success": False, "error": "无权操作"}, status_code=403)
    
    # 归档当前集
    execute(
        "INSERT INTO episodes (project_id, episode_num, script_text, status, created) VALUES (?, ?, ?, 'archived', ?)",
        (project_id, 1, script_text, time.time())
    )
    
    return {
        "success": True,
        "message": "当前集已归档，可开始新一集创作",
        "next_episode": 2,
    }

# ═══════════════════════════════════════════════════════════
# 4. pipeline/episodes/{id} — 获取剧集列表
# ═══════════════════════════════════════════════════════════
@router.get("/pipeline/episodes/{project_id}")
async def get_episodes(project_id: str, request: Request):
    user_id = get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")
    
    row = fetchone("SELECT id FROM projects WHERE id=? AND user_id=?", (project_id, user_id))
    if not row:
        return JSONResponse({"success": False, "error": "项目不存在"}, status_code=404)
    
    episodes = fetchall(
        "SELECT episode_num, status, created FROM episodes WHERE project_id=? ORDER BY episode_num",
        (project_id,)
    )
    
    return {
        "success": True,
        "episodes": [
            {"episode": e["episode_num"], "status": e["status"], "created": e["created"]}
            for e in (episodes or [])
        ]
    }

# ═══════════════════════════════════════════════════════════
# 5. pipeline/episode/{id}/{num} — 切换剧集
# ═══════════════════════════════════════════════════════════
@router.get("/pipeline/episode/{project_id}/{episode_num}")
async def switch_episode(project_id: str, episode_num: int, request: Request):
    user_id = get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")
    
    row = fetchone("SELECT id FROM projects WHERE id=? AND user_id=?", (project_id, user_id))
    if not row:
        return JSONResponse({"success": False, "error": "项目不存在"}, status_code=404)
    
    ep = fetchone("SELECT script_text, status FROM episodes WHERE project_id=? AND episode_num=?", (project_id, episode_num))
    if not ep:
        return JSONResponse({"success": False, "error": "剧集不存在"}, status_code=404)
    
    return {
        "success": True,
        "data": {
            "episode": episode_num,
            "script_text": ep["script_text"],
            "is_archived": ep["status"] == "archived",
        }
    }

# ═══════════════════════════════════════════════════════════
# 6. character/scene-images/{id} — 场景图分配
# ═══════════════════════════════════════════════════════════
@router.get("/character/scene-images/{project_id}")
async def get_scene_images(project_id: str, request: Request):
    user_id = get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")
    
    row = fetchone("SELECT id FROM projects WHERE id=? AND user_id=?", (project_id, user_id))
    if not row:
        return JSONResponse({"success": False, "error": "项目不存在"}, status_code=404)
    
    # 从 pipeline_progress 读取 scene 阶段数据
    conn = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "SELECT data FROM pipeline_progress WHERE project_id=? AND stage='scene' AND status='completed' ORDER BY id DESC LIMIT 1",
        (project_id,)
    ).fetchone()
    conn.close()
    
    if r:
        data = json.loads(r["data"]) if r["data"] else {}
        assignments = data.get("scene_images", [])
        return {
            "success": True,
            "data": {"assignments": assignments}
        }
    
    return {"success": True, "data": {"assignments": []}}

# ═══════════════════════════════════════════════════════════
# 7. auth/change-password — 修改密码
# ═══════════════════════════════════════════════════════════
@router.post("/auth/change-password")
async def change_password(request: Request):
    user_id = get_user_id(request)
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录")
    
    body = await request.json()
    old_pwd = body.get("old", "")
    new_pwd1 = body.get("new1", "")
    new_pwd2 = body.get("new2", "")
    
    if not old_pwd or not new_pwd1 or not new_pwd2:
        return JSONResponse({"detail": "请填写完整"}, status_code=400)
    
    if new_pwd1 != new_pwd2:
        return JSONResponse({"detail": "两次密码不一致"}, status_code=400)
    
    if len(new_pwd1) < 6:
        return JSONResponse({"detail": "密码至少6位"}, status_code=400)
    
    # 查旧密码
    user = fetchone("SELECT password FROM users WHERE id=?", (user_id,))
    if not user:
        return JSONResponse({"detail": "用户不存在"}, status_code=404)
    
    stored_pw = user["password"]
    # 验证旧密码（兼容 bcrypt 和旧格式）
    old_valid = False
    if stored_pw.startswith("$2"):
        try:
            old_valid = bcrypt.checkpw(old_pwd.encode(), stored_pw.encode())
        except:
            old_valid = False
    elif len(stored_pw) == 64:
        old_valid = hashlib.sha256(old_pwd.encode()).hexdigest() == stored_pw
    elif len(stored_pw) == 32:
        old_valid = hashlib.md5(old_pwd.encode()).hexdigest() == stored_pw
    else:
        old_valid = old_pwd == stored_pw
    
    if not old_valid:
        return JSONResponse({"detail": "原密码错误"}, status_code=400)
    
    # 更新为新密码（bcrypt）
    new_hash = bcrypt.hashpw(new_pwd1.encode(), bcrypt.gensalt(rounds=12)).decode()
    execute("UPDATE users SET password=? WHERE id=?", (new_hash, user_id))
    
    return JSONResponse({"success": True})
