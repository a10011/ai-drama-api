
"""Script Workflow Chat API - dialogue interface"""

import asyncio
import os
import os
import os, logging, json, time, uuid
from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from .workflow_engine import ScriptWorkflowEngine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/script-wf", tags=["script_workflow"])


# ===== init modifications table =====
def _init_modifications_db():
    try:
        from app_db import execute
        execute('''CREATE TABLE IF NOT EXISTS script_modifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            project_id TEXT DEFAULT '',
            version INTEGER DEFAULT 1,
            original_content TEXT DEFAULT '',
            modified_content TEXT DEFAULT '',
            diff_summary TEXT DEFAULT '',
            editor_notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            user_id INTEGER DEFAULT 0
        )''')
        logger.info("[ScriptWf] modifications DB table ready")
    except Exception as e:
        logger.warning(f"[ScriptWf] Init modifications DB failed: {e}")

_init_modifications_db()


@router.get("/ping")
async def ping():
    return {"status": "ok", "message": "ready", "model": "deepseek-reasoner"}


async def _extract_task(raw_message: str, model: str) -> dict:
    """Extract title/genre/synopsis from natural language.
    If input is a full script (long, has dialogue), preserve the full text for direct analysis."""
    from services.model_client import UnifiedModel

    # [P1] Input length limit to prevent prompt injection and API cost control
    max_input_len = int(os.environ.get("MAX_PROMPT_INPUT", "5000"))
    raw_message = raw_message[:max_input_len]

    # Detect if this is a full script (has dialogue markers + substantial length)
    is_full_script = len(raw_message) > 300 and ('：' in raw_message or '说' in raw_message or '【' in raw_message)

    system = """You are a script analyzer. Extract from user input:
- title: script title (concise, 2-10 chars)
- genre: genre (city/xianxia/suspense/sci-fi/romance/revenge/fantasy)
- synopsis: story summary (100-300 chars)

Output strict JSON: {"title":"...","genre":"...","synopsis":"..."}"""

    result = UnifiedModel.llm(
        prompt=f"User input: {raw_message[:3000]}",
        system=system,
        model=model,
        timeout=120,
        max_tokens=4096,
    )

    text = ""
    if isinstance(result, dict):
        text = result.get("text", result.get("content", json.dumps(result)))
    else:
        text = str(result)

    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("Not a dict")
        base = {
            "title": parsed.get("title", "Unnamed"),
            "genre": parsed.get("genre", "city"),
            "synopsis": parsed.get("synopsis", raw_message[:300]),
        }
    except (json.JSONDecodeError, ValueError):
        base = {
            "title": raw_message[:20] + ("..." if len(raw_message) > 20 else ""),
            "genre": "city",
            "synopsis": raw_message[:300],
        }

    # If full script uploaded, pass it through for direct character/scene extraction
    if is_full_script:
        base["full_script"] = raw_message
        base["is_full_script"] = True

    return base


def _save_conversation(session_id: str, user_id: int, messages: list, title: str = "", genre: str = ""):
    """Persist conversation to SQLite"""
    try:
        from app_db import execute, fetchone
        if not session_id:
            session_id = "sw_" + str(int(time.time() * 1000)) + "_" + str(user_id)
        existing = fetchone("SELECT id FROM script_conversations WHERE session_id=?", (session_id,))
        if existing:
            execute(
                "UPDATE script_conversations SET messages=?, title=?, genre=?, updated_at=? WHERE session_id=?",
                (json.dumps(messages), title, genre, time.strftime("%Y-%m-%d %H:%M:%S"), session_id)
            )
        else:
            execute(
                "INSERT INTO script_conversations (session_id, user_id, title, genre, messages, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                (session_id, user_id, title, genre, json.dumps(messages), time.strftime("%Y-%m-%d %H:%M:%S"), time.strftime("%Y-%m-%d %H:%M:%S"))
            )
    except Exception as e:
        logger.warning(f"[ScriptWf] Save conversation failed: {e}")


def _list_conversations(user_id: int) -> list:
    """List user conversations"""
    try:
        from app_db import fetchall
        rows = fetchall(
            "SELECT session_id, title, genre, updated_at FROM script_conversations WHERE user_id=? ORDER BY updated_at DESC LIMIT 30",
            (user_id,)
        )
        result = []
        for row in rows:
            d = dict(row) if hasattr(row, "_mapping") else dict(row)
            result.append({
                "id": d.get("session_id", ""),
                "title": d.get("title", "New script"),
                "genre": d.get("genre", ""),
                "updatedAt": d.get("updated_at", "")[:16] if d.get("updated_at") else "",
            })
        return result
    except Exception as e:
        logger.warning(f"[ScriptWf] List conversations failed: {e}")
        return []


