"""WeChat Pay router - APIv3 native implementation (Native QR + JSAPI)
No third-party SDK dependency. Uses cryptography for RSA signing.
Config file: config/wechat_pay.json
"""
import json, time, uuid, hashlib, base64, os, logging, random
from datetime import datetime, timezone
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from utils.auth_util import get_user_id

router = APIRouter(prefix="/api/v1/wechat/pay", tags=["wechat-pay"])
logger = logging.getLogger(__name__)

CONFIG_DIR = "/www/wwwroot/api.mzsh.top/config"
DB_PATH = "/www/wwwroot/api.mzsh.top/data/short_drama.db"
WECHAT_CONFIG_FILE = f"{CONFIG_DIR}/wechat_pay.json"
NOTIFY_URL_NATIVE = "https://api.mzsh.top/api/v1/wechat/pay/native-notify"
NOTIFY_URL_JSAPI = "https://api.mzsh.top/api/v1/wechat/pay/jsapi-notify"


def _get_db():
    import sqlite3
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def _load_pem(path):
    with open(path, "rb") as f:
        return f.read()


def _load_wechat_config():
    try:
        with open(WECHAT_CONFIG_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"[WeChat Pay] Config not found: {WECHAT_CONFIG_FILE}")
        return None


def _wechat_rsa_sign(message: str, private_key_pem: bytes) -> str:
    """RSA-SHA256 sign with merchant private key"""
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    signature = private_key.sign(
        message.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256()
    )
    return base64.b64encode(signature).decode()


def _create_order(order_id, user_id, title, amount, product_id):
    """Create local order record, ensuring product_id column exists"""
    db = _get_db()
    try:
        db.execute("ALTER TABLE orders ADD COLUMN product_id TEXT DEFAULT ''")
        db.commit()
    except Exception:
        pass
    db.execute(
        "INSERT INTO orders (id, user_id, title, amount, currency, product_id, status, created, updated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (order_id, user_id, title, amount, "CNY", product_id, "pending", time.time(), time.time())
    )
    db.commit()
    logger.info(f"[WeChat Pay] Created order {order_id} y={amount} {title} user={user_id}")


def _calc_amount(config, product_id, quantity, body):
    """Calculate payment amount in cents (wei)"""
    if product_id == "video_seconds":
        seconds = int(body.get("seconds", 0))
        if seconds <= 0:
            return None
        price_per_sec = config.get("price_per_second", 0.05)
        amount_yuan = round(price_per_sec * seconds, 2)
        return int(amount_yuan * 100)
    products = config.get("products", {})
    product = products.get(product_id)
    if not product:
        return None
    return int(product.get("price_yuan", 0) * quantity * 100)


def _get_product_name(config, product_id):
    products = config.get("products", {})
    p = products.get(product_id, {})
    return p.get("name", product_id)


def _call_wechat_api(config, endpoint, req_body):
    """Call WeChat Pay API v3 with RSA signature"""
    import urllib.request, ssl as _ssl

    merchant_id = config["merchant_id"]
    appid = config["appid"]
    cert_dir = config.get("cert_dir", CONFIG_DIR)
    private_key_path = f"{cert_dir}/wechat_private_key.pem"
    cert_serial_no = config["certificate_serial_no"]

    timestamp = str(int(time.time()))
    nonce_str = uuid.uuid4().hex[:32]

    body_json = json.dumps(req_body, separators=(",", ":")).encode("utf-8")

    # Build signature string
    sign_str = f"POST\n{endpoint}\n{timestamp}\n{nonce_str}\n{body_json.decode()}\n"
    priv_key = _load_pem(private_key_path)
    sign = _wechat_rsa_sign(sign_str, priv_key)

    auth_header = (
        f"WECHATPAY2-SHA256-RSA2048 "
        f'mchid="{merchant_id}", '
        f'serial_no="{cert_serial_no}", '
        f'nonce_str="{nonce_str}", '
        f'timestamp="{timestamp}", '
        f'signature="{sign}"'
    )

    url = f"https://api.mch.weixin.qq.com{endpoint}"
    req = urllib.request.Request(
        url,
        data=body_json,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": auth_header,
        },
        method="POST",
    )

    ctx = _ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ════════════════════════════════════════
