"""
共享上下文 — 所有智能体通过它读写数据
每个 project 对应 /www/wwwroot/api.mzsh.top/data/projects/{project_id}/context.json
"""
import json, os, time, logging

logger = logging.getLogger("api.context")

CONTEXT_DIR = os.environ.get("CONTEXT_DIR", "/www/wwwroot/api.mzsh.top/data/projects")

def _path(project_id: str) -> str:
    d = os.path.join(CONTEXT_DIR, project_id)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "context.json")

def read(project_id: str) -> dict:
    """读上下文，不存在则返回空字典"""
    p = _path(project_id)
    if not os.path.exists(p):
        return {}
    with open(p, encoding='utf-8') as f:
        return json.load(f)

def write(project_id: str, ctx: dict):
    """写上下文，自动加时间戳"""
    ctx["_updated"] = time.time()
    p = _path(project_id)
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(ctx, f, ensure_ascii=False, indent=2)

def update(project_id: str, **kwargs):
    """局部更新上下文"""
    ctx = read(project_id)
    ctx.update(kwargs)
    ctx["_updated"] = time.time()
    write(project_id, ctx)
    return ctx
