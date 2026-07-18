"""Alipay payment router - AI revenue for drama platform
Supports: membership / credits / per-second video generation billing
"""
import json, time, logging, uuid, base64, os
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from utils.auth_util import get_user_id

router = APIRouter(prefix="/api/v1/alipay", tags=["alipay"])
logger = logging.getLogger(__name__)

# -- Config --
APP_ID = "2021006169683274"
CONFIG_DIR = "/www/wwwroot/api.mzsh.top/config"
NOTIFY_URL = "https://api.mzsh.top/api/v1/alipay/notify"
RETURN_URL = "https://ai.mzsh.top/payment/success"
DB_PATH = "/www/wwwroot/api.mzsh.top/data/short_drama.db"

# -- Fixed product pricing --
PRODUCTS = {
    "vip_month":   {"name": "Monthly VIP",    "price": 29.90},
    "vip_quarter": {"name": "Quarterly VIP",  "price": 79.90},
    "vip_year":    {"name": "Yearly VIP",     "price": 299.00},
    "credits_100": {"name": "100 Credits",    "price": 9.90},
    "credits_500": {"name": "500 Credits",    "price": 39.90},
    "credits_1000":{"name": "1000 Credits",   "price": 69.90},
}


def _load_key(path):
    with open(path) as f:
        return f.read()


