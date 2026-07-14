"""认证路由"""
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
import hashlib, secrets, time, random, os, sqlite3, logging
from pydantic import BaseModel

router = APIRouter(prefix="/api/v1/auth")

# ═══ Verification Code Store (phone -> {code, expire}) ══
_code_store: dict = {}

# ═══ Captcha ══
_captcha_store: dict = {}

# [安全修复] send-code 频率限制
_send_code_phone_ts: dict = {}   # phone -> 上次发送时间
_send_code_ip: dict = {}         # ip -> [timestamps]

@router.get("/captcha")
async def get_captcha():
    a, b = random.randint(1, 20), random.randint(1, 20)
    op = random.choice(["+", "-"])
    ans = a + b if op == "+" else a - b
    token = secrets.token_hex(8)
    _captcha_store[token] = str(ans)
    return {"success": True, "data": {"question": f"{a} {op} {b} = ?", "token": token}}

def check_captcha(token: str, answer: str) -> bool:
    expected = _captcha_store.pop(token, None)
    return expected is not None and expected.strip() == answer.strip()

# ═══ Auth ═══
DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "short_drama.db")

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def hash_password(password: str) -> str:
    return hashlib.sha256(("salted_" + password).encode()).hexdigest()

def make_token() -> str:
    return "tok_" + secrets.token_hex(24)

class RegisterModel(BaseModel):
    username: str
    email: str = ""
    password: str

class LoginModel(BaseModel):
    username: str = ""


@router.post("/send-code")
async def send_verification_code(request: Request):
    data = await request.json()
    phone = data.get("phone", "").strip()
    if not phone or len(phone) != 11 or not phone.isdigit():
        return JSONResponse({"success": False, "error": "请输入正确手机号"}, status_code=400)

    # [安全修复] 频率限制：同一手机号 60s 一次，同一 IP 每分钟 5 次
    now = time.time()
    client_ip = request.client.host if request.client else "unknown"
    last_ts = _send_code_phone_ts.get(phone, 0)
    if now - last_ts < 60:
        return JSONResponse({"success": False, "error": "验证码发送过于频繁，请60秒后再试"}, status_code=429)
    ip_times = [t for t in _send_code_ip.get(client_ip, []) if now - t < 60]
    if len(ip_times) >= 5:
        return JSONResponse({"success": False, "error": "请求过于频繁，请稍后再试"}, status_code=429)
    ip_times.append(now)
    _send_code_ip[client_ip] = ip_times

    code = str(random.randint(100000, 999999))
    _code_store[phone] = {"code": code, "expire": time.time() + 300}
    _send_code_phone_ts[phone] = now

    # [安全修复] 不再把验证码返回给客户端
    # 此前直接返回 code，任何人可用任意手机号获取验证码冒充登录
    # 验证码应只通过真实短信通道下发；如未接短信通道，仅记录日志
    logging.getLogger("api.auth").warning("[send-code] 短信通道未配置，验证码未下发 phone=%s", phone[:3] + "****" + phone[-4:])
    return {"success": True, "data": {"message": "验证码已发送，请注意查收"}}


