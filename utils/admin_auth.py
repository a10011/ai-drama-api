"""管理员鉴权依赖

通过环境变量 ADMIN_TOKENS 配置管理员 token 列表（逗号分隔）。
持有任一管理员 token 的请求才能访问敏感接口（密钥配置、模型重置等）。

用法：
    from utils.admin_auth import require_admin
    @router.get("/keys", dependencies=[Depends(require_admin)])
    def list_keys(): ...
"""
import os
from fastapi import Depends, HTTPException, Request, status

from utils.auth_util import get_user_id


def _admin_tokens() -> set[str]:
    """从环境变量读取管理员 token 集合"""
    raw = os.environ.get("ADMIN_TOKENS", "").strip()
    if not raw:
        return set()
    return {t.strip() for t in raw.split(",") if t.strip()}


def is_admin_request(request: Request) -> bool:
    """判断当前请求是否持管理员 token

    判定顺序：
    1. Authorization 头中的 token 命中 ADMIN_TOKENS 白名单
    2. （兜底）无白名单时返回 False，强制最小权限
    """
    tokens = _admin_tokens()
    if not tokens:
        return False
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
    elif auth.startswith("*** "):
        token = auth.split("*** ")[-1].strip()
    else:
        token = ""
    return bool(token) and token in tokens


def require_admin(request: Request):
    """FastAPI 依赖：非管理员请求直接 403"""
    if not is_admin_request(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return True
