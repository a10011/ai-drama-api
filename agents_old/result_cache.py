"""
生成结果缓存层（图片/视频/立绘等）

工作原理：
- key = md5(场景描述 + 模型ID + 分辨率参数)
- value = { "url": "...", "created_at": "...", "model": "..." }
- 存储位置: /www/wwwroot/api.mzsh.top/cache/

每个缓存条目有效期默认24小时，可配。
支持手动清除：/api/v1/cache/clear
"""

import logging
logger = logging.getLogger(__name__)

import os
import json
import hashlib
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

# 缓存根目录
CACHE_DIR = "/www/wwwroot/api.mzsh.top/cache"
# 默认过期时间（秒）
DEFAULT_TTL = 24 * 3600


def _ensure_dir():
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR, exist_ok=True)


def _make_key(prompt: str, model: str = "", extra: str = "") -> str:
    """生成缓存key"""
    raw = f"{prompt}|{model}|{extra}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> str:
    _ensure_dir()
    return os.path.join(CACHE_DIR, f"{key}.json")


def get(prompt: str, model: str = "", extra: str = "", ttl: int = DEFAULT_TTL) -> Optional[Dict[str, Any]]:
    """读取缓存"""
    key = _make_key(prompt, model, extra)
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        created = data.get("created_at", 0)
        if time.time() - created > ttl:
            os.remove(path)
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def set(prompt: str, model: str = "", extra: str = "", data: dict = None) -> str:
    """写入缓存"""
    key = _make_key(prompt, model, extra)
    entry = {
        "key": key,
        "prompt_preview": prompt[:100],
        "model": model,
        "created_at": time.time(),
        "expires_at": time.time() + DEFAULT_TTL,
    }
    if data:
        entry.update(data)
    path = _cache_path(key)
    with open(path, "w") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)
    return key


def get_by_key(key: str, ttl: int = DEFAULT_TTL) -> Optional[Dict]:
    """通过key直接读取"""
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        created = data.get("created_at", 0)
        if time.time() - created > ttl:
            os.remove(path)
            return None
        return data
    except (json.JSONDecodeError, OSError):
        return None


def download_and_cache(url: str, prompt: str, model: str = "", extra: str = "",
                       timeout: int = 30) -> Optional[str]:
    """
    从远程 URL 下载结果到本地缓存目录。
    返回本地文件路径，失败返回 None。
    """
    import urllib.request
    import ssl
    _ensure_dir()

    key = _make_key(prompt, model, extra)
    # 文件后缀推断
    ext = ".bin"
    if url:
        url_lower = url.lower()
        if ".jpg" in url_lower or ".jpeg" in url_lower or "image/jpeg" in url_lower:
            ext = ".jpg"
        elif ".png" in url_lower:
            ext = ".png"
        elif ".webp" in url_lower:
            ext = ".webp"
        elif ".mp4" in url_lower or "video/mp4" in url_lower:
            ext = ".mp4"
        elif ".wav" in url_lower or ".mp3" in url_lower or ".aac" in url_lower:
            ext = "." + url_lower.rsplit(".", 1)[-1]
        elif "audio/" in url_lower:
            ext = ".wav"

    local_path = os.path.join(CACHE_DIR, f"{key}{ext}")

    # 已存在则直接返回
    if os.path.exists(local_path) and os.path.getsize(local_path) > 100:
        return local_path

    # 下载
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            data = resp.read()
        if len(data) < 100:
            logger.warning(f"[DownloadCache] 文件过小 ({len(data)} bytes): {url[:60]}")
            return None
        with open(local_path, "wb") as f:
            f.write(data)
        # 同步写入缓存索引
        set(prompt, model, extra, {
            "url": url,
            "local_path": local_path,
            "model": model,
            "filesize": len(data)
        })
        logger.info(f"[DownloadCache] {model} OK -> {local_path} ({len(data)} bytes)")
        return local_path
    except Exception as e:
        logger.warning(f"[DownloadCache] 下载失败 {model}: {e} - {url[:60]}")
        return None


def clear(older_than_hours: int = 0):
    """清理缓存。older_than_hours>0只清超过N小时的，0清全部"""
    _ensure_dir()
    now = time.time()
    count = 0
    for fname in os.listdir(CACHE_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(CACHE_DIR, fname)
        try:
            if older_than_hours > 0:
                mtime = os.path.getmtime(path)
                if now - mtime < older_than_hours * 3600:
                    continue
            os.remove(path)
            count += 1
        except OSError:
            pass
    return count


import threading
import atexit

_cleanup_interval = 3600  # 每小时清理一次
_cleanup_running = False

def _auto_cleanup_worker():
    """后台线程：定期清理过期缓存"""
    global _cleanup_running
    _cleanup_running = True
    logger.info(f"[CacheCleanup] 后台清理线程启动，间隔={_cleanup_interval}s")
    while _cleanup_running:
        try:
            time.sleep(_cleanup_interval)
            removed = clear(older_than_hours=24)
            if removed > 0:
                logger.info(f"[CacheCleanup] 清理 {removed} 个过期缓存")
        except Exception as e:
            logger.warning(f"[CacheCleanup] 清理出错: {e}")
            time.sleep(60)

def _stop_cleanup():
    global _cleanup_running
    _cleanup_running = False

_cleanup_thread = threading.Thread(target=_auto_cleanup_worker, daemon=True, name="cache-cleanup")
_cleanup_thread.start()
atexit.register(_stop_cleanup)

def stats() -> dict:
    """缓存统计"""
    _ensure_dir()
    total = 0
    total_size = 0
    oldest = time.time()
    newest = 0
    models = {}

    for fname in os.listdir(CACHE_DIR):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(CACHE_DIR, fname)
        try:
            size = os.path.getsize(path)
            mtime = os.path.getmtime(path)
            total += 1
            total_size += size
            oldest = min(oldest, mtime)
            newest = max(newest, mtime)

            with open(path, "r") as f:
                data = json.load(f)
            model = data.get("model", "unknown")
            models[model] = models.get(model, 0) + 1
        except (OSError, json.JSONDecodeError):
            pass

    return {
        "total_entries": total,
        "total_size_kb": round(total_size / 1024, 1),
        "oldest": datetime.fromtimestamp(oldest).isoformat() if total > 0 else None,
        "newest": datetime.fromtimestamp(newest).isoformat() if total > 0 else None,
        "models": models,
    }
