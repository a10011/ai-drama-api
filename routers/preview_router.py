"""预览路由器 - 返回当前用户作品进度HTML，用于前端右侧面板展示"""
from fastapi import APIRouter, Header
import json, sqlite3, os, traceback

router = APIRouter()
DB = "/www/wwwroot/api.mzsh.top/data/short_drama.db"

ICON_MAP = {
    "script": "fa-file-lines", "character": "fa-user", "storyboard": "fa-clapperboard",
    "scene": "fa-mountain", "tts": "fa-microphone", "bgm": "fa-music",
    "subtitle": "fa-closed-captioning", "video": "fa-video", "composite": "fa-film"
}
LABEL_MAP = {
    "script": "剧本创作", "character": "角色设计", "storyboard": "分镜生成",
    "scene": "场景生成", "tts": "配音合成", "bgm": "BGM配乐",
    "subtitle": "字幕生成", "video": "视频生成", "composite": "视频合成"
}

def _fmt_html(key, data):
    """Format a single step's data into rich preview HTML"""
    if not data:
        return ""
    if key in ("script", "polish"):
        # 剧本 - 显示标题和完整内容
        title = data.get("title", data.get("name", "未命名短剧"))
        t = data.get("outline") or data.get("script") or data.get("content") or ""
        if len(t) > 800:
            t = t[:800] + "..."
        # 格式化换行
        t = t.replace("\n", "<br>")
        return f'<div class="preview-script"><div class="preview-title">{title}</div><div class="preview-text">{t}</div></div>'
    if key in ("character", "characters"):
        # 角色 - 显示头像+名字+类型+描述 卡片
        chars = data.get("characters") or []
        parts = []
        for ch in chars:
            n = ch.get("name", "未知")
            tp = ch.get("type", ch.get("role", ""))
            desc = ch.get("description", ch.get("desc", ""))
            personality = ch.get("personality", "")
            appearance = ch.get("appearance", "")
            gender = ch.get("gender", "")
            info = f"{desc} {personality}"[:60]
            avatar_html = f'<div class="preview-char-avatar">{n[0]}</div>'
            parts.append(
                f'<div class="preview-char-card">'
                f'{avatar_html}'
                f'<div class="preview-char-info">'
                f'<div class="preview-char-name">{n}</div>'
                f'<div class="preview-char-type">{tp}</div>'
                f'<div class="preview-char-desc">{info}</div>'
                f'</div></div>'
            )
        return f'<div class="preview-chars">{"".join(parts)}</div>' if parts else ""
    if key in ("storyboard", "shot"):
        # 分镜 - 每个镜头卡片
        shots = data.get("shots") or []
        parts = []
        for i, s in enumerate(shots):
            desc = s.get("description") or s.get("scene") or s.get("content") or ""
            camera = s.get("camera", "")
            duration = s.get("duration", "")
            dialogue = s.get("dialogue", "")
            bg = ["#1a1a2e", "#16213e", "#0f3460", "#1a1a3e", "#2d1b4e"][i % 5]
            meta = ""
            if camera: meta += f'<span class="preview-shot-tag"><i class="fas fa-camera"></i> {camera}</span>'
            if duration: meta += f'<span class="preview-shot-tag"><i class="fas fa-clock"></i> {duration}s</span>'
            if dialogue: meta += f'<span class="preview-shot-tag"><i class="fas fa-comment"></i> {dialogue[:50]}</span>'
            parts.append(
                f'<div class="preview-shot-card" style="border-left:3px solid #40E0E9;background:{bg};border-radius:8px;padding:10px;margin-bottom:6px">'
                f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">'
                f'<span style="color:#40E0E9;font-size:12px;font-weight:600">#{i+1}</span>'
                f'<span style="color:rgba(255,255,255,0.7);font-size:12px">{desc[:80]}</span>'
                f'</div>'
                f'<div style="display:flex;gap:6px;flex-wrap:wrap">{meta}</div>'
                f'</div>'
            )
        return f'<div style="max-height:200px;overflow-y:auto">{"".join(parts)}</div>' if parts else ""
    if key in ("scene", "scenes"):
        scenes = data.get("scenes") or data.get("shots") or []
        t = data.get("description") or data.get("text") or data.get("scene_name") or ""
        if isinstance(scenes, list) and len(scenes) > 0:
            parts = [f'<div class="preview-text">{s.get("description","")[:100]}</div>' for s in scenes[:3]]
            return '<div class="preview-scenes">' + "".join(parts) + "</div>"
        return f'<div class="preview-text">{t[:300]}</div>'
    if key in ("tts", "audio"):
        # 配音 - 显示每段配音状态
        audios = data.get("audios") or data.get("audio_files") or []
        if isinstance(audios, list):
            n = len(audios)
            done = sum(1 for a in audios if isinstance(a, dict) and a.get("status") == "done")
            return f'<div class="preview-tts"><i class="fas fa-microphone" style="color:#40E0E9"></i> 已合成 {done}/{n} 段语音</div>'
        return '<div class="preview-tts"><i class="fas fa-microphone" style="color:#40E0E9"></i> 配音已就绪</div>'
    if key == "bgm":
        n = data.get("name") or data.get("title") or data.get("genre", "BGM已匹配")
        return f'<div class="preview-bgm"><i class="fas fa-music" style="color:#40E0E9"></i> {n}</div>'
    if key == "subtitle":
        subs = data.get("subtitles") or data.get("subtitle_list") or []
        if isinstance(subs, list):
            n = len(subs)
            return f'<div class="preview-sub"><i class="fas fa-closed-captioning" style="color:#40E0E9"></i> {n} 条字幕</div>'
        return '<div class="preview-sub"><i class="fas fa-closed-captioning" style="color:#40E0E9"></i> 字幕已就绪</div>'
    if key in ("video", "composite"):
        url = data.get("url") or data.get("output") or data.get("video_url") or ""
        if url:
            return f'<video src="{url}" controls style="width:100%;border-radius:8px;max-height:200px;background:#000"></video>'
        return '<div class="preview-video"><i class="fas fa-video" style="color:#40E0E9"></i> 视频生成中...</div>'
    return ""

def _build_preview_html(step_results):
    """Build full preview HTML from step_results dict"""
    if not step_results:
        return ""
    parts = []
    for key, val in step_results.items():
        dd = val.get("data") if isinstance(val, dict) else val
        if not dd:
            continue
        h = _fmt_html(key, dd)
        if h:
            icon = ICON_MAP.get(key, "fa-circle")
            label = LABEL_MAP.get(key, key)
            parts.append(f'<div class="preview-box-header"><i class="fas {icon}"></i> {label}</div>')
            parts.append(h)
            parts.append('<div style="height:8px"></div>')
    return "".join(parts)

@router.get("/preview")
def get_preview(authorization: str = Header(None)):
    """预览接口 - 永远返回空，预览由前端子组件通过previewControl推送"""
    return {"html": "", "text": "", "count": 0, "pid": ""}
