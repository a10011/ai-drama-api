"""项目 CRUD + 进度 + 余额校验 — 2026-06-27"""
import json, time, logging, random, string
from fastapi import APIRouter, Request
from app_db import fetchone, fetchall, execute, project_steps
from utils.auth_util import get_user_id
from utils.path_util import local_path_to_url

logger = logging.getLogger("api.projects")
router = APIRouter(prefix="/api/v1/projects", tags=["项目"])

@router.post("")
async def create_project(request: Request, data: dict = {}):
    user_id = get_user_id(request)
    title = data.get("title", "")
    script = data.get("script", "") or data.get("script_text", "")
    genre = data.get("genre", "")
    characters_raw = data.get("characters", "[]")
    # Normalize characters to JSON string for DB
    if isinstance(characters_raw, list):
        characters = json.dumps(characters_raw, ensure_ascii=False)
    elif isinstance(characters_raw, str):
        characters = characters_raw
    else:
        characters = "[]"
    steps = data.get("pipeline_steps", json.dumps(project_steps))

    # ── 如果提供了 project_id 且属于当前用户 → 更新已有项目，不扣款 ──
    existing_id = data.get("project_id")
    if existing_id:
        existing = fetchone("SELECT id, user_id FROM projects WHERE id=? AND user_id=?", (existing_id, user_id))
        if existing:
            now = time.time()
            updates = []
            params = []
            for field, val in [("title", title), ("script", script), ("genre", genre),
                               ("characters", characters), ("pipeline_steps", steps)]:
                if val:
                    updates.append(f"{field}=?")
                    params.append(val)
            if updates:
                updates.append("updated=?")
                params.append(now)
                params.append(existing_id)
                execute(f"UPDATE projects SET {','.join(updates)} WHERE id=?", params)
                logger.info(f"项目更新 user_id={user_id} project_id={existing_id}")
            return {"success": True, "id": existing_id, "project_id": existing_id, "updated": True}
        # project_id 不存在或不属于当前用户 → 回退到创建新项目（见下方）

    # ── 余额校验（2026-06-27） ──
    try:
        chars_list = json.loads(characters) if characters else []
        character_count = len(chars_list) if isinstance(chars_list, list) else 0
    except (json.JSONDecodeError, TypeError):
        character_count = 0
    scene_count = int(data.get("scene_count", 0) or 0)
    video_duration = int(data.get("video_duration_per_scene", 0) or 5)

    from routers.billing import check_and_deduct_for_project
    balance_check = check_and_deduct_for_project(
        user_id=user_id,
        script_text=script,
        character_count=character_count,
        scene_count=scene_count,
        video_duration_per_scene=video_duration,
    )

    if not balance_check["ok"]:
        logger.warning(f"创建项目余额不足 user_id={user_id} required={balance_check['estimated_cost']} balance={balance_check['balance']}")
        return {
            "success": False,
            "error": "余额不足",
            "data": {
                "balance": balance_check["balance"],
                "required": balance_check["estimated_cost"],
                "shortfall": balance_check["shortfall"],
                "message": balance_check["message"],
            },
            "status_code": 402,
        }

    now = time.time()
    pid = execute(
        "INSERT INTO projects (title,script,genre,characters,pipeline_steps,status,created,updated,user_id) VALUES (?,?,?,?,?,'active',?,?,?)",
        (title, script, genre, characters, steps, now, now, user_id)
    )
    # 自动生成订单ID，写入预估成本
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    order_id = f"ORD-{time.strftime('%Y%m%d')}-{suffix}"
    execute(
        "INSERT INTO orders (id,project_id,user_id,title,estimated_cost,status,created,updated) VALUES (?,?,?,?,?,'pending',?,?)",
        (order_id, pid, user_id, title, balance_check["estimated_cost"], now, now)
    )
    logger.info(f"项目创建+扣款成功 user_id={user_id} project_id={pid} cost={balance_check['estimated_cost']}")
    return {"success": True, "id": pid, "project_id": pid, "order_id": order_id, "estimated_cost": balance_check["estimated_cost"]}

@router.post("/create")
async def create_project_alias(request: Request, data: dict = {}):
    return await create_project(request, data)

@router.get("")
async def list_projects(request: Request, status: str = None):
    user_id = get_user_id(request)
    rows = fetchall("SELECT id,title,genre,progress,status,created,project_code FROM projects WHERE user_id=? ORDER BY id DESC LIMIT 20", (user_id,))
    result = []
    for r in rows:
        pid = r.get("id")
        p = dict(r)
        # status 筛选：incomplete = 状态不是 completed
        if status == 'incomplete' and p.get('status') == 'completed':
            continue
        pipe = fetchone("SELECT id, status, progress FROM pipelines WHERE project_id=? ORDER BY created DESC LIMIT 1", (str(pid),))
        if pipe:
            p["pipeline_id"] = pipe["id"]
            p["pipeline_status"] = pipe["status"]
            p["pipeline_progress"] = pipe["progress"]
        else:
            p["pipeline_id"] = ""
            p["pipeline_status"] = "none"
            p["pipeline_progress"] = 0
        result.append(p)
    return {"success": True, "projects": result}

@router.get("/{project_id}")
async def get_project(project_id: int, request: Request = None):
    user_id = get_user_id(request) if request else 0
    if user_id > 0:
        row = fetchone("SELECT id,title,genre,status,progress,created_at,user_id,script,characters FROM projects WHERE id=? AND user_id=?", (project_id, user_id))
    else:
        row = fetchone("SELECT id,title,genre,status,progress,created_at,user_id,script,characters FROM projects WHERE id=?", (project_id,))
    if not row:
        return {"success": False, "error": "项目不存在"}
    return {"success": True, "project": row}

@router.get("/{project_id}/progress")
async def get_progress(project_id: int, request: Request = None):
    user_id = get_user_id(request) if request else 0
    if user_id > 0:
        row = fetchone("SELECT id,title,genre,status,progress,created_at,user_id,script,characters FROM projects WHERE id=? AND user_id=?", (project_id, user_id))
    else:
        row = fetchone("SELECT id,title,genre,status,progress,created_at,user_id,script,characters FROM projects WHERE id=?", (project_id,))
    if not row:
        return {"success": False, "error": "项目不存在"}
    steps = json.loads(row["pipeline_steps"] or "[]")
    # 查视频输出URL（用于下载）
    video_url = ""
    try:
        pipe = fetchone("SELECT id FROM pipelines WHERE project_id=? ORDER BY created DESC LIMIT 1", (str(project_id),))
        if pipe:
            from routers.pipeline import _aggregate_progress_data
            agg = _aggregate_progress_data(str(project_id))
            video_url = agg.get("final_video_url", "")
            if video_url and video_url.startswith('/www/wwwroot/'):
                video_url = local_path_to_url(video_url)
    except Exception as ex_: logger.warning(f"[projects]  {ex_}")

    return {
        "success": True,
        "title": row["title"] or "",
        "script": row["script"] or "",
        "genre": row["genre"] or "",
        "characters": json.loads(row["characters"] or "[]"),
        "progress": row["progress"] or 0,
        "status": row["status"] or "",
        "steps": steps,
        "video_url": video_url
    }

@router.put("/{project_id}/characters")
async def update_characters(project_id: int, request: Request):
    """前端换脸/编辑后同步角色数据到DB"""
    try:
        user_id = get_user_id(request)
        row = fetchone("SELECT user_id FROM projects WHERE id=?", (project_id,))
        if row and user_id > 0 and row["user_id"] and row["user_id"] != user_id:
            return {"success": True}  # 静默拒绝对其他用户的编辑
        body = await request.json()
        chars = body.get("characters", [])
        if chars:
            execute("UPDATE projects SET characters=? WHERE id=?", (json.dumps(chars, ensure_ascii=False), project_id))
        return {"success": True}
    except Exception as e:
        return {"success": True}  # 静默失败不打扰前端

@router.put("/{project_id}")
async def update_project(project_id: int, data: dict = {}, request: Request = None):
    # 会员数据隔离：检查归属
    if request:
        user_id = get_user_id(request)
        if user_id > 0:
            row = fetchone("SELECT user_id FROM projects WHERE id=?", (project_id,))
            if not row:
                return {"success": False, "error": "项目不存在"}
            if row["user_id"] and row["user_id"] != user_id:
                return {"success": False, "error": "无权操作"}
    updates = []
    params = []
    for field in ["title","script","genre","characters","storyboard","pipeline_steps","current_step","status"]:
        if field in data and data[field] is not None:
            updates.append(f"{field}=?")
            params.append(data[field])
    if not updates:
        return {"success": False, "error": "无更新字段"}
    updates.append("updated=?")
    params.append(time.time())
    params.append(project_id)
    execute(f"UPDATE projects SET {','.join(updates)} WHERE id=?", params)
    return {"success": True}


@router.delete("/{project_id}")
async def delete_project(project_id: int, request: Request):
    user_id = get_user_id(request)
    if user_id <= 0:
        return {"success": False, "error": "请先登录"}
    row = fetchone("SELECT id, user_id FROM projects WHERE id=?", (project_id,))
    if not row:
        return {"success": False, "error": "项目不存在"}
    if row["user_id"] != user_id:
        return {"success": False, "error": "无权操作"}
    execute("DELETE FROM pipelines WHERE project_id=?", (str(project_id),))
    execute("DELETE FROM pipeline_progress WHERE project_id=?", (str(project_id),))
    execute("DELETE FROM projects WHERE id=?", (project_id,))
    return {"success": True, "message": "已删除"}
