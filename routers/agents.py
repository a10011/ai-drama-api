"""智能体执行 — 统一入口"""
import json, time, importlib, logging, asyncio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from api_models import AgentExecute
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.init_tools import create_registry

logger = logging.getLogger("api.agents")
router = APIRouter(prefix="/api/v1/agents", tags=["智能体"])

# ── 速率限制：每用户/每个IP每分钟最多30次 ──
import threading as _thr
_rate_limits = {}  # {key: [timestamps]}
_rate_lock = _thr.Lock()
_RATE_WINDOW = 60
_RATE_MAX = 30
_rate_cleanup_counter = 0  # 每 100 次检查清理一次空记录

def _check_rate_limit(user_key: str):
    """返回 (allowed, remaining, reset_after)"""
    now = time.time()
    with _rate_lock:
        if user_key not in _rate_limits:
            _rate_limits[user_key] = []
        # 清理过期记录
        _rate_limits[user_key] = [t for t in _rate_limits[user_key] if now - t < _RATE_WINDOW]
        # 定期清理空字典条目，防止内存泄漏
        global _rate_cleanup_counter
        _rate_cleanup_counter = (_rate_cleanup_counter + 1) % 100
        if _rate_cleanup_counter == 0:
            empty_keys = [k for k, v in _rate_limits.items() if not v]
            for k in empty_keys:
                del _rate_limits[k]
        count = len(_rate_limits[user_key])
        if count >= _RATE_MAX:
            reset_after = int(_RATE_WINDOW - (now - _rate_limits[user_key][0]))
            return (False, 0, reset_after)
        _rate_limits[user_key].append(now)
        return (True, _RATE_MAX - count - 1, 0)



AGENT_MAP = {
    "script": "agents.agent_script.ScriptAgent",
    "storyboard": "agents.agent_storyboard.StoryboardAgent",
    "scene": "agents.agent_scene.SceneAgent",
    "character": "agents.agent_character.CharacterAgent",
    "costume": "agents.agent_costume.CostumeAgent",
    "video": "agents.agent_video.VideoAgent",
    "tts": "agents.agent_tts.TTSAgent",
    "bgm": "agents.agent_bgm.BGMAgent",
    "subtitle": "agents.agent_subtitle.SubtitleAgent",
    "model_manager": "agents.agent_model_manager.AgentModelManager",
    "composite": "agents.agent_composite.CompositeAgent",
    "orchestrator": "agents.agent_orchestrator.OrchestratorAgent",
    "director": "agents.agent_director.DirectorAgent",
    "cinematographer": "agents.agent_cinematographer.CinematographerAgent",
    "sfx": "agents.agent_sfx.SFXAgent",
    "wardrobe": "agents.agent_wardrobe.WardrobeAgent",
}

_tool_registry = None

def _get_or_init_registry():
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = create_registry()
    return _tool_registry

def _load_agent(agent_id: str):
    path = AGENT_MAP.get(agent_id)
    if not path:
        return None
    mod_path, cls_name = path.rsplit(".", 1)
    mod = importlib.import_module(mod_path)
    reg = _get_or_init_registry()
    agent_key_map = {"script": "ScriptAgent", "director": "DirectorAgent"}
    agent_key = agent_key_map.get(agent_id, agent_id)
    return getattr(mod, cls_name)(tool_registry=reg, agent_name_for_tools=agent_key)

@router.post("/execute")
async def execute(body: AgentExecute, request: Request):
    # 速率限制检查
    user_key = "default"
    try:
        from utils.auth_util import get_user_id
        uid = get_user_id(request)
        if uid:
            user_key = str(uid)
    except Exception:
        pass
    # 也检查IP
    client_ip = request.client.host if request.client else "unknown"
    ip_key = f"ip:{client_ip}"
    for key in [user_key, ip_key]:
        allowed, remaining, reset_after = _check_rate_limit(key)
        if not allowed:
            logger.warning(f"[RateLimit] blocked {key} reset_in={reset_after}s")
            return JSONResponse(
                status_code=429,
                content={"success": False, "error": f"请求过于频繁，请{reset_after}秒后再试", "retry_after": reset_after}
            )
    
    agent = _load_agent(body.agent_id)
    if not agent:
        return {"success": False, "error": "未知智能体: " + body.agent_id}
    logger.info("Agent call: %s/%s params=%s" % (body.agent_id, body.action, str(body.params)[:100]))
    logger.info("[DBG] Agent params full: %s" % json.dumps(body.params, ensure_ascii=False)[:300])
    start = time.time()
    # [504修复] 生图类 agent 可能耗时较长（seedream 同步接口可能 30-90s）。
    # 配合 nginx proxy_read_timeout 120s，设 115s（留 5s 余量）。
    # 超时后立即返回友好错误，避免前端干等 504。后台线程仍在跑，结果不返回但不会卡死请求。
    AGENT_TIMEOUT = 240
    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, lambda: agent.run(action=body.action, **body.params)),
            timeout=AGENT_TIMEOUT
        )
        elapsed = int((time.time() - start) * 1000)
        err = result.error or ""
        if result.success:
            logger.info("  -> OK (%dms)" % elapsed)
            return {"success": True, "data": result.data, "error": err, "duration_ms": result.duration_ms}
        else:
            logger.warning("  -> FAIL (%dms): %s" % (elapsed, err))
            return {"success": False, "error": err or "智能体执行失败"}
    except asyncio.TimeoutError:
        elapsed = int((time.time() - start) * 1000)
        logger.warning("  -> TIMEOUT (%dms, >%ds): %s/%s" % (elapsed, AGENT_TIMEOUT, body.agent_id, body.action))
        return {"success": False, "error": f"生成超时（{AGENT_TIMEOUT}秒），请重试。长耗时任务请用一键生成或稍后再试。"}
    except Exception as e:
        import traceback, sys
        logger.error("Agent execute error: %s: %s", type(e).__name__, str(e)[:300])
        traceback.print_exc(file=sys.stderr)
        return {"success": False, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}

@router.get("/status")
async def agents_status():
    return {"success": True, "data": {"status": "ready", "agents": list(AGENT_MAP.keys()), "active": False, "queue_length": 0}}