def _get_conversation(session_id: str) -> dict:
    """Get a conversation by session_id"""
    try:
        from app_db import fetchone
        row = fetchone("SELECT * FROM script_conversations WHERE session_id=?", (session_id,))
        if row:
            d = dict(row) if hasattr(row, "_mapping") else dict(row)
            msgs = json.loads(d.get("messages", "[]"))
            return {"id": d["session_id"], "title": d.get("title", ""), "messages": msgs}
    except Exception as e:
        logger.warning(f"[ScriptWf] Get conversation failed: {e}")
    return None


async def _run_workflow(task: dict, mode: str, user_id: int) -> dict:
    """Run the script workflow"""
    engine = ScriptWorkflowEngine(user_id, llm_model=task.get("_llm_model", "deepseek-reasoner"))
    try:
        if mode == "fast":
            result = await engine.run_fast(task)
        else:
            result = await engine.run_precise(task)
    except Exception as e:
        logger.error(f"[ScriptWf] Workflow error: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
    return result


@router.post("/chat")
async def chat(request: Request):
    """Chat interface: receive natural language, return script content"""
    body = await request.json()
    message = body.get("message", "").strip()
    session_id = body.get("session_id", "")
    mode = body.get("mode", "precise")
    model = body.get("model", "deepseek-reasoner")
    style_hint = body.get("style_hint", "")
    scene_count = int(body.get("scene_count", "8"))
    user_id = getattr(request.state, "user_id", 0)

    if not message:
        return {"success": False, "error": "Please enter story idea"}

    logger.info(f"[ScriptWf/chat] session={session_id} mode={mode} model={model}")

    conversation = _get_conversation(session_id) if session_id else None
    messages = conversation["messages"] if conversation else []
    load_title = conversation.get("title", "") if conversation else ""
    load_genre = ""

    has_script = any(m.get("type") == "script" for m in messages)

    if not has_script:
        task = await _extract_task(message, model if model.startswith("deepseek") else "deepseek-v4-pro")
        if style_hint:
            task["style_hint"] = style_hint
        task["user_id"] = user_id
        task["scene_count"] = scene_count
        task["_llm_model"] = model

        result = await _run_workflow(task, mode, user_id)

        if not session_id:
            session_id = result.get("workflow_id", "sw_" + str(int(time.time() * 1000)) + "_" + str(user_id))

        script_content = ""
        if result.get("success"):
            script_parts = []
            if result.get("script"):
                script_parts.append(result["script"])
            if result.get("outline"):
                script_parts.append("## Story Outline" + chr(10) + result["outline"])
            if result.get("scenes"):
                scenes_text = (chr(10) + chr(10)).join(
                    [f"### Scene {s.get(chr(115)+chr(99)+chr(101)+chr(110)+chr(101)+chr(95)+chr(110)+chr(117)+chr(109)+chr(98)+chr(101)+chr(114), i+1)}" + chr(10) + s.get("content", json.dumps(s, ensure_ascii=False))
                     for i, s in enumerate(result["scenes"])]
                )
                script_parts.append("## Scene Script" + chr(10) + scenes_text)
            if result.get("characters"):
                import json as _j
                                # Format characters as readable cards
                chars = result["characters"]
                if isinstance(chars, list):
                    char_cards = []
                    for c in chars:
                        name = c.get("name", "?")
                        role = c.get("role", "")
                        gender = c.get("gender", "")
                        age = c.get("age", "")
                        appearance = c.get("appearance", "")
                        costume = c.get("costume", "")
                        personality = c.get("personality", "")
                        signature_pose = c.get("signature_pose", "")
                        strengths = c.get("strengths", [])
                        flaws = c.get("flaws", [])
                        # Build card
                        card = []
                        card.append(f"### {name} ({role})")
                        if gender or age:
                            card.append(f"**{gender}**{(' | ' + age) if age else ''}")
                        if appearance:
                            card.append(f"**外貌：**{appearance}")
                        if costume:
                            card.append(f"**服饰：**{costume}")
                        if signature_pose:
                            card.append(f"**标志动作：**{signature_pose}")
                        if personality:
                            card.append(f"**性格：**{personality}")
                        if strengths and isinstance(strengths, list):
                            card.append("**特长：**" + "、".join(strengths))
                        if flaws and isinstance(flaws, list):
                            card.append("**缺陷：**" + "、".join(flaws))
                        char_cards.append("\n".join(card))
                    cards_text = ("\n\n---\n\n".join(char_cards)) if char_cards else "无角色数据"
                    script_parts.append("## 角色设定" + chr(10) + cards_text)
                else:
                    script_parts.append("## 角色设定" + chr(10) + str(chars))
            script_content = (chr(10) + chr(10)).join(script_parts)
        else:
            script_content = "Generation failed: " + result.get("error", "Unknown")

        user_msg = {"role": "user", "content": message, "type": "text"}
        assistant_msg = {"role": "assistant", "content": script_content or "...", "type": "script"}
        messages.append(user_msg)
        messages.append(assistant_msg)
        _save_conversation(session_id, user_id, messages, task.get("title", ""), task.get("genre", ""))

        return {
            "success": result.get("success", False),
            "data": {
                "session_id": session_id,
                "title": task.get("title", ""),
                "genre": task.get("genre", ""),
                "content": script_content,
                "script": result.get("script", ""),
                "outline": result.get("outline", ""),
                "scenes": result.get("scenes", []),
                "characters": result.get("characters", []),
                "messages": messages,
            },
            "error": result.get("error", ""),
        }

    else:
        user_msg = {"role": "user", "content": message, "type": "text"}
        messages.append(user_msg)
        _save_conversation(session_id, user_id, messages, load_title, load_genre)

        last_script = ""
        for m in reversed(messages):
            if m.get("type") == "script":
                last_script = m.get("content", "")
                break

        assistant_msg = {"role": "assistant", "content": last_script, "type": "script"}
        messages.append(assistant_msg)
        _save_conversation(session_id, user_id, messages, load_title, load_genre)

        return {
            "success": True,
            "data": {
                "session_id": session_id,
                "content": last_script,
                "messages": messages,
                "note": "Modification feature active - use modify API for manual edits",
            },
        }


@router.get("/conversations")
async def list_conversations(request: Request):
    """List user script conversation history"""
    user_id = getattr(request.state, "user_id", 0)
    convs = _list_conversations(user_id)
    return {"success": True, "data": convs}


@router.get("/conversations/{session_id}")
async def get_conversation(session_id: str, request: Request):
    """Get specified conversation"""
    conv = _get_conversation(session_id)
    if not conv:
        return {"success": False, "error": "Conversation not found"}
    return {"success": True, "data": conv}


# ========== Modification Records & Copyright Certificate ==========

def _save_modification(session_id: str, user_id: int, original_content: str, modified_content: str, editor_notes: str = "", project_id: str = "") -> dict:
    """Save modification with auto-version + diff + timestamp"""
    from app_db import execute
    mods = _get_modifications(session_id)
    version = len(mods) + 1
    diff_summary = _generate_diff_summary(original_content, modified_content)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    execute(
        "INSERT INTO script_modifications (session_id, project_id, version, original_content, modified_content, diff_summary, editor_notes, created_at, user_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (session_id, project_id, version, original_content[:50000], modified_content[:50000], diff_summary, editor_notes, ts, user_id)
    )
    return {
        "version": version,
        "created_at": ts,
        "diff_summary": diff_summary,
        "note": "Modification saved with timestamp - can be used as copyright evidence"
    }


def _get_modifications(session_id: str) -> list:
    """Get all modification records"""
    try:
        from app_db import fetchall
        return list(fetchall("SELECT * FROM script_modifications WHERE session_id=? ORDER BY version ASC", (session_id,)))
    except Exception as e:
        logger.warning(f"[ScriptWf] Get modifications failed: {e}")
        return []


def _generate_diff_summary(original: str, modified: str) -> str:
    if not original or not modified:
        return "First creation"
    if original == modified:
        return "No changes"
    o_lines = original.split(chr(10))
    m_lines = modified.split(chr(10))
    o_len, m_len = len(o_lines), len(m_lines)
    changes = []
    if m_len > o_len:
        changes.append(f"Added {m_len - o_len} lines")
    elif o_len > m_len:
        changes.append(f"Removed {o_len - m_len} lines")
    diff_count = sum(1 for i in range(min(o_len, m_len)) if o_lines[i] != m_lines[i])
    if diff_count > 0 and max(o_len, m_len) > 0:
        changes.append(f"Modified {diff_count} lines ({diff_count * 100 // max(o_len, m_len)}%)")
    return ", ".join(changes) if changes else "Content adjusted"


@router.post("/modify/{session_id}")
async def submit_modification(session_id: str, request: Request):
    """Save human modification record with timestamp for copyright"""
    body = await request.json()
    user_id = getattr(request.state, "user_id", 0)
    modified_content = body.get("content", "").strip()
    editor_notes = body.get("notes", "").strip()
    project_id = body.get("project_id", "")
    title = body.get("title", "")
    genre = body.get("genre", "")
    if not modified_content:
        return {"success": False, "error": "Modified content cannot be empty"}
    original_content = ""
    conv = _get_conversation(session_id)
    if conv:
        for m in reversed(conv.get("messages", [])):
            if m.get("type") == "script":
                original_content = m.get("content", "")
                break
    if not original_content:
        return {"success": False, "error": "No original script found"}
    result = _save_modification(session_id, user_id, original_content, modified_content, editor_notes, project_id)
    if conv:
        messages = conv.get("messages", [])
        mod_msg = {
            "role": "assistant", "type": "modified_script",
            "content": modified_content, "version": result["version"],
            "timestamp": result["created_at"], "notes": editor_notes or "Manual edit"
        }
        user_action_msg = {
            "role": "user", "type": "modification",
            "content": "Version " + str(result["version"]) + " manual modification" + (f" ({editor_notes})" if editor_notes else "")
        }
        messages.append(user_action_msg)
        messages.append(mod_msg)
        _save_conversation(session_id, user_id, messages, title or conv.get("title", ""), genre)
    return {"success": True, "data": result}


@router.get("/modify/{session_id}")
async def list_modifications(session_id: str):
    """Get all modification records"""
    mods = _get_modifications(session_id)
    return {"success": True, "data": mods}


@router.get("/certificate/{session_id}")
async def get_certificate_report(session_id: str, request: Request):
    """Generate copyright certificate with evidence chain"""
    conv = _get_conversation(session_id)
    if not conv:
        return {"success": False, "error": "Conversation not found"}
    mods = _get_modifications(session_id)
    original_script = ""
    for m in reversed(conv.get("messages", [])):
        if m.get("type") == "script":
            original_script = m.get("content", "")
            break
    user_id = getattr(request.state, "user_id", 0)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")

    def _qh(text):
        import hashlib
        return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16] if text else ""

    report = {
        "report_type": "Original script copyright certificate",
        "session_id": session_id,
        "title": conv.get("title", "Unnamed"),
        "generated_at": ts,
        "evidence_chain": {
            "1_AI_generated": {
                "content_hash": _qh(original_script),
                "model": "DeepSeek-Reasoner (Hermes 7-Agent Workflow)",
            },
            "2_Human_modifications": {
                "total_versions": len(mods),
                "versions": [
                    {
                        "version": m["version"],
                        "timestamp": m["created_at"],
                        "diff_summary": m["diff_summary"],
                        "editor_notes": m.get("editor_notes", ""),
                        "content_hash": _qh(m["modified_content"]),
                    }
                    for m in mods
                ] if mods else [{"note": "No manual modifications yet"}]
            },
            "3_Final_version": {
                "content_hash": _qh(mods[-1]["modified_content"]) if mods else _qh(original_script),
                "final_version": mods[-1]["version"] if mods else 0,
            }
        },
        "legal_declaration": {
            "declaration": "This work was generated by AI drama platform (mzsh.top) and confirmed through human review.",
            "creator": str(user_id),
            "generation_platform": "mzsh.top AI Platform",
            "generation_model": "ARK DeepSeek + Hermes 7-Agent Pipeline",
            "compliance": "All characters are AI-generated, no real person rights involved"
        }
    }
    return {"success": True, "data": report}