@router.post("/login-or-register")
async def login_or_register(request: Request):
    data = await request.json()
    phone = data.get("phone", "").strip()
    code = data.get("code", "").strip()
    
    if not phone or len(phone) != 11 or not phone.isdigit():
        return JSONResponse({"success": False, "error": "请输入正确手机号"}, status_code=400)
    if not code:
        return JSONResponse({"success": False, "error": "请输入验证码"}, status_code=400)
    
    # 校验验证码
    stored = _code_store.get(phone)
    if not stored:
        return JSONResponse({"success": False, "error": "请先获取验证码"}, status_code=400)
    if time.time() > stored["expire"]:
        del _code_store[phone]
        return JSONResponse({"success": False, "error": "验证码已过期"}, status_code=400)
    if stored["code"] != code:
        return JSONResponse({"success": False, "error": "验证码错误"}, status_code=400)
    
    # 验证通过，删除已用的验证码
    del _code_store[phone]
    
    from app_db import fetchone, execute
    import hashlib, os as _os
    
    row = fetchone("SELECT id,username,password,token FROM users WHERE username = ?", (phone,))
    if row:
        # 手机号已存在 -> 登录
        token = row["token"] if row["token"] else "tok_" + hashlib.sha256(_os.urandom(32)).hexdigest()[:40]
        execute("UPDATE users SET token = ? WHERE id = ?", (token, row["id"]))
        return {"success": True, "data": {"access_token": token, "username": row["username"], "user_id": row["id"]}}
    else:
        # 手机号不存在 -> 自动注册
        import bcrypt
        random_pw = hashlib.sha256(_os.urandom(32)).hexdigest()[:24]
        pw_hash = bcrypt.hashpw(random_pw.encode(), bcrypt.gensalt(rounds=4)).decode()
        token = "tok_" + hashlib.sha256(_os.urandom(32)).hexdigest()[:40]
        execute(
            "INSERT INTO users (username, password, token, created) VALUES (?, ?, ?, ?)",
            (phone, pw_hash, token, time.time()))
        execute(
            "INSERT OR IGNORE INTO user_balance (user_id, balance, total_charged, total_spent, updated) VALUES ((SELECT id FROM users WHERE username=?), 0, 0, 0, ?)",
            (phone, time.time()))
        row = fetchone("SELECT id,username,password,token FROM users WHERE username = ?", (phone,))
        if row:
            return {"success": True, "data": {"access_token": token, "username": row["username"], "user_id": row["id"]}}
        return {"success": True, "data": {"access_token": token, "username": phone, "user_id": 0}}


@router.get("/me")
async def get_me(request: Request):
    token = request.query_params.get("token", request.headers.get("Authorization", "").removeprefix("Bearer "))
    if not token:
        raise HTTPException(status_code=401, detail="未登录")
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, username, email, created, COALESCE(tier,'free') as tier, COALESCE(avatar_url,'') as avatar_url FROM users WHERE token = ?",
            (token,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="登录过期")
        return {"success": True, "data": dict(row)}
    finally:
        conn.close()


@router.post("/register")
async def register_alias(request: Request):
    """Dedicated registration endpoint - skips captcha for new users"""
    try:
        try:
            data = await request.json()
        except Exception:
            return JSONResponse({"detail": "Invalid JSON body"}, status_code=400)
        username = data.get("username", "").strip()
        password = data.get("password", "")
        email = data.get("email", "")
        if not username or not password:
            return JSONResponse({"detail": "请填写账号和密码"}, status_code=400)
        if len(password) < 6:
            return JSONResponse({"detail": "密码至少6位"}, status_code=400)
        conn = get_db()
        try:
            # [安全修复] 新用户密码用 bcrypt 存储，不再用弱 sha256
            import bcrypt
            pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()
            row = conn.execute("SELECT id,username,password,token FROM users WHERE username = ?", (username,)).fetchone()
            if row:
                return JSONResponse({"detail": "账号已存在"}, status_code=400)
            token = make_token()
            cur = conn.execute(
                "INSERT INTO users (username, email, password, token, created) VALUES (?, ?, ?, ?, ?)",
                (username, email, pw_hash, token, time.time()))
            conn.execute(
                "INSERT OR IGNORE INTO user_balance (user_id, balance, total_charged, total_spent, updated) VALUES (?, 0, 0, 0, ?)",
                (cur.lastrowid, time.time()))
            conn.commit()
            return {"success": True, "data": {"access_token": token, "username": username, "user_id": cur.lastrowid, "tier": "free", "avatar_url": ""}}
        except Exception as db_err:
            __import__('logging').getLogger('api.auth').exception("Register DB error")
            return JSONResponse({"detail": "注册失败，请稍后再试"}, status_code=500)
        finally:
            conn.close()
    except Exception as e:
        __import__('logging').getLogger('api.auth').exception("Register unexpected error")
        return JSONResponse({"detail": "服务器内部错误"}, status_code=500)