def _rsa_sign(content, private_key):
    """RSA-SHA256 signature"""
    from cryptography.hazmat.primitives import hashes, serialization, padding as asym_padding
    from cryptography.hazmat.backends import default_backend
    key = serialization.load_pem_private_key(
        private_key.encode(), password=None, backend=default_backend()
    )
    sig = key.sign(content.encode("utf-8"), asym_padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


def _rsa_verify(content, signature, public_key):
    """RSA-SHA256 verification"""
    from cryptography.hazmat.primitives import hashes, serialization, asym_padding
    from cryptography.hazmat.backends import default_backend
    try:
        key = serialization.load_pem_public_key(
            public_key.encode(), backend=default_backend()
        )
        key.verify(
            base64.b64decode(signature),
            content.encode("utf-8"),
            asym_padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False


def _build_sign_string(params):
    """Alipay sign string: sorted key=value pairs joined by &, excluding sign"""
    keys = sorted(k for k in params
                  if k != "sign" and params[k] is not None and params[k] != "")
    return "&".join(f"{k}={params[k]}" for k in keys)


def _get_db():
    import sqlite3
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


@router.post("/create")
async def create_payment(request: Request):
    """
    Request: {"product_id": "vip_month", "quantity": 1}
         or  {"product_id": "video_seconds", "seconds": 60}
    Returns: {"success": true, "data": {"pay_url": "...", "order_id": "AL...", "amount": 29.90}}
    """
    user_id = get_user_id(request)
    if not user_id:
        return JSONResponse({"success": False, "error": "Please login first"}, status_code=401)

    body = await request.json()
    product_id = body.get("product_id", "")

    if product_id == "video_seconds":
        seconds = int(body.get("seconds", 0))
        if seconds <= 0:
            return JSONResponse(
                {"success": False, "error": "Video duration must be > 0 seconds"},
                status_code=400
            )
        price_per_sec = float(body.get("price_per_sec", 0.05))
        total = round(price_per_sec * seconds, 2)
        title = body.get("title", f"Video Generation {seconds}s")
    else:
        product = PRODUCTS.get(product_id)
        if not product:
            return JSONResponse({"success": False, "error": "Product not found"}, status_code=400)
        quantity = max(int(body.get("quantity", 1)), 1)
        total = round(product["price"] * quantity, 2)
        title = product["name"]

    order_id = f"AL{int(time.time())}{uuid.uuid4().hex[:8]}"

    # Ensure product_id column exists
    db = _get_db()
    try:
        db.execute("ALTER TABLE orders ADD COLUMN product_id TEXT DEFAULT ''")
        db.commit()
    except Exception:
        pass

    db.execute(
        "INSERT INTO orders (id, user_id, title, amount, currency, product_id, status, created, updated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (order_id, user_id, title, total, "CNY", product_id, "pending", time.time(), time.time())
    )
    db.commit()
    db.close()
    logger.info(f"[Alipay] Created order {order_id} y={total} {title} user={user_id}")

    # Build Alipay payment URL
    import urllib.parse
    private_key = _load_key(f"{CONFIG_DIR}/alipay_private_key.pem")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    biz_content = json.dumps({
        "out_trade_no": order_id,
        "total_amount": str(total),
        "subject": title,
        "product_code": "FAST_INSTANT_TRADE_PAY",
        "body": f"AI-Drama-{title}",
    }, ensure_ascii=False, separators=(",", ":"))

    params = {
        "app_id": APP_ID,
        "method": "alipay.trade.page.pay",
        "format": "JSON",
        "charset": "utf-8",
        "sign_type": "RSA2",
        "timestamp": timestamp,
        "version": "1.0",
        "notify_url": NOTIFY_URL,
        "return_url": RETURN_URL,
        "biz_content": biz_content,
    }
    sign_str = _build_sign_string(params)
    params["sign"] = _rsa_sign(sign_str, private_key)

    pay_url = "https://openapi.alipay.com/gateway.do?" + urllib.parse.urlencode(params)
    return {"success": True, "data": {"pay_url": pay_url, "order_id": order_id, "amount": total}}


@router.post("/notify")
async def alipay_notify(request: Request):
    """Alipay callback: verify signature -> update order -> deliver"""
    form = dict(await request.form())
    logger.info(f"[Alipay] Received notification: {json.dumps(form, ensure_ascii=False)[:300]}")

    sign = form.get("sign", "")
    sign_content = _build_sign_string(form)
    public_key = _load_key(f"{CONFIG_DIR}/alipay_public_key.pem")
    if not _rsa_verify(sign_content, sign, public_key):
        logger.warning("[Alipay] Signature verification failed")
        return "failure"

    out_trade_no = form.get("out_trade_no", "")
    trade_status = form.get("trade_status", "")
    logger.info(f"[Alipay] Order {out_trade_no} status={trade_status}")

    if trade_status in ("TRADE_SUCCESS", "TRADE_FINISHED"):
        db = _get_db()
        try:
            db.execute(
                "UPDATE orders SET status='completed', trade_no=?, paid_at=?, updated=? WHERE id=?",
                (form.get("trade_no", ""), time.time(), time.time(), out_trade_no)
            )
            order = db.execute("SELECT * FROM orders WHERE id=?", (out_trade_no,)).fetchone()
            if order:
                uid = order["user_id"]
                amt = order["amount"]
                pid = order.get("product_id", "") or ""

                if pid.startswith("vip_"):
                    _deliver_membership(db, uid, pid, amt)
                elif pid.startswith("credits_"):
                    _deliver_credits(db, uid, pid, amt)
                else:
                    # Default: recharge balance
                    db.execute(
                        "INSERT INTO user_balance "
                        "(user_id, balance, total_charged, frozen, created, updated) "
                        "VALUES (?, ?, 0, 0, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
                        "balance=balance+?, total_charged=total_charged+?, updated=?",
                        (uid, amt, time.time(), time.time(), amt, amt, time.time())
                    )

                db.execute(
                    "INSERT INTO billing_log (user_id, order_id, amount, action, created) "
                    "VALUES (?,?,?,?,?)",
                    (uid, out_trade_no, amt, "alipay", time.time())
                )
                logger.info(f"[Alipay] Delivered OK user={uid} order={out_trade_no} amt={amt}")
            db.commit()
        except Exception as e:
            logger.error(f"[Alipay] Delivery error: {e}")
            db.rollback()
        finally:
            db.close()

    return "success"


def _deliver_membership(db, uid, product_id, amount):
    """Deliver VIP membership"""
    try:
        db.execute("ALTER TABLE users ADD COLUMN tier TEXT DEFAULT 'free'")
        db.execute("ALTER TABLE users ADD COLUMN vip_expires_at REAL DEFAULT 0")
        db.commit()
    except Exception:
        pass
    duration_map = {
        "vip_month": 30 * 86400,
        "vip_quarter": 90 * 86400,
        "vip_year": 365 * 86400,
    }
    secs = duration_map.get(product_id, 30 * 86400)
    expires_at = time.time() + secs
    db.execute(
        "UPDATE users SET tier='pro', vip_expires_at=? WHERE id=?",
        (expires_at, uid)
    )
    db.commit()
    logger.info(f"[Alipay] VIP delivered user={uid}")


def _deliver_credits(db, uid, product_id, amount):
    """Deliver creative credits"""
    credits_map = {
        "credits_100": 100,
        "credits_500": 500,
        "credits_1000": 1000,
    }
    credits = credits_map.get(product_id, int(amount * 10))
    db.execute(
        "INSERT INTO user_balance "
        "(user_id, balance, total_charged, frozen, created, updated) "
        "VALUES (?, ?, 0, 0, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
        "balance=balance+?, total_charged=total_charged+?, updated=?",
        (uid, credits, time.time(), time.time(), credits, credits, time.time())
    )
    db.commit()
    logger.info(f"[Alipay] Credits delivered user={uid} credits={credits}")


@router.get("/query/{order_id}")
async def query_order(order_id: str, request: Request):
    user_id = get_user_id(request)
    db = _get_db()
    order = db.execute(
        "SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, user_id)
    ).fetchone()
    db.close()
    if not order:
        return JSONResponse({"success": False, "error": "Order not found"})
    return {"success": True, "data": dict(order)}
