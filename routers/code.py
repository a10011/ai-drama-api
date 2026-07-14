"""Claude Code 助手"""
from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse
from services.model_client import call_llm
import logging

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/code", tags=["code"])

SYSTEM_PROMPT = """你是 Claude，一个专业的编程助手 + 服务器运维助手。

## 服务器信息
- SSH 凭据通过环境变量或配置提供，不在此处方文
- 后端目录: /www/wwwroot/api.mzsh.top/
- 前端目录: /www/wwwroot/ai.mzsh.top/
- PM2: ai-drama-api (cwd=/www/wwwroot/api.mzsh.top)
- Nginx配置: /etc/nginx/sites-enabled/ai.mzsh.top
- Web: ai.mzsh.top (Vue 3) | API: api.mzsh.top (FastAPI, 端口8000)

## 可用命令
- 重启后端: pm2 restart ai-drama-api
- 前端构建: cd /www/wwwroot/ai.mzsh.top && npm run build
- Nginx测试+重载: nginx -t && nginx -s reload

## 规则
- 改代码后提示用户执行对应重启/构建命令
- 先理解问题，再动手
- 代码要有注释
- 给出可运行的完整方案，标注修改的文件和行号
- 中文回复，代码注释用中文
- 不确定的地方直接问，不要猜
- 绝不暴露或询问 SSH 凭据"""

@router.get("/chat")
async def code_chat(msg: str = Query(..., description="用户消息")):
    """Claude 代码助手"""
    try:
        result = call_llm(
            prompt=msg,
            system=SYSTEM_PROMPT,
            model="claude-sonnet-4-20250514",
            timeout=60,
            max_tokens=4096
        )
        if result.get("success"):
            return {"reply": result["text"].strip(), "model": "claude-sonnet-4-20250514"}
        return {"reply": f"出错: {result.get('error')}", "model": "claude-sonnet-4-20250514"}
    except Exception as e:
        return {"reply": f"异常: {e}", "model": "claude-sonnet-4-20250514"}


@router.get("/exec")
async def code_exec(cmd: str = Query(..., description="要执行的命令")):
    """执行服务器命令（受限）"""
    import subprocess, shlex
    # 允许的命令白名单
    # 白名单仅含只读诊断命令，防止服务器被远程操控
    SAFE_PREFIXES = [
        "ls", "cat", "grep", "find", "head", "tail",
        "python3 -m py_compile",
        "nginx -t",
        "du ", "df ", "free ", "uptime", "whoami",
        "echo ", "pwd", "wc ", "sort ", "uniq ",
        "systemctl status", "pm2 status", "pm2 list",
    ]
    is_safe = any(cmd.strip().startswith(p) for p in SAFE_PREFIXES)
    if not is_safe:
        return {"ok": False, "output": f"命令不允许: {cmd[:80]}", "cmd": cmd}

    try:
        args = shlex.split(cmd)
        result = subprocess.run(
            args if isinstance(args, list) else ["/bin/bash", "-c", cmd],
            capture_output=True, text=True, timeout=30,
            cwd="/www/wwwroot/api.mzsh.top"
        )
        output = result.stdout
        if result.stderr:
            output += "\n[stderr]\n" + result.stderr
        return {"ok": True, "output": output.strip() or "(no output)", "cmd": cmd[:200], "rc": result.returncode}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": "命令超时(30s)", "cmd": cmd[:200]}
    except Exception as e:
        return {"ok": False, "output": str(e), "cmd": cmd[:200]}

CODE_PAGE = None  # loaded from disk on first request

def _load_page():
    global CODE_PAGE
    if CODE_PAGE is None:
        import os
        path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "code_page.html")
        with open(path, "r", encoding="utf-8") as f:
            CODE_PAGE = f.read()
    return CODE_PAGE

@router.get("", response_class=HTMLResponse)
async def code_page():
    """Claude Code 对话页面"""
    return HTMLResponse(content=_load_page())
