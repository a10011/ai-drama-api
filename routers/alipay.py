"""支付宝支付路由 — AI 收产品 for 短剧平台"""
import json, time, logging, uuid, base64, os
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from utils.auth_util import get_user_id

router = APIRouter(prefix="/api/v1/alipay", tags=["alipay"])
logger = logging.getLogger(__name__)

# ── 配置 ──
APP_ID = os.environ.get("ALIPAY_APP_ID", "2021006169683274")
CONFIG_DIR = os.environ.get("ALIPAY_CONFIG_DIR", "/www/wwwroot/api.mzsh.top/config")
NOTIFY_URL = os.environ.get("ALIPAY_NOTIFY_URL", "https://api.mzsh.top/api/v1/alipay/notify")
RETURN_URL = os.environ.get("ALIPAY_RETURN_URL", "https://ai.mzsh.top/payment/success")
DB_PATH = os.environ.get("ALIPAY_DB_PATH", "/www/wwwroot/api.mzsh.top/data/short_drama.db")

PRODUCTS = {
    "vip_month":   {"name": "月度会员",    "price": 29.90},
    "vip_quarter": {"name": "季度会员",    "price": 79.90},
    "vip_year":    {"name": "年度会员",    "price": 299.00},
    "credits_100": {"name": "100创作点数", "price": 9.90},
    "credits_500": {"name": "500创作点数", "price": 39.90},
    "credits_1000":{"name": "1000创作点数","price": 69.90},
}

def _load_key(path):
    with open(path) as f:
        return f.read()

def _rsa_sign(content: str, private_key: str) -> str:
    """RSA-SHA256 签名"""
    from cryptography.hazmat.primitives import hashes, serialization, padding as asym_padding
    from cryptography.hazmat.backends import default_backend
    key = serialization.load_pem_private_key(private_key.encode(), password=None, backend=default_backend())
    sig = key.sign(content.encode("utf-8"),
                   asym_padding.PKCS1v15(),
                   hashes.SHA256())
    return base64.b64encode(sig).decode()

def _rsa_verify(content: str, signature: str, public_key: str) -> bool:
    """RSA-SHA256 验签"""
    from cryptography.hazmat.primitives import hashes, serialization, asym_padding
    from cryptography.hazmat.backends import default_backend
    try:
        key = serialization.load_pem_public_key(public_key.encode(), backend=default_backend())
        key.verify(base64.b64decode(signature),
                   content.encode("utf-8"),
                   asym_padding.PKCS1v15(),
                   hashes.SHA256())
        return True
    except Exception:
        return False

def _build_sign_string(params: dict) -> str:
    """Alipay 签名串：按 key 排序 key=value&... 排除 sign 和空值"""
    keys = sorted(k for k in params if k != "sign" and params[k] is not None and params[k] != "")
    return "&".join(f"{k}={params[k]}" for k in keys)

def _get_db():
    import sqlite3
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db

# ════════════════════════════════════════
# ① 创建支付订单 → 返回支付宝支付链接
# ════════════════════════════════════════
@router.post("/create")
async def create_payment(request: Request):
    """
    请求: {"product_id": "vip_month", "quantity": 1}
    返回: {"success": true, "data": {"pay_url": "https://openapi.alipay.com/...", "order_id": "AL..."}}
    """
    user_id = get_user_id(request)
    if not user_id:
        return JSONResponse({"success": False, "error": "请先登录"}, status_code=401)

    body = await request.json()
    product_id = body.get("product_id", "")
    product = PRODUCTS.get(product_id)
    if not product:
        return JSONResponse({"success": False, "error": "商品不存在"})

    quantity = max(int(body.get("quantity", 1)), 1)
    total = round(product["price"] * quantity, 2)
    order_id = f"AL{int(time.time())}{uuid.uuid4().hex[:8]}"

    # 本地创建订单
    db = _get_db()
    db.execute(
        "INSERT INTO orders (id, user_id, title, amount, quantity, status, created, updated) VALUES (?,?,?,?,?,?,?,?)",
        (order_id, user_id, product["name"], total, quantity, "pending", time.time(), time.time())
    )
    db.commit()
    db.close()
    logger.info(f"[支付宝] 创建订单 {order_id} ¥{total} {product['name']} user={user_id}")

    # 构造支付宝跳转 URL（直接页面跳转支付，无需 SDK）
    import urllib.parse
    private_key = _load_key(f"{CONFIG_DIR}/alipay_private_key.pem")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    biz_content = json.dumps({
        "out_trade_no": order_id,
        "total_amount": str(total),
        "subject": product["name"],
        "product_code": "FAST_INSTANT_TRADE_PAY",
        "body": f"AI短剧-{product['name']}",
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


# ════════════════════════════════════════
# ② 支付宝异步通知（最重要！）
# ════════════════════════════════════════
@router.post("/notify")
async def alipay_notify(request: Request):
    """支付宝回调验签 → 更新订单 → 发货（充值余额/开会员）"""
    form = dict(await request.form())
    logger.info(f"[支付宝] 收到通知: {json.dumps(form, ensure_ascii=False)[:300]}")

    # 验签
    sign = form.get("sign", "")
    sign_content = _build_sign_string(form)
    public_key = _load_key(f"{CONFIG_DIR}/alipay_public_key.pem")
    if not _rsa_verify(sign_content, sign, public_key):
        logger.warning("[支付宝] 签名验证失败，拒绝处理")
        return "failure"

    out_trade_no = form.get("out_trade_no", "")
    trade_status = form.get("trade_status", "")
    logger.info(f"[支付宝] 订单 {out_trade_no} 状态={trade_status}")

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
                pid = order["product_id"]

                # 充值到余额
                db.execute(
                    "INSERT INTO user_balance (user_id, balance, total_charged, frozen, created, updated) VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET balance=balance+?, total_charged=total_charged+?, updated=?",
                    (uid, amt, amt, 0, time.time(), time.time(), amt, amt, time.time())
                )
                # 记录日志
                db.execute(
                    "INSERT INTO billing_log (user_id, order_id, amount, action, created) VALUES (?,?,?,?,?)",
                    (uid, out_trade_no, amt, "alipay_recharge", time.time())
                )
                logger.info(f"[支付宝] ✅ 发货成功 user={uid} order={out_trade_no} amount={amt}")
            db.commit()
        except Exception as e:
            logger.error(f"[支付宝] 发货异常: {e}")
            db.rollback()
        finally:
            db.close()

    return "success"


# ════════════════════════════════════════
# ③ 查询订单
# ════════════════════════════════════════
@router.get("/query/{order_id}")
async def query_order(order_id: str, request: Request):
    user_id = get_user_id(request)
    db = _get_db()
    order = db.execute("SELECT * FROM orders WHERE id=? AND user_id=?", (order_id, user_id)).fetchone()
    db.close()
    if not order:
        return JSONResponse({"success": False, "error": "订单不存在"})
    return {"success": True, "data": dict(order)}
