"""企业微信消息接收 — URL验证 + 消息回调"""
import hashlib
import base64
import struct
import logging
from Crypto.Cipher import AES
from fastapi import APIRouter, Request, Query
from fastapi.responses import PlainTextResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/wechat", tags=["wechat"])

WECHAT_TOKEN = "PBztR9dqJpoATqx"
WECHAT_ENCODING_AES_KEY = "rqbzR81aOs1CyppYJtqjOFfFFd2a9oAJclokez34Jyt"
WECHAT_CORP_ID = ""  # 企业ID，消息解密时需要

def _sha1_sign(*args) -> str:
    """SHA1签名"""
    tmp_list = sorted(args)
    tmp_str = "".join(tmp_list)
    return hashlib.sha1(tmp_str.encode()).hexdigest()

def _aes_decrypt(encrypted: str) -> str:
    """AES解密企业微信消息"""
    try:
        aes_key = base64.b64decode(WECHAT_ENCODING_AES_KEY + "=")
        cipher = AES.new(aes_key, AES.MODE_CBC, aes_key[:16])
        decrypted = cipher.decrypt(base64.b64decode(encrypted))
        # PKCS7 unpadding
        pad = decrypted[-1]
        decrypted = decrypted[:-pad]
        # 格式: 16字节random + 4字节msg_len + msg + corp_id
        msg_len = struct.unpack(">I", decrypted[16:20])[0]
        msg = decrypted[20:20 + msg_len].decode("utf-8")
        return msg
    except Exception as e:
        logger.error(f"[WeChat] AES解密失败: {e}")
        raise

@router.get("/callback")
async def wechat_verify(
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...)
):
    """企业微信URL验证"""
    try:
        # 1. 验证签名
        sign = _sha1_sign(WECHAT_TOKEN, timestamp, nonce, echostr)
        if sign != msg_signature:
            logger.warning(f"[WeChat] 签名校验失败 expected={sign[:20]} got={msg_signature[:20]}...")
            return PlainTextResponse("signature failed", status_code=403)

        # 2. 解密echostr
        plain = _aes_decrypt(echostr)
        logger.info(f"[WeChat] URL验证通过, echostr={plain[:30]}...")
        return PlainTextResponse(plain)
    except Exception as e:
        logger.error(f"[WeChat] 验证异常: {e}")
        return PlainTextResponse(f"error: {e}", status_code=500)

@router.post("/callback")
async def wechat_message(request: Request):
    """接收企业微信消息推送"""
    try:
        body = await request.body()
        body_str = body.decode("utf-8")
        logger.info(f"[WeChat] 收到消息: {body_str[:300]}")
        # TODO: 解析XML → 提取消息内容 → 响应（如每日账单）
        return PlainTextResponse("success")
    except Exception as e:
        logger.error(f"[WeChat] 消息处理异常: {e}")
        return PlainTextResponse("error", status_code=500)