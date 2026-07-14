"""API Key 管理路由

提供统一的 key 管理，支持后台页面配置。
所有 provider 从此读取，不再硬编码。
"""
import json, os, logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional, Dict
from utils.admin_auth import require_admin

logger = logging.getLogger("api.keys")
router = APIRouter(prefix="/api/v1/config", tags=["配置"])

# Key 配置文件路径
KEYS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
KEYS_FILE = os.path.join(KEYS_DIR, "api_keys.json")


# ---- 默认模板 ----
DEFAULT_KEYS = {
    "aliyun_bailian": {
        "label": "阿里云百炼",
        "key": "",
        "desc": "通义千问 / 万相生图 / CosyVoice TTS / VideoRetalk 口型",
        "base_url": "https://dashscope.aliyuncs.com"
    },
    "deepseek": {
        "label": "DeepSeek 官方",
        "key": "",
        "desc": "deepseek-chat（剧本/角色/分镜/客服等）",
        "base_url": "https://api.deepseek.com/v1"
    },
    "ark_volc": {
        "label": "火山方舟 ARK",
        "key": "",
        "desc": "豆包 Seedream（生图）/ Seedance（视频）",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3"
    },
    "jimeng_ak": {
        "label": "即梦 AccessKey",
        "key": "",
        "desc": "火山引擎 AK/SK 签名鉴权用"
    },
    "jimeng_sk": {
        "label": "即梦 SecretKey",
        "key": "",
        "secret": True
    },
    "kling_ak": {
        "label": "可灵 AccessKey",
        "key": "",
        "desc": "可灵视频生成"
    },
    "kling_sk": {
        "label": "可灵 SecretKey",
        "key": "",
        "secret": True
    },
    "agnes": {
        "label": "Agnes Hub",
        "key": "",
        "desc": "备用生图",
        "base_url": "https://apihub.agnes-ai.com/v1"
    }
}


def _load_keys() -> dict:
    if not os.path.exists(KEYS_FILE):
        return dict(DEFAULT_KEYS)
    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"读取 keys 失败: {e}")
        return dict(DEFAULT_KEYS)


def _save_keys(data: dict):
    os.makedirs(KEYS_DIR, exist_ok=True)
    with open(KEYS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_key(key_id: str) -> str:
    """获取指定 key 的明文值"""
    keys = _load_keys()
    return keys.get(key_id, {}).get("key", "")


def get_base_url(key_id: str) -> str:
    keys = _load_keys()
    return keys.get(key_id, {}).get("base_url", "")


class KeyUpdateRequest(BaseModel):
    updates: Dict[str, dict]


@router.get("/keys", dependencies=[Depends(require_admin)])
def list_keys():
    """列出所有 key（值脱敏）"""
    keys = _load_keys()
    result = {}
    for k, v in keys.items():
        entry = dict(v)
        raw = entry.get("key", "")
        if raw and len(raw) > 8:
            entry["key"] = raw[:6] + "…" + raw[-4:]
        elif raw:
            entry["key"] = "·" * len(raw)
        entry.pop("secret", None)
        result[k] = entry
    return {"code": 0, "data": result}


@router.post("/keys", dependencies=[Depends(require_admin)])
def update_keys(req: KeyUpdateRequest):
    """更新 key"""
    keys = _load_keys()
    for key_id, upd in req.updates.items():
        if key_id not in keys:
            raise HTTPException(400, f"未知 key: {key_id}")
        for field in ("key", "desc", "base_url"):
            val = upd.get(field)
            if val is not None and val != "":
                keys[key_id][field] = str(val).strip()
    _save_keys(keys)
    return {"code": 0, "message": "已更新", "updated": list(req.updates.keys())}