# 1. Native Payment (PC QR code scan)
# ════════════════════════════════════════
@router.post("/native/create")
async def native_create_payment(request: Request):
    """
    PC: Create Native order -> returns QR code URL
    Request: {"product_id": "video_seconds", "seconds": 60}
         or  {"product_id": "vip_month", "quantity": 1}
    Returns: {"success": true, "data": {"code_url": "weixin://...", "order_id": "WP...", "amount": 29.90}}
    """
    user_id = get_user_id(request)
    if not user_id:
        return JSONResponse({"success": False, "error": "Please login first"}, status_code=401)

    body = await request.json()
    product_id = body.get("product_id", "")
    quantity = int(body.get("quantity", 1))
    config = _load_wechat_config()

    if not config:
        return JSONResponse({"success": False, "error": "WeChat Pay not configured"}, status_code=502)

    amount_cents = _calc_amount(config, product_id, quantity, body)
    if amount_cents is None:
        return JSONResponse({"success": False, "error": f"Unknown product: {product_id}"}, status_code=400)

    total_fee = amount_cents
    if total_fee <= 0:
        return JSONResponse({"success": False, "error": "Amount must be > 0"}, status_code=400)

    order_id = f"WP{int(time.time())}{uuid.uuid4().hex[:8]}"
    description = body.get("title", _get_product_name(config, product_id))

    _create_order(order_id, user_id, description, total_fee / 100, product_id)

    req_body = {
        "appid": config["appid"],
        "mchid": config["merchant_id"],
        "description": description[:127],
        "out_trade_no": order_id,
        "time_expire": datetime.fromtimestamp(
            time.time() + 1800, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "attach": product_id,
        "notify_url": NOTIFY_URL_NATIVE,
        "amount": {"total": total_fee, "currency": "CNY"},
        "scene_info": {"payer_client_ip": "127.0.0.1", "device_info": "WEB"},
    }

    result = _call_wechat_api(config, "/api/v3/pay/transactions/native", req_body)
    code_url = result.get("code_url", "")
    if not code_url:
        logger.error(f"[WeChat Pay] No code_url: {result}")
        return JSONResponse({"success": False, "error": "Failed to get payment link"})

    return {"success": True, "data": {"code_url": code_url, "order_id": order_id, "amount": total_fee / 100}}


# ════════════════════════════════════════
# 2. JSAPI Payment (WeChat Official Account / Mini Program)
# ════════════════════════════════════════
@router.post("/jsapi/create")
async def jsapi_create_payment(request: Request):
    """
    Official account / H5: Create JSAPI order -> returns prepay_id + JS params
    Request: {"product_id": "video_seconds", "seconds": 60, "openid": "oXXXXX"}
    Returns: {"success": true, "data": {"prepay_id": "wx...", "js_params": {...}, "amount": 29.90}}
    """
    user_id = get_user_id(request)
    if not user_id:
        return JSONResponse({"success": False, "error": "Please login first"}, status_code=401)

    body = await request.json()
    product_id = body.get("product_id", "")
    quantity = int(body.get("quantity", 1))
    openid = body.get("openid", "")
    config = _load_wechat_config()

    if not config:
        return JSONResponse({"success": False, "error": "WeChat Pay not configured"}, status_code=502)

    amount_cents = _calc_amount(config, product_id, quantity, body)
    if amount_cents is None:
        return JSONResponse({"success": False, "error": f"Unknown product: {product_id}"}, status_code=400)

    total_fee = amount_cents
    if total_fee <= 0:
        return JSONResponse({"success": False, "error": "Amount must be > 0"}, status_code=400)

    order_id = f"WP{int(time.time())}{uuid.uuid4().hex[:8]}"
    description = body.get("title", _get_product_name(config, product_id))

    _create_order(order_id, user_id, description, total_fee / 100, product_id)

    req_body = {
        "appid": config["appid"],
        "mchid": config["merchant_id"],
        "description": description[:127],
        "out_trade_no": order_id,
        "time_expire": datetime.fromtimestamp(
            time.time() + 1800, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "attach": product_id,
        "notify_url": NOTIFY_URL_JSAPI,
        "amount": {"total": total_fee, "currency": "CNY"},
        "payer": {"openid": openid},
        "scene_info": {"payer_client_ip": "127.0.0.1"},
    }

    result = _call_wechat_api(config, "/api/v3/pay/transactions/jsapi", req_body)
    prepay_id = result.get("prepay_id", "")
    if not prepay_id:
        logger.error(f"[WeChat Pay] No prepay_id: {result}")
        return JSONResponse({"success": False, "error": "Failed to get prepay_id"})

    # Build JSAPI params for frontend
    js_ts = str(int(time.time()))
    js_nonce = uuid.uuid4().hex[:32]
    cert_dir = config.get("cert_dir", CONFIG_DIR)
    priv_key = _load_pem(f"{cert_dir}/wechat_private_key.pem")

    js_sign_str = f"{config['appid']}\n{js_ts}\n{js_nonce}\n{prepay_id}\n"
    js_sign = _wechat_rsa_sign(js_sign_str, priv_key)

    js_params = {
        "appId": config["appid"],
        "timeStamp": js_ts,
        "nonceStr": js_nonce,
        "package": f"prepay_id={prepay_id}",
        "signType": "RSA",
        "paySign": js_sign,
    }

    return {"success": True, "data": {"prepay_id": prepay_id, "order_id": order_id, "amount": total_fee / 100, "js_params": js_params}}


# ════════════════════════════════════════
# 3. Payment callbacks
# ════════════════════════════════════════
@router.post("/native-notify")
async def native_notify(request: Request):
    await _handle_pay_callback(request)
    return "success"


@router.post("/jsapi-notify")
async def jsapi_notify(request: Request):
    await _handle_pay_callback(request)
    return "success"


async def _handle_pay_callback(request: Request):
    """Handle WeChat Pay callback: decrypt -> verify -> fulfill order"""
    config = _load_wechat_config()
    if not config:
        logger.error("[WeChat Pay] Config missing, rejecting callback")
        return

    body_raw = await request.body()
    body = json.loads(body_raw.decode("utf-8"))

    try:
        # Decrypt callback body (AES-256-GCM with API v3 key)
        api_key = config.get("api_key_v3", "")
        resource = body.get("resource", {})
        ciphertext = resource.get("ciphertext", "")
        nonce = resource.get("nonce", "")
        associated_data = resource.get("associated_data", "")
        tag = resource.get("tag", "")

        from Crypto.Cipher import AES
        import base64 as _b64

        key_bytes = hashlib.sha256(api_key.encode("utf-8")).digest()
        cipher = AES.new(key_bytes, AES.MODE_GCM, nonce=_b64.b64decode(nonce))
        cipher.update(associated_data.encode("utf-8"))
        plaintext = cipher.decrypt_and_verify(
            _b64.b64decode(ciphertext), _b64.b64decode(tag)
        )
        res_data = json.loads(plaintext.decode("utf-8"))
    except Exception as e:
        logger.error(f"[WeChat Pay] Decrypt failed: {e}")
        return

    trade_state = res_data.get("trade_state", "")
    out_trade_no = res_data.get("out_trade_no", "")
    transaction_id = res_data.get("transaction_id", "")

    if trade_state == "SUCCESS":
        logger.info(f"[WeChat Pay] Payment success order={out_trade_no} txn={transaction_id}")
        await _fulfill_order(out_trade_no, res_data)
    else:
        logger.warning(f"[WeChat Pay] Payment not success order={out_trade_no} state={trade_state}")


async def _fulfill_order(order_id, wx_result):
    """Fulfill order after payment success"""
    db = _get_db()
    try:
        order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            logger.warning(f"[WeChat Pay] Order not found: {order_id}")
            return

        uid = order["user_id"]
        product_id = order.get("product_id", "") or ""
        amount = order["amount"]

        db.execute(
            "UPDATE orders SET status='completed', trade_no=?, paid_at=?, updated=? WHERE id=?",
            (wx_result.get("transaction_id", ""), time.time(), time.time(), order_id)
        )

        if product_id.startswith("vip_"):
            _deliver_membership(db, uid, product_id, amount)
        elif product_id.startswith("credits_"):
            _deliver_credits(db, uid, product_id, amount)
        else:
            db.execute(
                "INSERT INTO user_balance (user_id, balance, total_charged, frozen, created, updated) "
                "VALUES (?, ?, 0, 0, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
                "balance=balance+?, total_charged=total_charged+?, updated=?",
                (uid, amount, time.time(), time.time(), amount, amount, time.time())
            )

        db.execute(
            "INSERT INTO billing_log (user_id, order_id, amount, action, created) VALUES (?,?,?,?,?)",
            (uid, order_id, amount, "wechat_pay", time.time())
        )
        db.commit()
        logger.info(f"[WeChat Pay] Delivered OK user={uid} order={order_id} product={product_id}")
    except Exception as e:
        logger.error(f"[WeChat Pay] Delivery error: {e}")
        db.rollback()
    finally:
        db.close()


def _deliver_membership(db, uid, product_id, amount):
    try:
        db.execute("ALTER TABLE users ADD COLUMN tier TEXT DEFAULT 'free'")
        db.execute("ALTER TABLE users ADD COLUMN vip_expires_at REAL DEFAULT 0")
        db.commit()
    except Exception:
        pass
    duration_map = {"vip_month": 30*86400, "vip_quarter": 90*86400, "vip_year": 365*86400}
    secs = duration_map.get(product_id, 30*86400)
    db.execute(
        "UPDATE users SET tier='pro', vip_expires_at=? WHERE id=?",
        (time.time() + secs, uid)
    )
    db.commit()


def _deliver_credits(db, uid, product_id, amount):
    credits_map = {"credits_100": 100, "credits_500": 500, "credits_1000": 1000}
    credits = credits_map.get(product_id, int(amount * 10))
    db.execute(
        "INSERT INTO user_balance (user_id, balance, total_charged, frozen, created, updated) "
        "VALUES (?, ?, 0, 0, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
        "balance=balance+?, total_charged=total_charged+?, updated=?",
        (uid, credits, time.time(), time.time(), credits, credits, time.time())
    )
    db.commit()


# ════════════════════════════════════════
# 4. Query order
# ════════════════════════════════════════
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


# ════════════════════════════════════════
# 5. Refund
# ════════════════════════════════════════
@router.post("/refund")
async def refund_order(request: Request):
    """Request refund"""
    user_id = get_user_id(request)
    if not user_id:
        return JSONResponse({"success": False, "error": "Please login"}, status_code=401)

    body = await request.json()
    order_id = body.get("order_id", "")
    reason = body.get("reason", "")

    db = _get_db()
    order = db.execute(
        "SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, user_id)
    ).fetchone()
    if not order or order["status"] != "completed":
        db.close()
        return JSONResponse({"success": False, "error": "Order not found or not completed"}, status_code=404)

    config = _load_wechat_config()
    if not config:
        db.close()
        return JSONResponse({"success": False, "error": "WeChat Pay not configured"}, status_code=502)

    refund_id = f"RF{int(time.time())}{uuid.uuid4().hex[:6]}"
    req_body = {
        "out_trade_no": order_id,
        "out_refund_no": refund_id,
        "reason": reason,
        "notify_url": NOTIFY_URL_NATIVE,
        "amount": {
            "refund": int(order["amount"] * 100),
            "total": int(order["amount"] * 100),
            "currency": "CNY",
        },
    }

    try:
        result = _call_wechat_api(config, "/v3/refund/domestic/refunds", req_body)
        db.execute("UPDATE orders SET status='refunded', updated=? WHERE id=?", (time.time(), order_id))
        db.execute(
            "UPDATE user_balance SET balance=balance+?, updated=? WHERE user_id=?",
            (order["amount"], time.time(), user_id)
        )
        db.commit()
        db.close()
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"[WeChat Pay] Refund failed: {e}")
        db.close()
        return JSONResponse({"success": False, "error": f"Refund failed: {str(e)[:100]}"}, status_code=502)
