"""
总导演聊天 + 知识库 API
端点: /api/v1/director/*
"""
from fastapi import APIRouter, Request
from pydantic import BaseModel
import os, json, time, hashlib
router = APIRouter(prefix="/api/v1/director", tags=["director"])


def _get_deepseek_key() -> str:
    """Get DeepSeek API key from centralized config"""
    try:
        from services.ai_providers import _get_key
        return _get_key("deepseek")
    except Exception:
        return os.environ.get("DEEPSEEK_API_KEY", "")


def _get_deepseek_base_url() -> str:
    """Get DeepSeek base URL from centralized config"""
    try:
        from services.ai_providers import _get_base_url
        url = _get_base_url("deepseek")
        return url or "https://api.deepseek.com/v1"
    except Exception:
        return "https://api.deepseek.com/v1"


def _chat_director(messages: list) -> str:
    import requests
    api_key = _get_deepseek_key()
    base_url = _get_deepseek_base_url()
    
    if not api_key:
        raise Exception("DeepSeek API key not configured")
    
    payload = {
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.8,
        "max_tokens": 300
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }
    r = requests.post(
        f"{base_url}/chat/completions",
        json=payload, headers=headers, timeout=(10, 20)
    )
    if r.status_code != 200:
        raise Exception(f"DeepSeek {r.status_code}: {r.text[:100]}")
    return r.json()["choices"][0]["message"]["content"]

CONFIG_DIR = "/www/wwwroot/api.mzsh.top/data/director"
os.makedirs(CONFIG_DIR, exist_ok=True)
KNOWLEDGE_DIR = os.path.join(CONFIG_DIR, "knowledge")
os.makedirs(KNOWLEDGE_DIR, exist_ok=True)

# ---- 模式 Prompt ----
MODE_PROMPTS = {
    "短剧": """你是短剧总导演。专注短剧赛道：
- 每集1-2分钟，快节奏高反转
- 竖屏9:16构图，视觉冲击力强
- 爽点密集，开头3秒勾住观众
- 对话精简，用画面讲故事
- 古装/都市/甜宠/逆袭各赛道都懂""",
    "大片": """你是电影总导演。专注电影级别制作：
- 宽银幕16:9构图，电影级镜头语言
- 重视画面调色、光影、氛围
- 角色弧光和情感层次丰富
- 注重场景调度和演员走位
- 配乐和音效与画面深度配合""",
}


class ChatRequest(BaseModel):
    message: str
    mode: str = "短剧"
    history: list = []


class LearnRequest(BaseModel):
    text: str
    mode: str = "短剧"


# ---- 聊天 ----
@router.post("/chat")
async def director_chat(req: ChatRequest):
    mode = req.mode if req.mode in MODE_PROMPTS else "短剧"
    system = MODE_PROMPTS[mode] + "\n\n额外要求：\n- 回复简洁直接，不超过200字\n- 多用表情符号增强表达\n- 如果用户问剧本相关，给出具体建议而非泛泛而谈"
    
    # 构建历史消息
    msgs = [{"role": "system", "content": system}]
    for h in req.history[-10:]:
        role = "assistant" if h.get("role") == "assistant" else "user"
        msgs.append({"role": role, "content": h.get("content", "")[:2000]})
    msgs.append({"role": "user", "content": req.message[:2000]})
    
    try:
        reply = _chat_director(msgs)
        if not reply:
            reply = "嗯，让我想想..."
    except Exception as e:
        reply = f"导演正在思考...({str(e)[:80]})"
    
    # 存聊天记录
    log_file = os.path.join(CONFIG_DIR, "chat_log.jsonl")
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(json.dumps({"t": time.time(), "mode": mode, "user": req.message[:200], "reply": (reply or "")[:200]}, ensure_ascii=False) + '\n')
    
    return {"success": True, "reply": reply or "好的，我明白了。"}


# ---- 学习知识 ----
@router.post("/learn")
async def director_learn(req: LearnRequest):
    text = req.text.strip()
    if len(text) < 20:
        return {"success": False, "error": "内容太短，至少20字"}
    
    # 生成文件名
    h = hashlib.md5(text[:200].encode()).hexdigest()[:8]
    fname = f"learn_{int(time.time())}_{h}.md"
    fpath = os.path.join(KNOWLEDGE_DIR, fname)
    
    # 提取主题
    topics = []
    for kw in ["古装","都市","甜宠","逆袭","悬疑","科幻","仙侠","武侠","分镜","角色","剧本","拍摄"]:
        if kw in text:
            topics.append(kw)
    
    with open(fpath, 'w', encoding='utf-8') as f:
        f.write(f"# 导演学习笔记\n\n时间：{time.strftime('%Y-%m-%d %H:%M')}\n模式：{req.mode}\n\n{text}\n")
    
    return {"success": True, "file": fname, "topics": topics or ["通用"]}


# ---- 状态 ----
@router.get("/status")
async def director_status():
    files = [f for f in os.listdir(KNOWLEDGE_DIR) if f.endswith('.md')]
    chat_count = 0
    log_file = os.path.join(CONFIG_DIR, "chat_log.jsonl")
    if os.path.exists(log_file):
        with open(log_file) as f:
            chat_count = sum(1 for _ in f)
    return {"knowledge_files": len(files), "experience_count": chat_count}


# ---- 知识列表 ----
@router.get("/knowledge")
async def list_knowledge():
    files = []
    for f in os.listdir(KNOWLEDGE_DIR):
        if f.endswith('.md'):
            fp = os.path.join(KNOWLEDGE_DIR, f)
            files.append({"name": f, "size": os.path.getsize(fp)})
    files.sort(key=lambda x: x["name"], reverse=True)
    return {"files": files[:20]}


# ---- 经验摘要 ----
@router.get("/experiences")
async def list_experiences():
    log_file = os.path.join(CONFIG_DIR, "chat_log.jsonl")
    if not os.path.exists(log_file):
        return {"recent_summary": "暂无经验", "count": 0}
    
    lines = []
    with open(log_file) as f:
        for line in f:
            try: lines.append(json.loads(line))
            except Exception: pass
    
    recent = [f"{l.get('user','')[:60]} → {l.get('reply','')[:60]}" for l in lines[-20:]]
    return {"recent_summary": "\n".join(recent), "count": len(lines)}


# ---- 查看知识 ----
@router.get("/knowledge/{name}")
async def get_knowledge(name: str):
    fpath = os.path.join(KNOWLEDGE_DIR, name)
    if not os.path.exists(fpath) or '..' in name:
        return {"content": "文件不存在", "total_chars": 0}
    with open(fpath, encoding='utf-8') as f:
        content = f.read()
    return {"content": content, "total_chars": len(content)}