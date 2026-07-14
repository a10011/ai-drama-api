"""用户认证工具 - 从请求中提取 user_id
[优化] 使用 app_db 全局连接池，避免每次请求新建 SQLite 连接。
"""
from fastapi import Request
from app_db import fetchone


def get_user_id(request: Request) -> int:
    """从请求提取 user_id：优先 state.user_id，其次 Authorization header"""
    try:
        # 优先使用中间件注入的 state.user_id
        uid = getattr(request.state, "user_id", 0)
        if uid and uid > 0:
            return uid

        # Bearer token 格式
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
        else:
            return 0

        if not token:
            return 0
        # 仅按 token 匹配（不再 OR username，防冒充）
        row = fetchone("SELECT id FROM users WHERE token = ?", (token,))
        return row["id"] if row else 0
    except Exception:
        return 0