@router.get("/cert/{project_id}")
async def get_cert(project_id: str):
    from services.certification import get_certificate
    cert = get_certificate(project_id)
    if not cert:
        return {"success": False, "error": "No certificate yet"}
    return {"success": True, "data": cert}


@router.get("/cert/{project_id}/logs")
async def get_cert_logs(project_id: str):
    from services.certification import get_project_logs
    logs = get_project_logs(project_id)
    return {"success": True, "data": logs}


@router.post("/cert/generate/{project_id}")
async def gen_cert(project_id: str, request: Request):
    from services.certification import generate_certificate
    user_id = getattr(request.state, "user_id", 0)
    body = {}
    try:
        raw = await request.body()
        if raw:
            body = json.loads(raw)
    except Exception:
        pass
    cert = generate_certificate(project_id, user_id, body.get("title",""), body.get("genre",""), body.get("video_url",""))
    return {"success": True, "data": cert}


@router.post("/outline/confirm/{session_id}")
async def confirm_outline(session_id: str, request: Request):
    """Member confirms outline with timestamp - human participation evidence"""
    body = await request.json()
    user_id = getattr(request.state, "user_id", 0)
    outline = body.get("outline", "").strip()
    notes = body.get("notes", "").strip()

    conv = _get_conversation(session_id)
    if not conv:
        return {"success": False, "error": "Conversation not found"}

    original_script = ""
    for m in reversed(conv.get("messages", [])):
        if m.get("type") == "script":
            original_script = m.get("content", "")
            break

    final_outline = outline if outline else original_script[:10000]
    original_for_diff = original_script[:10000]
    editor_note = "Member confirmed outline" + ((" - " + notes) if notes else "")
    result = _save_modification(session_id, user_id, original_for_diff, final_outline, editor_note)
    result["outline_confirmed"] = True
    result["note"] = "Outline confirmed with timestamp"
    return {"success": True, "data": result}


