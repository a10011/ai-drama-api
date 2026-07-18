import sys, os; sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
"""FastAPI 入口"""
import sys, os, logging, time
sys.path.insert(0, os.path.dirname(__file__))
import json

import asyncio

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app_db import init_db

logger = logging.getLogger("api")
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
log_fmt = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
log_datefmt = "%Y-%m-%d %H:%M:%S"
logging.basicConfig(level=getattr(logging, LOG_LEVEL), format=log_fmt, datefmt=log_datefmt)
logger = logging.getLogger("api")
from app_config import BASE_URL

app = FastAPI(title="AI短剧 API", version="4.0.0", docs_url="/docs", redoc_url="/redoc")


@app.exception_handler(json.JSONDecodeError)
async def json_decode_handler(request: Request, exc: json.JSONDecodeError):
    """JSON parse failure -> 400, do not crash process"""
    return JSONResponse(
        status_code=400,
        content={"success": False, "error": "JSON format error: " + str(exc)}
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Last-resort exception handler to prevent any uncaught crash"""
    print(f"UNCAUGHT: {request.method} {request.url.path}: {exc}", flush=True)
    return JSONResponse(
        status_code=500,
        content={"success": False, "error": "Internal server error"}
    )


from fastapi import Request

@app.middleware("http")
async def inject_user_id(request: Request, call_next):
    # Skip auth for login/captcha/health endpoints
    if request.url.path in ("/api/login", "/api/captcha", "/health"):
        response = await call_next(request)
        return response
    request.state.user_id = 0
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        if token:
            from app_db import fetchone
            row = fetchone("SELECT id, expires_at FROM users WHERE token=?", (token,))
            if row:
                # [P2] Check token expiry (30 days = 2592000s)
                expires = row.get("expires_at")
                if expires is None or expires > time.time():
                    request.state.user_id = row["id"]
                else:
                    request.state.user_id = 0  # token expired
    response = await call_next(request)
    return response

# [FIX 1] CORS 改为允许特定域名
app.add_middleware(
    CORSMiddleware,
    allow_origins=[BASE_URL, "http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

# Custom handler: prevent 500 when RequestValidationError contains binary bytes
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc):
    try:
        errs = []
        for e in exc.errors():
            loc = ".".join(str(l) for l in e.get("loc", []))
            msg = e.get("msg", "")
            errs.append(f"{loc}: {msg}" if loc else msg)
        detail = "; ".join(errs) if errs else "Invalid request format"
        return JSONResponse(status_code=422, content={"detail": detail})
    except Exception:
        return JSONResponse(status_code=422, content={"detail": "Invalid request body"})


# ---------- 健康检查 ----------
@app.get("/health")
async def health():
    """深度健康检查：DB连接、存储"""
    import time as _t, os as _os
    result = {"status": "ok", "time": _t.time(), "checks": {}}
    try:
        import sqlite3
        db_path = _os.path.join(_os.path.dirname(__file__), "data", "short_drama.db")
        conn = sqlite3.connect(db_path, timeout=2)
        conn.execute("SELECT 1")
        conn.close()
        result["checks"]["db"] = "ok"
    except Exception as e:
        result["checks"]["db"] = f"error: {str(e)[:50]}"
        result["status"] = "degraded"
    try:
        storage = "/www/wwwroot/storage"
        test_file = _os.path.join(storage, ".healthcheck")
        with open(test_file, "w") as f:
            f.write("ok")
        _os.remove(test_file)
        result["checks"]["storage"] = "ok"
    except Exception as e:
        result["checks"]["storage"] = f"error: {str(e)[:50]}"
        result["status"] = "degraded"
    return result


# ── 模型管家面板 ──

@app.get("/api/v1/models/status")
async def models_status():
    """全部模型健康状态"""
    try:
        from agents.agent_model_manager import get_model_manager
        mm = get_model_manager()
        return mm.run(action="status")
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/v1/models/metrics")
async def models_metrics(model: str = None, hours: int = 1):
    """模型调用指标"""
    try:
        from agents.agent_model_manager import get_model_manager
        mm = get_model_manager()
        return mm.run(action="metrics", model=model, hours=hours)
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/v1/models/spec")
async def models_spec(model: str = None):
    """模型规格查询"""
    try:
        from agents.agent_model_manager import get_model_manager
        mm = get_model_manager()
        return mm.run(action="spec", model=model)
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/v1/models/reset")
async def models_reset(model: str):
    """重置模型状态"""
    try:
        from agents.agent_model_manager import get_model_manager
        mm = get_model_manager()
        return mm.run(action="reset", model=model)
    except Exception as e:
        return {"success": False, "error": str(e)}

# ---------- WebSocket 连接管理 ----------
class WSManager:
    """[FIX 7] channels 操作加 asyncio.Lock 保证线程安全"""

    def __init__(self):
        self.channels: dict[str, list[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, channel: str = "default"):
        await ws.accept()
        async with self._lock:
            self.channels.setdefault(channel, []).append(ws)

    async def disconnect(self, ws: WebSocket, channel: str = "default"):
        async with self._lock:
            if channel in self.channels and ws in self.channels[channel]:
                self.channels[channel].remove(ws)

    async def broadcast(self, data: dict, channel: str = "default"):
        async with self._lock:
            # snapshot the list under lock to avoid mutation during iteration
            targets = list(self.channels.get(channel, []))
        for ws in targets:
            try:
                await ws.send_json(data)
            except Exception:
                # [FIX 8-1] 裸 except:pass → logger.exception()
                logger.exception("broadcast send_json failed for channel=%s", channel)


ws_manager = WSManager()


@app.websocket("/ws/pipeline")
async def websocket_pipeline(websocket: WebSocket):
    channel = "default"
    await ws_manager.connect(websocket, channel)
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action", "")
            if action == "subscribe":
                await ws_manager.disconnect(websocket, channel)
                channel = data.get("channel", "default")
                await ws_manager.connect(websocket, channel)
                await websocket.send_json({"success": True, "data": {"action": "subscribe", "channel": channel}})
            elif action == "unsubscribe":
                await ws_manager.disconnect(websocket, channel)
                channel = "default"
                await ws_manager.connect(websocket, channel)
                await websocket.send_json({"success": True, "data": {"action": "unsubscribe"}})
            elif action == "ping":
                await websocket.send_json({"success": True, "data": {"action": "pong"}})
            elif action == "broadcast":
                ch = data.get("channel", channel)
                await ws_manager.broadcast(data.get("payload", {}), ch)
                await websocket.send_json({"success": True, "data": {"action": "broadcast_sent"}})
            else:
                await websocket.send_json({"success": False, "error": f"未知 action: {action}"})
    except WebSocketDisconnect:
        # [FIX 8-2] 裸 except:pass → logger.exception()（正常断开只记 info）
        logger.info("WebSocket disconnected, channel=%s", channel)
    except Exception:
        # [FIX 8-3] 裸 except:pass → logger.exception()
        logger.exception("WebSocket error, channel=%s", channel)
    finally:
        await ws_manager.disconnect(websocket, channel)


@app.on_event("startup")
async def startup():
    init_db()
    try:
        from routers.materials import _m as _mproxy
        _mproxy().init()
    except Exception:
        # [FIX 8-4] 裸 except:pass → logger.exception()
        logger.exception("materials.init() failed")

    # ── Framework v2 bootstrap ──
    try:
        from core.bootstrap import bootstrap as fw_bootstrap
        config = fw_bootstrap()
        logger.info(f"Framework v2 bootstrapped: env={config.environment.value}")
    except Exception as e:
        logger.warning(f"Framework v2 bootstrap skipped: {e}")

    # 启动重试队列后台工作线程
    try:
        import threading
        from services.retry_manager import retry_worker_loop
        t = threading.Thread(target=retry_worker_loop, daemon=True, name="retry-worker")
        t.start()
        logger.info("Retry worker started")
    except Exception as e:
        logger.warning(f"Retry worker failed: {e}")
    import threading
    def _start_v2_workers():
        try:
            from routers.pipeline_v2 import start_all_workers
            start_all_workers()
            logger.info("V2 workers started")
        except Exception as e:
            logger.warning(f"V2 workers: {e}")
    threading.Thread(target=_start_v2_workers, daemon=True, name="v2-workers").start()
    # V2 pipeline workers (Hermes MQ)
    try:
        import threading as _thr
        def _start_v2():
            from routers.pipeline_v2 import start_all_workers
            start_all_workers()
            logger.info("V2 pipeline workers started")
        _thr.Thread(target=_start_v2, daemon=True, name="v2-workers").start()
    except Exception as _ve:
        logger.warning(f"V2 workers failed: {_ve}")
    logger.info("API started on port 8000")


# ═══════════════════════════════════════════════════════
# 兼容前端旧路由: /api/login + /api/captcha
# 前端 LoginPage.vue 调这两个路径，不走 /api/v1/auth
# ═══════════════════════════════════════════════════════
import random, secrets as _secrets, time as _time
from fastapi import Request as _Request
from fastapi.responses import JSONResponse as _JSONResponse

# Libraries for security fixes
import bcrypt  # [FIX 2] bcrypt for password hashing
import hashlib as _hashlib


def _verify_password(plain: str, stored: str) -> bool:
    """兼容多种密码格式：bcrypt ($2b$/$2y$) → 直接校验；旧格式 → SHA256/MD5 校验。
    避免老用户密码不是 bcrypt 时 bcrypt.checkpw 抛 "Invalid salt" 导致 500。"""
    if not stored:
        return False
    # bcrypt 格式：$2b$ / $2y$ / $2a$
    if stored.startswith("$2"):
        try:
            return bcrypt.checkpw(plain.encode(), stored.encode())
        except (ValueError, TypeError):
            return False
    # 旧格式：SHA256 hex (64 chars) 或 MD5 hex (32 chars)
    if len(stored) == 64:
        return _hashlib.sha256(plain.encode()).hexdigest() == stored
    if len(stored) == 32:
        return _hashlib.md5(plain.encode()).hexdigest() == stored
    # 兜底：明文直接比较
    return plain == stored

# [FIX 5] _captcha_store: 值改为 (answer, created_at) 元组，支持30秒过期
_captcha_store: dict[str, tuple[str, float]] = {}

# [FIX 6] 登录速率限制：同一IP 5次/分钟
_login_attempts: dict[str, list[float]] = {}
_LOGIN_RATE_LIMIT = 5       # 5 attempts
_LOGIN_RATE_WINDOW = 60.0   # per 60 seconds


def _cleanup_expired_captchas(now: float | None = None):
    """清理超过30秒的验证码"""
    if now is None:
        now = _time.time()
    expired = [tok for tok, (_, ts) in _captcha_store.items() if now - ts > 30]
    for tok in expired:
        del _captcha_store[tok]


def _cleanup_login_attempts(now: float | None = None):
    """[FIX 13] 清理所有IP的过期登录速率限制记录"""
    if now is None:
        now = _time.time()
    for ip in list(_login_attempts.keys()):
        _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < _LOGIN_RATE_WINDOW]
        if not _login_attempts[ip]:
            del _login_attempts[ip]


def _check_rate_limit(ip: str) -> bool:
    """检查IP是否超过速率限制，返回 True 表示允许，False 表示超限"""
    now = _time.time()
    if ip not in _login_attempts:
        _login_attempts[ip] = []
    # 清理过期记录
    _login_attempts[ip] = [t for t in _login_attempts[ip] if now - t < _LOGIN_RATE_WINDOW]
    if len(_login_attempts[ip]) >= _LOGIN_RATE_LIMIT:
        return False
    _login_attempts[ip].append(now)
    return True


@app.post("/api/login")
async def _compat_login(request: _Request):
    # [FIX 6] 速率限制检查
    _cleanup_login_attempts()
    client_ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(client_ip):
        return _JSONResponse({"detail": "请求过于频繁，请稍后再试"}, status_code=429)

    try:
        body = await request.json()
    except Exception:
        import traceback
        traceback.print_exc()
        return _JSONResponse({"detail": "请求格式错误"}, status_code=400)
    username = body.get("username", "").strip()
    password = body.get("password", "")

    # [FIX 3] 验证码校验
    captcha_token = body.get("captcha_token", "")
    captcha_answer = body.get("captcha_answer", "")

    if not username or not password:
        return _JSONResponse({"detail": "请填写账号和密码"}, status_code=400)

    # [FIX 5] 清理过期验证码后再校验
    _cleanup_expired_captchas()

    # 验证码可选：前端兼容（旧版登录页不传 captcha）
    # captcha optional for frontend compat
    if captcha_token:
        if captcha_token not in _captcha_store:
            return _JSONResponse({"detail": "captcha expired"}, status_code=400)
        stored_answer, _ = _captcha_store[captcha_token]
        if stored_answer != str(captcha_answer).strip():
            del _captcha_store[captcha_token]
            return _JSONResponse({"detail": "captcha wrong"}, status_code=400)
        del _captcha_store[captcha_token]
    from app_db import fetchone, execute

    row = fetchone("SELECT * FROM users WHERE username = ?", (username,))
    if row:
        stored_pw = row["password_hash"]
        if not _verify_password(password, stored_pw):
            return _JSONResponse({"detail": "密码错误"}, status_code=400)

        token = row["token"] if row["token"] else "tok_" + _secrets.token_hex(24)
        execute("UPDATE users SET token = ? WHERE id = ?", (token, row["id"]))
        return {"success": True, "data": {"token": token, "access_token": token, "username": row["username"], "user_id": row["id"]}}
    else:
        # 登录即注册：自动创建账号
        import hashlib, os as _os
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
        token = "tok_" + hashlib.sha256(_os.urandom(32)).hexdigest()[:40]
        execute(
            "INSERT INTO users (username, password_hash, token, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (username, pw_hash, token, time.time(), time.time() + 2592000))
        execute(
            "INSERT OR IGNORE INTO user_balance (user_id, balance, total_charged, total_spent, updated) VALUES ((SELECT id FROM users WHERE username=?), 0, 0, 0, ?)",
            (username, time.time()))
        row = fetchone("SELECT * FROM users WHERE username = ?", (username,))
        if row:
            return {"success": True, "data": {"token": token, "access_token": token, "username": row["username"], "user_id": row["id"]}}
        return {"success": True, "data": {"token": token, "access_token": token, "username": username, "user_id": 0}}


@app.get("/api/captcha")
async def _compat_captcha():
    # [FIX 5] 生成前先清理过期验证码
    _cleanup_expired_captchas()
    # [FIX 12] 验证码字典容量限制 (最大1000条)
    if len(_captcha_store) >= 1000:
        oldest = min(_captcha_store.keys(), key=lambda k: _captcha_store[k][1])
        del _captcha_store[oldest]
    a = random.randint(1, 20)
    b = random.randint(1, 20)
    op = random.choice(["+", "-"])
    ans = a + b if op == "+" else a - b
    tok = _secrets.token_hex(8)
    _captcha_store[tok] = (str(ans), _time.time())  # 存储答案+时间戳
    return {"success": True, "data": {"question": f"{a} {op} {b} = ?", "token": tok}}


# [FIX 10] 合并重复的 router import，归为一个 import 块
# [FIX 11] 如果 router 内部已定义 prefix，include_router 不再重复加 prefix
from routers.assets_router import router as assets_router
from routers import (
    orders_router,
    projects, pipeline, agents, pipeline_v2, characters, command,
    materials, cs, keys, code,
    auth, director_api, wechat_router, portrait_api,
    preview_router, media_router, status_router,
    tool_routes, points_router, billing, hermes_router,
)

# ===== Main App Setup =====
try:
    from routers import pipeline
    from routers import scene_image_router
    from routers import review_endpoint
    from routers import projects as projects_check
except ImportError as e:
    print(f"Warning: Failed to load some routers: {e}")

# [FIX 11] 修复 router prefix 双重拼接:
# 如果路由模块内部已经通过 APIRouter(prefix=...) 定义了 prefix，
# include_router 这里就不再添加 prefix，避免出现 /api/v1/auth/api/v1/auth/... 的双重前缀。
# 以下路由器假设内部已定义 prefix，故此处仅传 tags。
# 若某路由器未定义内部 prefix，请在 include_router 中补回 prefix 参数。
app.include_router(auth.router, tags=["auth"])              # 原 prefix="/api/v1/auth" 已移除
app.include_router(director_api.router)
app.include_router(portrait_api.router)
app.include_router(preview_router.router)
app.include_router(pipeline.router, tags=["pipeline"])
app.include_router(scene_image_router.router)
app.include_router(review_endpoint.router)
app.include_router(agents.router, tags=["agents"])
app.include_router(projects.router, tags=["projects"])
app.include_router(characters.router, tags=["characters"])
app.include_router(materials.router, tags=["materials"])
app.include_router(cs.router, tags=["cs"])
app.include_router(keys.router, tags=["keys"])
app.include_router(code.router, tags=["code"])
app.include_router(hermes_router.router, tags=["hermes"])
app.include_router(media_router.router)
app.include_router(status_router.router)
app.include_router(billing.router)
app.include_router(orders_router.router)  # 订单路由 — 必须在 command wildcard 之前
from agents_v2.script_workflow.router import router as script_workflow_router
app.include_router(script_workflow_router)
app.include_router(command.router, tags=["command"])
app.include_router(tool_routes.router)
app.include_router(points_router.router)
app.include_router(wechat_router.router)
app.include_router(assets_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

from routers.v2_balance import router as v2_balance
app.include_router(v2_balance)

from routers.v2_characters import router as v2_characters
app.include_router(v2_characters)
from routers.faceswap_presets import router as faceswap_presets_router
from routers.v2_assets import router as v2_assets
import json
app.include_router(faceswap_presets_router)
app.include_router(v2_assets)
from routers import composite_progress
app.include_router(composite_progress.router)
app.include_router(pipeline_v2.router, tags=["pipeline-v2"])

# Hermes 独立剧本工作流
from routers.missing_endpoints import router as missing_router
app.include_router(missing_router)
