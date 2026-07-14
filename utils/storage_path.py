"""
utils/storage_path.py — 存储路径统一工具（v2）

取代 path_util.py 和所有散落的 URL 拼接代码。
所有 AI 产物按 project_id 隔离，存放在 /www/wwwroot/storage/projects/{project_id}/ 下。

使用方式：
    from utils.storage_path import figure_path, scene_path, ... , local_to_url

设计原则：
    - 每个文件类型一个独立函数，签名统一为 (project_id, filename, ...)
    - 返回 (本地绝对路径, 公网 URL) 二元组
    - project_id 为空时后台生成目录（兼容无项目阶段）
    - 所有函数同时兼容新旧目录结构（通过 FILE_TYPE_MAP 控制）
"""

import os
import time

from app_config import BASE_URL

# ── 常量 ──────────────────────────────────────────────────

STORAGE_ROOT = "/www/wwwroot/storage"
PROJECTS_DIR = f"{STORAGE_ROOT}/projects"

# 文件类型目录映射：内部类型名 → 子目录名
FILE_TYPE_MAP = {
    "figure":   "figures",
    "scene":    "scenes",
    "tts":      "tts",
    "bgm":      "bgm",
    "video":    "videos",
    "subtitle": "subtitle",
    "final":    "final",
}

# 扩展名映射（预留，不做强制校验，仅用于命名提示）
EXT_MAP = {
    "figure":   ".jpg",
    "scene":    ".jpg",
    "tts":      ".mp3",
    "bgm":      ".mp3",
    "video":    ".mp4",
    "subtitle": ".srt",
    "final":    ".mp4",
}


# ── 核心函数 ──────────────────────────────────────────────

def _project_dir(project_id: str) -> str:
    """返回 project_id 对应的项目目录绝对路径。"""
    return f"{PROJECTS_DIR}/{project_id}"


def _ensure_project_dir(project_id: str) -> str:
    """创建并返回项目目录。"""
    d = _project_dir(project_id)
    os.makedirs(d, exist_ok=True)
    return d


def _type_dir(project_id: str, media_type: str) -> str:
    """返回某类型文件的子目录路径。"""
    sub = FILE_TYPE_MAP.get(media_type, media_type)
    return f"{_ensure_project_dir(project_id)}/{sub}"


def _ensure_type_dir(project_id: str, media_type: str) -> str:
    """创建并返回某类型子目录。"""
    d = _type_dir(project_id, media_type)
    os.makedirs(d, exist_ok=True)
    return d


def local_to_url(local_path: str) -> str:
    """本地绝对路径 → 公网 URL（基于 BASE_URL，无硬编码域名）。"""
    if not local_path:
        return ""
    if local_path.startswith("http://") or local_path.startswith("https://"):
        return local_path
    if local_path.startswith(STORAGE_ROOT):
        rel = local_path[len(STORAGE_ROOT):]
        return f"{BASE_URL}/storage{rel}"
    if local_path.startswith("/www/wwwroot/"):
        rel = local_path[len("/www/wwwroot"):]
        return f"{BASE_URL}{rel}"
    if local_path.startswith("/storage"):
        return f"{BASE_URL}{local_path}"
    return f"{BASE_URL}/{local_path.lstrip('/')}"


def url_to_local(url: str) -> str:
    """公网 URL → 本地绝对路径（反向转换）。"""
    if not url:
        return ""
    if url.startswith("/www/wwwroot"):
        return url
    if url.startswith("/storage"):
        return f"{STORAGE_ROOT}{url[8:]}"
    if url.startswith("http://") or url.startswith("https://"):
        if BASE_URL and url.startswith(BASE_URL):
            rel = url[len(BASE_URL):]
            return f"{STORAGE_ROOT}{rel[8:]}"
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return f"{STORAGE_ROOT}{parsed.path[8:]}" if parsed.path.startswith("/storage") else url
    return url


