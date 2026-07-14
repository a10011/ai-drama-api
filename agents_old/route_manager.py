# NOTE: run_with_fallback 已禁用降级，现在所有路由走 model_client 直接重试
"""智能体路由容错管理器 — 每个模型自动尝试主/备用，连接失败就换"""
import logging, time, random
from typing import Optional, Callable, Any
from .result_cache import get as cache_get, set as cache_set

logger = logging.getLogger(__name__)

# ============================================================
# 模型提供商注册表（主+备用链）
# ============================================================
# 顺序 = 优先级链：主->备1->备2->...
# Static=True 表示不需要 API 调用的本地方法（如 FFmpeg），始终成功

IMAGE_PROVIDERS = [
    {"name": "seedream",    "provider": "ARKImageProvider",    "service": "ark",      "model": "doubao-seedream-4-0-250828",     "timeout": 60},
    {"name": "hidream",     "provider": "HiDreamImageProvider","service": "hidream",  "model": "z1-image",                       "timeout": 60},
    {"name": "agnes",       "provider": "AgnesAIProvider",     "service": "agnes",    "model": "agnes-image-2.1-flash",          "timeout": 30},
    {"name": "wanxiang",    "provider": "TongyiWanxiangProvider", "service": "wanxiang", "model": "wanx2.1-t2i-plus",             "timeout": 60},
]

VIDEO_PROVIDERS = [
    {"name": "kling",       "provider": "KlingProvider",       "service": "kling",    "model": "kling-v2-6",                    "timeout": 60},
    {"name": "seedance",    "provider": "seedance",            "service": "ark",      "model": "doubao-seedance-1-5-pro-251215", "timeout": 30},
    {"name": "happyhorse",  "provider": "happyhorse",          "service": "happyhorse","model": "happyhorse",                     "timeout": 20},
]

FACE_PROVIDERS = [
    {"name": "seedream",    "provider": "ARKImageProvider",    "service": "ark",      "model": "doubao-seedream-4-0-250828",     "timeout": 60},
    {"name": "hidream",     "provider": "HiDreamImageProvider","service": "hidream",  "model": "z1-image",                       "timeout": 60},
    {"name": "agnes",       "provider": "AgnesAIProvider",     "service": "agnes",    "model": "agnes-image-2.1-flash",          "timeout": 30},
    {"name": "wanxiang",    "provider": "TongyiWanxiangProvider", "service": "wanxiang", "model": "wanx2.1-t2i-plus",             "timeout": 60},
]

TTS_PROVIDERS = [
    {"name": "edge-tts",   "provider": "EdgeTTSProvider",   "service": "edge",     "model": "edge-tts",                   "timeout": 15},
    {"name": "cosyvoice",   "provider": "cosyvoice",           "service": "qwen",     "model": "cosyvoice-v2",                   "timeout": 20},
    {"name": "silent",      "provider": "silent",              "service": "silent",    "model": "silent",                         "timeout": 1, "static": True},
]

BGM_PROVIDERS = [
    {"name": "music-api",   "provider": "music_api",           "service": "music",    "model": "bgm-generator",                  "timeout": 30},
    # P0-2: local fallback removed — BGM must come from real music API or fail explicitly
]

PROVIDER_MAP = {
    "image": IMAGE_PROVIDERS,
    "video": VIDEO_PROVIDERS,
    "face": FACE_PROVIDERS,
    "tts": TTS_PROVIDERS,
    "bgm": BGM_PROVIDERS,
}


def get_route(service_type: str, preferred: str = None) -> list:
    """获取服务类型的路由链，支持指定首选"""
    chain = list(PROVIDER_MAP.get(service_type, []))
    if preferred and chain:
        idx = next((i for i, p in enumerate(chain) if p["name"] == preferred or p["service"] == preferred), -1)
        if idx > 0:
            chain.insert(0, chain.pop(idx))
    return chain