def _build_script_output(task, story_result, char_result, scene_result, review_result):
    """Build formatted script text from workflow stage results"""
    script_parts = []

    # Outline
    story_struct = story_result.get("story_structure", {}) if isinstance(story_result, dict) else {}
    outline_text = story_struct.get("outline", story_struct.get("synopsis", ""))
    if outline_text:
        script_parts.append("## \u6545\u4e8b\u5927\u7eb2\n" + outline_text)

    # Characters
    chars = char_result.get("characters", []) if isinstance(char_result, dict) else []
    if chars:
        char_cards = []
        for c in chars:
            name = c.get("name", "?")
            role = c.get("role", "")
            gender = c.get("gender", "")
            age = c.get("age", "")
            appearance = c.get("appearance", "")
            costume = c.get("costume", "")
            personality = c.get("personality", "")
            sig_pose = c.get("signature_pose", "")
            strengths = c.get("strengths", [])
            flaws = c.get("flaws", [])
            card = [f"### {name} ({role})"]
            if gender or age:
                parts = []
                if gender:
                    parts.append(f"**{gender}**")
                if age:
                    parts.append(age)
                card.append(" | ".join(parts))
            if appearance:
                card.append(f"**\u5916\u8c8c\uff1a**{appearance}")
            if costume:
                card.append(f"**\u670d\u9970\uff1a**{costume}")
            if sig_pose:
                card.append(f"**\u6807\u5fd7\u52a8\u4f5c\uff1a**{sig_pose}")
            if personality:
                card.append(f"**\u6027\u683c\uff1a**{personality}")
            if strengths and isinstance(strengths, list):
                card.append("**\u7279\u957f\uff1a**" + ",".join(strengths))
            if flaws and isinstance(flaws, list):
                card.append("**\u7f3a\u9677\uff1a**" + ",".join(flaws))
            char_cards.append("\n".join(card))
        cards_text = "\n\n---\n\n".join(char_cards) if char_cards else "\u65e0\u89d2\u8272\u6570\u636e"
        script_parts.append("## \u89d2\u8272\u8bbe\u5b9a\n" + cards_text)

    # Scenes
    scenes = scene_result.get("scenes", []) if isinstance(scene_result, dict) else []
    if scenes:
        scene_lines = []
        for i, s in enumerate(scenes):
            sid = s.get("scene_id", i + 1)
            loc = s.get("location", "")
            stime = s.get("time", "")
            ie = s.get("interior_exterior", "")
            tone = s.get("emotional_tone", "")
            dur = s.get("duration_seconds", 30)
            summary = s.get("summary", "")
            scene_lines.append(
                f"### \u573a\u666f {sid}: {loc} ({stime} {ie})\n"
                f"**\u60c5\u7eea\uff1a**{tone} | **\u65f6\u957f\uff1a**{dur}\u79d2\n"
                f"**\u6982\u8981\uff1a**{summary}\n"
            )
            shots = s.get("shots", [])
            for si, shot in enumerate(shots):
                shot_id = shot.get("shot_id", si + 1)
                cam = shot.get("camera", "")
                img = shot.get("image_prompt", "")
                shot_dur = shot.get("duration", 5)
                dial = shot.get("dialogue", "")
                action = shot.get("action", "")
                line = f"- ???{shot_id} {cam}: {img} ({shot_dur}??"
                if dial:
                    line += f" [TALK]{dial}"
                if action:
                    line += f" [DIRECTOR]{action}"
                scene_lines.append(line)
        scenes_text = "\n".join(scene_lines)
        script_parts.append("## \u5206\u573a\u5267\u672c\n" + scenes_text)

    # Review
    review = review_result.get("review", {}) if isinstance(review_result, dict) else {}
    if review:
        score = review.get("overall_score", "")
        passed = review.get("passed", False)
        verdict = "[CHECK] \u5ba1\u6838\u901a\u8fc7" if passed else "[WARN] \u5f85\u6539\u8fdb"
        script_parts.append(f"## \u8d28\u91cf\u5ba1\u6838\n**\u8bc4\u5206\uff1a**{score}/100 | **\u7ed3\u8bba\uff1a**{verdict}")
        issues = review.get("issues", [])
        if issues:
            script_parts.append("**\u6539\u8fdb\u5efa\u8bae\uff1a**\n" + "\n".join(f"- {issue}" for issue in issues))

    return "\n\n".join(script_parts)


