"""商户端认证路由 — 登录即注册"""
import sqlite3, os, secrets, time, random, logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/merchant")
logger = logging.getLogger("merchant")

DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "short_drama.db")

_codes: dict = {}  # phone -> {code, expire}

def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def make_merchant_token() -> str:
    return "mer_" + secrets.token_hex(24)

@router.post("/send-code")
async def send_code(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"detail": "请求格式错误"}, status_code=400)
    phone = data.get("phone", "").strip()
    if not phone or len(phone) != 11 or not phone.startswith("1"):
        return JSONResponse({"detail": "请输入正确的手机号"}, status_code=400)
    code = str(random.randint(100000, 999999))
    _codes[phone] = {"code": code, "expire": time.time() + 300}
    logger.info(f"[商户登录] 手机号 {phone} 验证码: {code}")
    return {"success": True, "message": "验证码已发送"}

@router.post("/login")
async def merchant_login(request: Request):
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"detail": "请求格式错误"}, status_code=400)
    phone = data.get("phone", "").strip()
    code = data.get("code", "").strip()
    if not phone or len(phone) != 11 or not phone.startswith("1"):
        return JSONResponse({"detail": "请输入正确的手机号"}, status_code=400)
    if not code:
        return JSONResponse({"detail": "请输入验证码"}, status_code=400)
    cached = _codes.get(phone)
    if not cached or cached["expire"] < time.time():
        return JSONResponse({"detail": "验证码已过期，请重新获取"}, status_code=400)
    if cached["code"] != code:
        return JSONResponse({"detail": "验证码错误"}, status_code=400)
    del _codes[phone]
    conn = get_db()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS merchants (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT UNIQUE, token TEXT, created_at REAL)"
        )
        row = conn.execute("SELECT * FROM merchants WHERE phone = ?", (phone,)).fetchone()
        if row:
            token = row["token"] if row["token"] else make_merchant_token()
            conn.execute("UPDATE merchants SET token = ? WHERE id = ?", (token, row["id"]))
            conn.commit()
            is_new = False
        else:
            token = make_merchant_token()
            conn.execute("INSERT INTO merchants (phone, token, created_at) VALUES (?, ?, ?)",
                         (phone, token, time.time()))
            conn.commit()
            is_new = True
        return {"success": True, "data": {"access_token": token, "phone": phone, "is_new": is_new}}
    finally:
        conn.close()