def call_with_retry(provider_fn: Callable[[int], Any], timeout: int = 30, retries: int = 0, cache_key: str = "") -> Any:
    """
    调用 provider 函数，默认尝试1次（retries=0=不重试）。
    如果启用缓存，失败会标记缓存，短时间不再调同一个模型。
    如果成功，结果写入缓存。
    """
    # 查失败标记缓存（5分钟内不再重复尝试）
    if cache_key and cache_get(cache_key, "fail_flag", extra="", ttl=300):
        raise Exception(f"[CacheSkip] 模型标记失败（5分钟冷却中）: {cache_key}")

    import concurrent.futures
    force_timeout = max(timeout, 60)  # at least 60s
    last_error = None
    for attempt in range(retries + 1):
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(provider_fn, timeout=timeout)
                result = future.result(timeout=force_timeout)
            if result:
                if cache_key:
                    cache_set(cache_key, "success_flag", extra="", data={"ok": True})
                return result
        except concurrent.futures.TimeoutError:
            last_error = TimeoutError(f"provider timeout after {force_timeout}s")
            err_str = str(last_error)
            logger.warning(f"[RouteManager] 超时 force_timeout={force_timeout}s attempt={attempt}")
        except Exception as e:
            last_error = e
            err_str = str(e)
        if attempt < retries:
            wait = min(1.0 * (2 ** attempt), 8)
            if "429" in err_str or "RateLimit" in err_str:
                wait = min(5.0 * (2 ** attempt), 30)
            time.sleep(wait)

    # 全部失败 -> 写失败标记缓存
    if cache_key:
        cache_set(cache_key, "fail_flag", extra="", data={"ok": False, "error": str(last_error or "unknown")})
    raise last_error or Exception("调用失败")


def run_with_fallback(service_type: str, provider_fn_factory: Callable,
                      preferred: str = None, model_routes: dict = None,
                      **kwargs) -> dict:
    """统一路由：按优先级链依次尝试，全部失败返回最后一条错误
    新策略：如果模型返回排队，并发调其他模型，先到先用"""
    import concurrent.futures

    chain = list(get_route(service_type, preferred))

    if model_routes and service_type in model_routes:
        route_cfg = model_routes[service_type]
        preferred = route_cfg.get("primary")
        if preferred:
            idx = next((i for i, p in enumerate(chain) if p["name"] == preferred), -1)
            if idx > 0:
                chain.insert(0, chain.pop(idx))

    errors = []
    # 缓存 key 用于去重排队
    cache_key = kwargs.get("prompt", "") or kwargs.get("scene_prompt", "")

    def _try_one(provider_info):
        """尝试单个provider，返回结果或None"""
        name = provider_info["name"]
        timeout = provider_info.get("timeout", 30)
        static = provider_info.get("static", False)
        try:
            logger.info(f"[{service_type}] 尝试: {name} (timeout={timeout}s)")
            if static:
                result = provider_fn_factory(provider_info, timeout=timeout, **kwargs)
            else:
                info = dict(provider_info)
                result = call_with_retry(
                    lambda timeout=timeout, **kw: provider_fn_factory(info, timeout=timeout, **kwargs, **kw),
                    timeout=timeout, retries=0, cache_key=f"{cache_key}|{name}"
                )
            if result:
                return {"success": True, "data": result, "provider": name,
                        "model": provider_info.get("model", ""), "error": ""}
            return None
        except Exception as e:
            error_msg = str(e)[:100]
            logger.warning(f"[{service_type}] {name} 失败: {error_msg}")
            return {"success": False, "provider": name, "error": error_msg}

    # 第一个provider必调（优先）
    if chain:
        r1 = _try_one(chain[0])
        if r1 and r1.get("success"):
            return r1
        if r1: errors.append(f"{r1['provider']}: {r1.get('error','fail')}")
        # 第一个返回排队：并发调剩下的
        remaining = chain[1:]
        if remaining and any("排队" in e.get("error","") or "busy" in e.get("error","").lower() or "rate" in e.get("error","").lower() for e in [r1] if r1):
            logger.info(f"[{service_type}] 模型排队中，并发尝试其他模型: {[p['name'] for p in remaining]}")
            with concurrent.futures.ThreadPoolExecutor(max_workers=len(remaining)) as ex:
                futs = {ex.submit(_try_one, p): p for p in remaining}
                for fut in concurrent.futures.as_completed(futs, timeout=60):
                    r = fut.result()
                    if r and r.get("success"):
                        return r
                    if r: errors.append(f"{r['provider']}: {r.get('error','fail')}")
        else:
            # 第一个不排队但失败了，顺序试剩下的
            for p in remaining:
                r = _try_one(p)
                if r and r.get("success"):
                    return r
                if r: errors.append(f"{r['provider']}: {r.get('error','fail')}")

    err_msg = "; ".join(errors)
    logger.error(f"[{service_type}] 所有provider均失败: {err_msg}")
    # 不跳过，返回明确失败状态让 pipeline 暂停等待
    return {"success": False, "data": None, "provider": "", "model": "",
            "error": err_msg, "all_failed": True}