def _auto_filename(media_type: str, extension: str = "") -> str:
    """自动生成文件名：{类型}_{毫秒时间戳}.{ext}"""
    ext = extension or EXT_MAP.get(media_type, ".bin")
    return f"{media_type}_{int(time.time() * 1000)}{ext}"


def figure_path(project_id: str, filename: str = "", extension: str = ".jpg") -> tuple:
    """
    角色锁脸图路径。
    返回：(本地路径, 公网URL)
    """
    fname = filename or _auto_filename("figure", extension)
    local = f"{_ensure_type_dir(project_id, 'figure')}/{fname}"
    return local, local_to_url(local)


def scene_path(project_id: str, filename: str = "", extension: str = ".jpg") -> tuple:
    """
    场景图路径。
    返回：(本地路径, 公网URL)
    """
    fname = filename or _auto_filename("scene", extension)
    local = f"{_ensure_type_dir(project_id, 'scene')}/{fname}"
    return local, local_to_url(local)


def tts_path(project_id: str, filename: str = "", extension: str = ".mp3") -> tuple:
    """
    配音音频路径。
    返回：(本地路径, 公网URL)
    """
    fname = filename or _auto_filename("tts", extension)
    local = f"{_ensure_type_dir(project_id, 'tts')}/{fname}"
    return local, local_to_url(local)


def bgm_path(project_id: str, filename: str = "", extension: str = ".mp3") -> tuple:
    """
    背景音乐路径。
    返回：(本地路径, 公网URL)
    """
    fname = filename or _auto_filename("bgm", extension)
    local = f"{_ensure_type_dir(project_id, 'bgm')}/{fname}"
    return local, local_to_url(local)


def video_path(project_id: str, filename: str = "", extension: str = ".mp4") -> tuple:
    """
    单镜头视频路径（含合成音频的镜头视频）。
    返回：(本地路径, 公网URL)
    """
    fname = filename or _auto_filename("video", extension)
    local = f"{_ensure_type_dir(project_id, 'video')}/{fname}"
    return local, local_to_url(local)


def subtitle_path(project_id: str, filename: str = "", extension: str = ".srt") -> tuple:
    """
    字幕文件路径。
    返回：(本地路径, 公网URL)
    """
    fname = filename or _auto_filename("subtitle", extension)
    local = f"{_ensure_type_dir(project_id, 'subtitle')}/{fname}"
    return local, local_to_url(local)


def final_path(project_id: str, filename: str = "", extension: str = ".mp4") -> tuple:
    """
    最终合成视频路径。
    返回：(本地路径, 公网URL)
    """
    fname = filename or _auto_filename("final", extension)
    local = f"{_ensure_type_dir(project_id, 'final')}/{fname}"
    return local, local_to_url(local)


# ── 批量/通用工具 ──────────────────────────────────────────

def all_type_dirs(project_id: str) -> dict:
    """
    返回 project_id 下所有类型目录 dict。
    确保所有目录存在。
    """
    result = {}
    for key, sub in FILE_TYPE_MAP.items():
        d = f"{_ensure_project_dir(project_id)}/{sub}"
        os.makedirs(d, exist_ok=True)
        result[sub] = d
    return result


def build_path(project_id: str, media_type: str, filename: str = "") -> tuple:
    """
    通用路径构建（不区分文件类型时使用）。
    返回：(本地路径, 公网URL)
    """
    fname = filename or _auto_filename(media_type)
    local = f"{_ensure_type_dir(project_id, media_type)}/{fname}"
    return local, local_to_url(local)


def store_content(project_id: str, media_type: str, filename: str,
                  content: bytes) -> tuple:
    """
    写入字节内容到统一路径，返回 (本地路径, 公网URL)。
    用法：
        local, url = store_content("123", "subtitle", "act1.srt", srt_bytes)
    """
    local, url = build_path(project_id, media_type, filename)
    os.makedirs(os.path.dirname(local), exist_ok=True)
    with open(local, "wb") as f:
        f.write(content)
    return local, url