async def _stream_generator(message, session_id, mode, model, style_hint, scene_count, user_id):
    """Async generator yielding SSE events for progressive script generation"""

    def _sse(event, data):
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    # Step 0: Extract task
    yield _sse("progress", {"stage": "extract", "message": "[SEARCH] \u5206\u6790\u6545\u4e8b\u521b\u610f...", "pct": 2})
    task = await _extract_task(message, model)
    if style_hint:
        task["style_hint"] = style_hint
    task["user_id"] = user_id
    task["scene_count"] = scene_count
    task["_llm_model"] = model

    engine = ScriptWorkflowEngine(user_id, llm_model=model)
    workflow_id = None

    try:
        loop = asyncio.get_event_loop()

        # Step 1: Showrunner
        yield _sse("progress", {"stage": "showrunner", "message": "[DIRECTOR] \u5bfc\u6f14\u5206\u6790\u4e2d...", "pct": 8})
        showrunner_result = await loop.run_in_executor(None, engine.showrunner.run, task)
        if not showrunner_result.get("success"):
            yield _sse("error", {"message": showrunner_result.get("error", "\u5bfc\u6f14\u5206\u6790\u5931\u8d25")})
            return
        task["showrunner_analysis"] = showrunner_result.get("analysis", {})

        # Step 2: Story Architect (generates outline + structure)
        yield _sse("progress", {"stage": "architect", "message": "[STORY] \u6545\u4e8b\u67b6\u6784\u5e08\u6784\u5efa\u4e2d...", "pct": 20})
        story_result = await loop.run_in_executor(None, engine.story_architect.run, task)
        if not story_result.get("success"):
            yield _sse("error", {"message": story_result.get("error", "\u6545\u4e8b\u67b6\u6784\u5931\u8d25")})
            return
        task["upstream_story"] = story_result

        # Emit outline event
        story_struct = story_result.get("story_structure", {}) if isinstance(story_result, dict) else {}
        outline_text = story_struct.get("outline", story_struct.get("synopsis", task.get("synopsis", "")))
        outline_data = {
            "title": task.get("title", ""),
            "genre": task.get("genre", ""),
            "outline": outline_text,
            "episode_outlines": story_result.get("episode_outlines", []),
            "synopsis": task.get("synopsis", ""),
        }
        yield _sse("outline", outline_data)

        # Step 3: Character Development
        yield _sse("progress", {"stage": "characters", "message": "[CHARACTER] \u89d2\u8272\u8bbe\u8ba1\u5e08\u521b\u4f5c\u4e2d...", "pct": 35})
        char_result = await loop.run_in_executor(None, engine.character_dev.run, task)
        if not char_result.get("success"):
            char_result = {"success": True, "characters": story_result.get("characters", [])}
        task["upstream_chars"] = char_result

        characters = char_result.get("characters", []) if isinstance(char_result, dict) else []
        yield _sse("characters", {"characters": characters})

        # Step 4: Scene Designer
        yield _sse("progress", {"stage": "scenes", "message": "[DIRECTOR] \u573a\u666f\u7f16\u5267\u7f16\u5199\u4e2d...", "pct": 50})
        scene_result = await loop.run_in_executor(None, engine.scene_designer.run, task)
        if not scene_result.get("success"):
            yield _sse("error", {"message": scene_result.get("error", "\u573a\u666f\u7f16\u5267\u5931\u8d25")})
            return
        task["upstream_scene"] = scene_result

        scenes = scene_result.get("scenes", []) if isinstance(scene_result, dict) else []
        yield _sse("scenes", {"scenes": scenes, "total_duration": scene_result.get("total_duration", 180)})

        # Step 5: Dialogue Writer
        yield _sse("progress", {"stage": "dialogue", "message": "[TALK] \u5bf9\u767d\u4e13\u5bb6\u6da6\u8272\u4e2d...", "pct": 65})
        dialogue_result = await loop.run_in_executor(None, engine.dialogue_writer.run, task)
        if dialogue_result.get("success"):
            dial_scenes = dialogue_result.get("scenes", [])
            if dial_scenes:
                task["upstream_scene"] = {**task["upstream_scene"], "scenes": dial_scenes}
                scenes = dial_scenes
                yield _sse("scenes", {"scenes": scenes, "note": "\u5bf9\u767d\u5df2\u6da6\u8272"})

        # Step 6: Pacing Editor
        yield _sse("progress", {"stage": "pacing", "message": "[PACING] \u8282\u594f\u7f16\u8f91\u4f18\u5316\u4e2d...", "pct": 78})
        pacing_result = await loop.run_in_executor(None, engine.pacing_editor.run, task)
        if not pacing_result.get("success"):
            pacing_result = {"success": True, "pacing_report": {"overall_rating": "\u672a\u8bc4\u4f30"}}
        task["upstream_pacing"] = pacing_result

        # Step 7: Review
        yield _sse("progress", {"stage": "review", "message": "[CHECK] \u8d28\u91cf\u5ba1\u6838\u4e2d...", "pct": 90})
        review_result = await loop.run_in_executor(None, engine.reviewer.run, task)

        # Finalize
        yield _sse("progress", {"stage": "finalizing", "message": "[SCRIPT] \u6574\u7406\u6700\u7ec8\u5267\u672c...", "pct": 97})

        workflow_id = f"sw_{int(time.time() * 1000)}_{user_id}"
        if not session_id:
            session_id = workflow_id

        script_content = _build_script_output(task, story_result, char_result, task["upstream_scene"], review_result)

        final_data = {
            "success": True,
            "session_id": session_id,
            "title": task.get("title", ""),
            "genre": task.get("genre", ""),
            "content": script_content,
            "script": script_content,
            "characters": characters,
            "scenes": scenes,
        }
        yield _sse("script", final_data)

        # Save conversation
        conv_messages = [
            {"role": "user", "content": message, "type": "text"},
            {"role": "assistant", "content": script_content, "type": "script"},
        ]
        _save_conversation(session_id, user_id, conv_messages, task.get("title", ""), task.get("genre", ""))

        yield _sse("complete", {"session_id": session_id, "workflow_id": workflow_id, "title": task.get("title", "")})

    except Exception as e:
        logger.error(f"[ScriptWf/stream] Error: {e}", exc_info=True)
        yield _sse("error", {"message": f"\u751f\u6210\u5931\u8d25: {str(e)}"})


@router.post("/chat/stream")
async def chat_stream(request: Request):
    """Streaming chat: receive story idea, return SSE events progressively"""
    body = await request.json()
    message = body.get("message", "").strip()
    session_id = body.get("session_id", "")
    mode = body.get("mode", "precise")
    model = body.get("model", "deepseek-reasoner")
    style_hint = body.get("style_hint", "")
    scene_count = int(body.get("scene_count", "8"))
    user_id = getattr(request.state, "user_id", 0)

    if not message:
        return {"success": False, "error": "Please enter story idea"}

    logger.info(f"[ScriptWf/chat_stream] session={session_id} mode={mode} model={model}")

    return StreamingResponse(
        _stream_generator(message, session_id, mode, model, style_hint, scene_count, user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )
