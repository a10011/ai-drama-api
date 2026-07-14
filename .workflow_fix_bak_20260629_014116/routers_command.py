"""统一指令接口 — 2026-06-18 修复用户隔离"""
import json, time, logging, importlib, os, re as _re, glob, random
from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional
from app_db import fetchone, fetchall, execute, project_steps
from utils.auth_util import get_user_id

logger = logging.getLogger("api.command")
router = APIRouter(prefix="/api/v1", tags=["指令"])

class CommandRequest(BaseModel):
    action: str
    params: dict = {}

def _load(aid):
    try: m = importlib.import_module(f"agents.agent_{aid}"); return getattr(m, f"{aid.capitalize()}Agent")()
    except Exception: return None

def _call_agent(aid, action, **kw):
    a = _load(aid)
    if not a: return None
    try:
        r = a.run(action=action, **kw)
        return r.data if r and r.success else None
    except Exception: return None

def _llm_chat(system, user, temp=0.3, timeout=45, retries=2):
    """直接调 DeepSeek V4 返回 JSON，失败自动重试"""
    last_raw = ""
    for attempt in range(retries + 1):
        try:
            from ai_base import ai as _ai
            raw = _ai.llm(system, user, temp=temp, timeout=timeout)
            last_raw = raw[:200] if raw else ""
            m = _re.search(r'\{.*\}', raw, _re.DOTALL)
            if m:
                return json.loads(m.group(0))
            m = _re.search(r'\[.*\]', raw, _re.DOTALL)
            if m:
                return json.loads(m.group(0))
            logger.warning(f"_llm_chat parse fail attempt={attempt+1} raw_preview={last_raw[:100]}")
        except json.JSONDecodeError as e:
            logger.warning(f"_llm_chat JSON decode error attempt={attempt+1}: {e}")
        except Exception as e:
            logger.warning(f"_llm_chat error attempt={attempt+1}: {e}")
        if attempt < retries:
            time.sleep(2)
    logger.error(f"_llm_chat all {retries+1} attempts failed, last_raw={last_raw[:100]}")
    return None

@router.post("/command")
async def handle(req: CommandRequest, request: Request):
    action = req.action
    p = req.params or {}
    user_id = get_user_id(request)
    logger.info(f"command: {action} user_id={user_id} {json.dumps(p, ensure_ascii=False)[:200]}")

    if action == "generate_script":
        premise = p.get("premise", "")
        title = p.get("title", "")
        genre = p.get("genre", "都市")
        if not premise:
            return {"success": False, "error": "剧本不能为空"}
        
        # 1. 调剧本智能体生成 (use polish for long premises to preserve original text)
        script_agent = _load("script")
        if len(premise.strip()) > 30:
            script_result = script_agent.run(action="polish", script_text=premise, title=title, genre=genre)
        else:
            script_result = script_agent.run(action="create", premise=premise, title=title, genre=genre)
        script_data = script_result.data if script_result.success else {}
        full_script = script_data.get("outline", "") + "\n" + json.dumps(script_data.get("scenes", []), ensure_ascii=False)
        
        # 2. 第二次调 LLM：一次性提取角色+场景
        # 先保留 script_agent 第一轮输出的角色（不丢弃）
        enriched_chars = script_data.get("characters", [])
        scenes = script_data.get("scenes", [])
        system_p = "你是一个专业短剧分析器。根据输入的剧本，返回JSON格式，包含characters数组(每个含name/gender/age/personality/appearance/role_type，至少3个)、scenes数组(每个含scene_num/location/atmosphere/dialogue/action)、outline(剧情概要)"
        user_p = f"剧本：{premise[:3000]}\n\n请提取：\n1. 所有角色（至少3个，含主角配角反派）\n2. 分场场景\n3. 剧情概要\nJSON格式：{{\"characters\":[...],\"scenes\":[...],\"outline\":\"...\"}}"
        second = _llm_chat(system_p, user_p, timeout=90, retries=2)
        # 如果失败或角色太少，重试
        if not second or len(second.get("characters", [])) < 3:
            if second and second.get("characters"):
                logger.warning(f"  -> 角色不足 ({len(second['characters'])}个)，加强重试...")
            else:
                logger.warning(f"  -> 提取失败/无角色，重试...")
            # 加强版 prompt
            sp2 = "你是一个专业短剧编剧。根据剧本完整提取所有角色，必须返回至少3个角色（主角+配角+反派），不能少！返回纯JSON。"
            up2 = f'剧本：{premise[:3000]}\n\n必须提取至少3个角色，每个角色必须完整！JSON：{{"characters":[{{"name":"XX","gender":"男","age":"25","personality":"...","appearance":"...","role_type":"主角/配角/反派"}},...],"scenes":[...],"outline":"..."}}'
            retry_result = _llm_chat(sp2, up2, timeout=90, retries=1)
            if retry_result:
                second = retry_result
                logger.info(f"  -> 加强重试成功: {len(retry_result.get('characters',[]))}角色")
        
        if second:
            chars = second.get("characters", [])
            # 如果还不够3个，从剧本原文里捞角色名（对话标记、出场人物）
            if len(chars) < 3:
                logger.warning(f"  -> 角色仍不足 ({len(chars)}个)，从剧本原文提取...")
                existing_names = {c.get("name","") for c in chars}
                # 从剧本中提取对话中的说话人（"XX：" 或 "XX说"）
                import re as _re2
                speakers = set()
                for m in _re2.finditer(r'([^\s：:，,。！!\n]{2,4})[：:]', premise[:3000]):
                    name = m.group(1)
                    if name not in existing_names and len(name) >= 2:
                        speakers.add(name)
                for m in _re2.finditer(r'([^\s：:，,。！!\n]{2,4})说[：:]?', premise[:3000]):
                    name = m.group(1)
                    if name not in existing_names and len(name) >= 2:
                        speakers.add(name)
                logger.info(f"  -> 剧本中检测到对话角色: {speakers}")
                
                role_types = ["配角", "反派", "配角"]
                idx = 0
                for sp_name in list(speakers)[:3 - len(chars)]:
                    chars.append({
                        "name": sp_name,
                        "gender": "未知",
                        "age": "未知",
                        "personality": "待分析",
                        "appearance": "待设定",
                        "role_type": role_types[idx] if idx < len(role_types) else "配角"
                    })
                    idx += 1
                    logger.info(f"  -> 从剧本补充角色: {sp_name}")
                
                second["characters"] = chars
                # 如果还是不够，记警告但不塞假数据
                if len(chars) < 3:
                    logger.warning(f"  -> ⚠️ 最终角色数 {len(chars)}，不足3个（剧本可能角色较少）")
            
            if chars:
                enriched_chars = chars
            if second.get("scenes"):
                scenes = second.get("scenes")
            if second.get("outline"):
                script_data["outline"] = second["outline"]
            logger.info(f"  -> 二次提取: {len(enriched_chars)}角色 {len(scenes)}场景")
        
        # 归一化 role_type 和 gender (ensure always runs)
        _role_map = {"protagonist":"主角","main":"主角","hero":"主角","heroine":"主角","supporting":"配角","support":"配角","side":"配角","antagonist":"反派","villain":"反派","rival":"反派"}
        _gender_map = {"male":"男","female":"女","man":"男","woman":"女","m":"男","f":"女"}
        for _c in enriched_chars:
            _rt = str(_c.get("role_type","")).lower().strip()
            if not _rt or _rt == "none":
                _c["role_type"] = "配角"
            elif _rt not in ("主角","配角","反派"):
                _c["role_type"] = _role_map.get(_rt, "配角")
            _g = str(_c.get("gender","")).lower().strip()
            if _g and _g not in ("男","女"):
                _c["gender"] = _gender_map.get(_g, _g)

        # 3. 自动创建项目（使用真实 user_id）
        order_id = time.strftime("ORD%Y%m%d") + "".join(random.choices("ABCDEFGHJKLMNPQRSTUVWXYZ23456789", k=6))
        pid = execute("INSERT INTO projects (title,script,genre,pipeline_steps,status,created,updated,order_id,user_id) VALUES (?,?,?,?,'active',?,?,?,?)",
            (title or script_data.get("title","短剧"), premise, genre, json.dumps(project_steps()), time.time(), time.time(), order_id, user_id))
        
        # Save characters to DB immediately
        if enriched_chars:
            try:
                chars_json = json.dumps(enriched_chars, ensure_ascii=False)
                execute("UPDATE projects SET characters=? WHERE id=?", (chars_json, pid))
                logger.info(f"  -> Characters saved to project {pid}: {len(enriched_chars)} chars")
            except Exception as e:
                logger.warning(f"  -> Failed to save characters to DB: {e}")
        
        return {"success": True, "project_id": pid, "order_id": order_id, "title": script_data.get("title", title), "script": script_data.get("script", script_data.get("outline","")),
                "genre": script_data.get("genre", genre), "outline": script_data.get("outline",""),
                "characters": enriched_chars, "scenes": scenes,
                "episodes": script_data.get("episodes",[]), "tasks": script_data.get("tasks",[]),
                "duration_ms": script_result.duration_ms}
    
    elif action == "generate_storyboard":
        project_id = p.get("project_id", 0)
        script = p.get("script", "")
        characters = p.get("characters", [])
        title = p.get("title", "")
        genre = p.get("genre", "")
        result = _call_agent("storyboard", "generate", script=script, characters=characters, title=title, genre=genre)
        return {"success": True, "shots": result.get("shots",[]) if result else []}
    
    elif action == "start_pipeline":
        from routers.pipeline import _executor, _run_with_orchestrator, STAGE_ICONS as _SICONS, DB_PATH as _DBP
        from app_db import execute as db_execute
        import sqlite3 as _sqlite3
        project_id_in = str(p.get("project_id", "default"))
        full_script = ""
        script_text = p.get("script_text", "")
        raw_genre = p.get("genre", "都市")
        title = p.get("title", "")
        genre = raw_genre
        if (not raw_genre or raw_genre == "都市") and title.strip():
            import re as _re2
            for kw in ["古装","古代","仙侠","武侠","宫斗","穿越","玄幻","悬疑","科幻","恐怖","民国"]:
                if kw in title:
                    genre = kw
                    break
        pid = f"pipe_{int(time.time())}_{project_id_in[:16]}"
        _conn = _sqlite3.connect(_DBP, timeout=10)
        _c = _conn.cursor()
        _c.execute("SELECT id, title FROM projects WHERE (title=? OR id=?) AND user_id=?", (project_id_in, project_id_in, user_id))
        _row = _c.fetchone()
        if not _row:
            all_stages = ["导演分析", "剧本创作", "角色设计", "分镜生成", "场景生成", "配音合成", "字幕生成", "BGM配乐", "视频生成", "视频合成"]
            steps = [{"icon": _SICONS.get(s, ""), "label": s, "desc": "", "status": "idle", "progress": 0, "duration": "", "log": ""} for s in all_stages]
            _c.execute("INSERT INTO projects (title,script,pipeline_steps,progress,status,user_id) VALUES (?,?,?,0,'active',?)",
                      (title or genre or "短剧", script_text[:500], json.dumps(steps, ensure_ascii=False), user_id))
            project_id = str(_c.lastrowid)
        else:
            project_id = str(_row["id"])
        try:
            _c.execute("INSERT INTO pipelines (id,project_id,script_text,genre,status,created,updated,user_id) VALUES (?,?,?,?,'pending',?,?,?)",
                      (pid, project_id, script_text[:1000], genre, time.time(), time.time(), user_id))
            _conn.commit()
        except Exception as e:
            logger.warning(f"insert pipeline row failed: {e}")
        _conn.close()
        _executor.submit(_run_with_orchestrator, pid, project_id, genre, p.get('characters', []), p.get('max_shots', 8), script_text)
        return {"success": True, "pipeline_id": pid, "project_id": project_id, "video_url": ""}
    
    elif action == "get_progress":
        project_id = p.get("project_id", 0)
        pipeline_id = p.get("pipeline_id", "")
        row = fetchone("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user_id))
        if not row:
            if pipeline_id:
                pipe_row = fetchone("SELECT project_id, user_id FROM pipelines WHERE id=?", (pipeline_id,))
                if pipe_row:
                    pid2 = pipe_row[0] if isinstance(pipe_row, (tuple, list)) else (pipe_row["project_id"] if isinstance(pipe_row, dict) else "")
                    pipe_user = pipe_row[1] if isinstance(pipe_row, (tuple, list)) and len(pipe_row) > 1 else (pipe_row.get("user_id", 0) if isinstance(pipe_row, dict) else 0)
                    if pid2:
                        row = fetchone("SELECT * FROM projects WHERE id=? AND user_id=?", (pid2, user_id))
                        if not row:
                            row = fetchone("SELECT * FROM projects WHERE title=? AND user_id=?", (pid2, user_id))
            if not row:
                row = fetchone("SELECT * FROM projects WHERE status='active' AND user_id=? ORDER BY id DESC LIMIT 1", (user_id,))
            if not row:
                return {"success": False, "error": "项目不存在"}
        steps = json.loads(row.get("pipeline_steps") or "[]") if row else []
        stage_previews = {}
        if pipeline_id:
            try:
                pipe_row = fetchone("SELECT step_results FROM pipelines WHERE id=? AND user_id=?", (pipeline_id, user_id))
                if pipe_row:
                    sr = json.loads(pipe_row.get("step_results", "{}") or "{}")
                    for key, val in sr.items():
                        d = val.get("data", {}) if isinstance(val, dict) else {}
                        if key == "character_result":
                            urls = [c.get("figure_url", c.get("photo_url", c.get("image_url", ""))) for c in (d.get("characters") or [])]
                            char_imgs = d.get("char_images", {}) or {}
                            urls += [v for v in char_imgs.values() if v]
                            urls = [u for u in urls if u]
                            if urls:
                                stage_previews["角色设计"] = {"type": "image", "urls": urls}
                        if key == "storyboard_result" and d.get("shots"):
                            stage_previews["分镜生成"] = {"type": "image", "urls": [s.get("image_url", "") for s in d["shots"] if s.get("image_url")]}
                        if key == "scene_result":
                            scene_imgs = d.get("images", d.get("image_map", {}))
                            if isinstance(scene_imgs, dict):
                                urls = [v for v in scene_imgs.values() if v and isinstance(v, str)]
                            elif isinstance(scene_imgs, list):
                                urls = [s.get("image_url", s.get("url", "")) for s in scene_imgs if s.get("image_url") or s.get("url")]
                            else:
                                urls = []
                            if urls:
                                stage_previews["场景生成"] = {"type": "image", "urls": urls}
                        if key == "composite_result":
                            v_url = d.get("video_url") or d.get("output", "")
                            if v_url:
                                if not v_url.startswith("http"):
                                    basename = v_url.split("/")[-1] if "/" in v_url else v_url.split("\\")[-1]
                                    subfolder = ""
                                    if "/videos/" in v_url:
                                        subfolder = "videos/"
                                    elif "/figures/" in v_url:
                                        subfolder = "figures/"
                                    elif "/scenes/" in v_url:
                                        subfolder = "scenes/"
                                    elif "/audio/" in v_url:
                                        subfolder = "audio/"
                                    elif "/bgm/" in v_url:
                                        subfolder = "bgm/"
                                    v_url = f"https://ai.mzsh.top/storage/{subfolder}{basename}"
                                stage_previews["视频合成"] = {"type": "video", "urls": [v_url]}
            except Exception as e:
                logger.warning(f"[get_progress] step_results parse: {e}")
        pipeline_status = ""
        pipeline_video_url = ""
        shot_info = {}
        if pipeline_id:
            try:
                pr = fetchone("SELECT status, step_results FROM pipelines WHERE id=? AND user_id=?", (pipeline_id, user_id))
                if pr:
                    pipeline_status = pr.get("status", "") if isinstance(pr, dict) else ""
                    if pipeline_status in ("completed", "done", "success"):
                        pipeline_status = "done"
                    # 提取Shot进度（中间状态，视频智能体每完成一镜就写入）
                    try:
                        sr_json = json.loads(pr.get("step_results", "{}") or "{}")
                        shot_info = sr_json.get("shot_progress", {})
                    except Exception as ex_: logger.warning(f"[command]  {ex_}")
            except Exception as ex_: logger.warning(f"[command]  {ex_}")
        return {"success": True, "progress": row.get("progress", 0) if row else 0, "steps": steps, "previews": stage_previews, "pipeline_status": pipeline_status, "pipeline_id": pipeline_id, "shot_progress": shot_info}
    
    elif action == "get_status":
        pipeline_id = p.get("pipeline_id", "")
        row = fetchone("SELECT * FROM pipelines WHERE id=? AND user_id=?", (pipeline_id, user_id))
        if not row: return {"success": False, "error": "流水线不存在"}
        video_url = ""
        try:
            pid_from_pipe = row.get("project_id", "") if isinstance(row, dict) else ""
            if pid_from_pipe:
                ord_row = fetchone("SELECT order_id FROM projects WHERE id=?", (pid_from_pipe,))
                ord_id = ord_row[0] if ord_row and ord_row[0] else ""
                if ord_id:
                    fp = f"/www/wwwroot/storage/{ord_id}.mp4"
                    if os.path.exists(fp):
                        video_url = f"https://ai.mzsh.top/storage/{ord_id}.mp4"
            if not video_url:
                parts = pipeline_id.split("_")
                pid_last = parts[-1] if parts else ""
                matches = glob.glob(f"/www/wwwroot/storage/*_{pid_last}.mp4")
                if matches:
                    video_url = f"https://ai.mzsh.top/storage/{os.path.basename(matches[0])}"
        except Exception as ex_: logger.warning(f"[command]  {ex_}")
        return {"success": True, "status": row.get("status", ""), "progress": row.get("progress", 0), 
                "current_stage": row.get("current_stage", ""), "video_url": video_url}

    elif action == "get_achievements":
        # 按用户统计成就
        if user_id > 0:
            count_all = fetchone("SELECT COUNT(*) as cnt FROM projects WHERE user_id=?", (user_id,))
            count_done = fetchone("SELECT COUNT(*) as cnt FROM projects WHERE user_id=? AND status='active' AND CAST(progress AS INTEGER)>=100", (user_id,))
            genre_rows = fetchall("SELECT DISTINCT genre FROM projects WHERE user_id=? AND genre!='' AND genre IS NOT NULL", (user_id,)) or []
        else:
            count_all = fetchone("SELECT COUNT(*) as cnt FROM projects WHERE user_id=0")
            count_done = fetchone("SELECT COUNT(*) as cnt FROM projects WHERE user_id=0 AND status='active' AND CAST(progress AS INTEGER)>=100")
            genre_rows = fetchall("SELECT DISTINCT genre FROM projects WHERE user_id=0 AND genre!='' AND genre IS NOT NULL") or []
        total_projects = count_all["cnt"] if count_all else 0
        total_completed = count_done["cnt"] if count_done else 0
        genres = [r["genre"] for r in genre_rows]
        
        achievements = [
            {"id": "first_drama", "name": "初露锋芒", "icon": "🎬", "desc": "创作你的第一部短剧", "unlocked": total_completed >= 1},
            {"id": "five_dramas", "name": "小有成就", "icon": "🏆", "desc": "累计创作5部短剧", "unlocked": total_completed >= 5, "progress": min(total_completed, 5), "max": 5},
            {"id": "ten_dramas", "name": "创作达人", "icon": "👑", "desc": "累计创作10部短剧", "unlocked": total_completed >= 10, "progress": min(total_completed, 10), "max": 10},
            {"id": "vip_master", "name": "尊贵会员", "icon": "💎", "desc": "成为VIP会员", "unlocked": True},
            {"id": "genre_urban", "name": "都市编导", "icon": "🏙️", "desc": "完成一部都市题材短剧", "unlocked": "都市" in genres},
            {"id": "genre_xianxia", "name": "仙侠大师", "icon": "⚔️", "desc": "完成一部仙侠题材短剧", "unlocked": "修仙" in genres or "仙侠" in genres},
            {"id": "genre_suspense", "name": "悬疑推理", "icon": "🔍", "desc": "完成一部悬疑题材短剧", "unlocked": "悬疑" in genres},
            {"id": "genre_sweet", "name": "甜宠编剧", "icon": "💕", "desc": "完成一部甜宠题材短剧", "unlocked": "甜宠" in genres or "爱情" in genres},
            {"id": "fast_pipeline", "name": "闪电制作", "icon": "⚡", "desc": "在60秒内完成一部短剧生成", "unlocked": False},
            {"id": "one_hundred_percent", "name": "完美之作", "icon": "✨", "desc": "获得一次100%完成度", "unlocked": total_completed >= 1},
            {"id": "serial_creator", "name": "系列出品", "icon": "📺", "desc": "连续创作3部同题材短剧", "unlocked": False},
        ]
        unlocked_count = sum(1 for a in achievements if a.get("unlocked"))
        return {"success": True, "achievements": achievements, "unlocked": unlocked_count, "total": len(achievements)}
    
    elif action == "save_characters":
        project_id = p.get("project_id", 0)
        chars = p.get("characters", [])
        # 数据隔离：校验项目归属
        owner = fetchone("SELECT user_id FROM projects WHERE id=?", (project_id,))
        if owner and user_id > 0 and owner["user_id"] and owner["user_id"] != user_id:
            return {"success": True}  # 静默拒绝
        execute("UPDATE projects SET characters=?, updated=? WHERE id=?", (json.dumps(chars, ensure_ascii=False), time.time(), project_id))
        return {"success": True}
    
    elif action == "get_characters":
        project_id = p.get("project_id", 0)
        row = fetchone("SELECT * FROM projects WHERE id=? AND user_id=?", (project_id, user_id))
        chars = json.loads(row.get("characters") or "[]") if row else []
        return {"success": True, "characters": chars}
    
    # ── 模型情报 ──
    if action == "model_intel":
        try:
            from ai_base import ai as _ai
            try:
                with open('/www/wwwroot/api.mzsh.top/model_controller.py') as _f:
                    _config = _f.read()
            except Exception:
                _config = "未读取到配置"
            prompt = f"""你是一位短剧模型配置顾问。我们已对接以下模型，请为动漫剧创作搭配最优方案。

已对接的模型配置：
{_config[:3000]}

请基于以上配置，为动漫剧创作给出完整方案（用JSON返回）：
1. 剧本创作 → 用哪个模型？为什么？
2. 角色设计/换脸 → 用哪个？
3. 分镜生图 → 用哪个？
4. 配音 → 用哪个？
5. BGM → 用哪个？
6. 视频合成/口型同步 → 用哪个？

返回格式：
{{
  "analysis": "整体分析（中文）",
  "pipeline": [
    {{"step": "步骤名", "model": "模型名", "reason": "选择理由"}}
  ],
  "total_cost_estimate": "单集预估成本",
  "optimization_tips": ["优化建议1", "优化建议2"]
}}"""
            reply = _ai.llm(prompt, "请分析当前模型配置并给出优化建议。", temp=0.4, timeout=60)
            return {"success": True, "reply": reply, "role": "analyst"}
        except Exception as e:
            return {"success": False, "error": f"情报分析失败: {e}"}
    
    # ── 导演对话 ──
    if action == "chat":
        message = p.get("message", "")
        if not message:
            return {"success": False, "error": "消息不能为空"}
        director = _load("director")
        if not director:
            try:
                from ai_base import ai as _ai
                reply = _ai.llm(
                    "你是一位短剧导演AI助手，回答用户关于短剧创作的问题。如果用户想创作短剧，引导他们提供剧本内容。",
                    message, temp=0.7, timeout=30
                )
                return {"success": True, "reply": reply, "role": "director"}
            except Exception as e:
                return {"success": False, "error": f"导演不在线: {e}"}
        try:
            result = director.run(action="chat", message=message)
            if result and result.success:
                reply = result.data.get("reply", str(result.data))
                if any(kw in message for kw in ["生成", "开始", "做吧", "确认", "好", "可以", "go", "start"]):
                    try:
                        if len(message) > 20:
                            from ai_base import ai as _ai
                            analysis = _ai.llm(
                                "提取用户想创作的短剧信息。返回JSON: {\"premise\": \"剧情梗概\", \"title\": \"标题\", \"genre\": \"类型\"}",
                                f"用户需求：{message}\n导演建议：{reply[:500]}",
                                temp=0.2, timeout=20
                            )
                            m = _re.search(r'\{.*\}', analysis, _re.DOTALL)
                            if m:
                                info = json.loads(m.group(0))
                                result2 = await handle(CommandRequest(action="generate_script", params={
                                    "premise": info.get("premise", message[:500]),
                                    "title": info.get("title", "未命名"),
                                    "genre": info.get("genre", "都市")
                                }), request)
                                return {"success": True, "reply": reply + "\n\n🎬 好的，已开始为你生成剧本！请稍候...", "role": "director", "auto_started": True}
                    except Exception as e:
                        logger.warning(f"自动生成失败: {e}")
                return {"success": True, "reply": reply, "role": "director"}
        except Exception as e:
            logger.warning(f"导演chat失败: {e}")
        try:
            from ai_base import ai as _ai
            reply = _ai.llm(
                "你是一位专业短剧导演AI助手。你负责帮助用户创作短剧。\n当前有这些能力：\n1. 分析剧本并生成角色\n2. 生成分镜头\n3. 一键生成完整短剧\n请根据用户需求引导他们。",
                message, temp=0.7, timeout=30
            )
            return {"success": True, "reply": reply, "role": "director"}
        except Exception as e:
            return {"success": False, "error": f"导演回复失败: {e}"}
    
    elif action == "agents_execute":
        # proxied from frontend /agents/execute → action=agents_execute
        # extract actual params from p
        agent = p.get("agent_id", "")
        agent_action = p.get("action", "")
        agent_params = p.get("params", {})
        logger.info(f"agents_execute: agent={agent}, action={agent_action}")
        import traceback
        
        if agent == "character" and agent_action in ("beautify", "generate_figure"):
            # Rewrite params to match generate_portrait format
            char_name = agent_params.get("char_name", "")
            gender = agent_params.get("gender", "")
            description = agent_params.get("description", "")
            ref_image = agent_params.get("ref_image", "")
            age = agent_params.get("age", "")
            style = agent_params.get("style", "")
            # Rebuild p params for generate_portrait handler
            p["name"] = char_name
            p["gender"] = gender
            p["description"] = description
            p["reference_image"] = ref_image
            p["age"] = age
            p["genre"] = style
            # Fall through to generate_portrait logic by setting action
            action = "generate_portrait"
            logger.info(f"agents_execute -> generate_portrait: name={char_name}, ref={'yes' if ref_image else 'no'}")

        elif agent == "character" and agent_action == "extract":
            action = "extract_characters"
            
        else:
            return {"success": False, "error": f"未知智能体指令: agent={agent}, action={agent_action}"}
    
    elif action == "generate_portrait":
        name = p.get("name", "角色")
        gender = p.get("gender", "男")
        description = p.get("description", "")
        reference_image = p.get("reference_image", "")
        age = p.get("age", "")
        try:
            from agents.agent_character import CharacterAgent
            from agents.route_manager import run_with_fallback
            ca = CharacterAgent()
            extra = {}
            if reference_image:
                # data: URL → 先存到服务器，再传给图生图模型
                if reference_image.startswith('data:'):
                    try:
                        import base64, os, uuid
                        header, b64data = reference_image.split(',', 1)
                        img_data = base64.b64decode(b64data)
                        # 推断格式
                        ext = 'jpg'
                        if 'png' in header: ext = 'png'
                        elif 'webp' in header: ext = 'webp'
                        fname = f"{uuid.uuid4().hex}.{ext}"
                        fpath = os.path.join('/www/wwwroot/storage/figures', fname)
                        with open(fpath, 'wb') as f:
                            f.write(img_data)
                        ref_url = f"https://ai.mzsh.top/storage/figures/{fname}"
                        logger.info(f"[Portrait] data:URL saved to {ref_url}")
                        # 注册到素材库，归属当前会员
                        try:
                            from services.media_registry import save as _media_save
                            _media_save(img_data, fname, "figures", name=f"上传照片-{name}",
                                       tags=["上传", "换脸参考", name], user_id=user_id, state="private")
                        except Exception as _e:
                            logger.warning(f"[Portrait] media_registry save failed: {_e}")
                    except Exception as e:
                        logger.warning(f"[Portrait] data:URL save failed: {e}")
                        ref_url = reference_image  # fallback
                else:
                    ref_url = reference_image
                extra["reference_image"] = ref_url
            if age:
                extra["age"] = age
            extra["user_id"] = user_id  # 传给 agent，用于结果注册到素材库
            result = ca.run(
                action="generate_figure",
                name=name,
                gender=gender,
                appearance=description,
                outfit=description,
                prompt=description,
                **extra
            )
            if result and result.success:
                url = result.data.get("figure_url", "")
                return {"success": True, "portrait_url": url, "name": name}
            else:
                return {"success": False, "error": f"立绘生成失败: {result.error if result else 'unknown'}"}
        except Exception as e:
            logger.error(f"generate_portrait error: {e}")
            return {"success": False, "error": str(e)}

    return {"success": False, "error": f"未知指令: {action}"}

@router.post("/{action_path:path}")
async def catch_all(action_path: str, req: CommandRequest, request: Request):
    """Catch-all: forward all /api/v1/* requests to handle"""
    logger = logging.getLogger("api.command")
    logger.info(f"catch-all: {action_path} -> {req.action}")
    req.action = action_path.replace("/", "_")
    return await handle(req, request)
