"""并发池调度器 — 无限制模式，按需并发"""
import asyncio
import time
import logging
from functools import wraps
import threading
from typing import Optional, Callable

logger = logging.getLogger(__name__)


class ConcurrencyPool:
    """并发池 — 不限制任何并发，所有任务立即执行"""
    STAGE_LIMITS = {
        "image": 999, "face": 999, "video": 999, "tts": 999,
        "bgm": 999, "subtitle": 999, "composite": 999, "llm": 999,
    }
    RPM_LIMITS = {
        "happyhorse": 6,
        "kling": 3,
        "seedream": 10,
        "deepseek": 500,
        "agnes": 10,
        "edge_tts": 999,
    }

    def __init__(self, max_total: int = 999):
        self.max_total = max_total
        self._active = {}
        self._total_active = 0
        self._waiting = 0
        self._lock = threading.Lock()
        self._rpm_count = {}
        self._rpm_lock = threading.Lock()
        # 不设信号量，全放开

    async def acquire(self, stage: str = "llm") -> bool:
        with self._lock:
            self._active[stage] = self._active.get(stage, 0) + 1
            self._total_active += 1
        return True

    async def release(self, stage: str = "llm"):
        with self._lock:
            self._active[stage] = max(0, self._active.get(stage, 0) - 1)
            self._total_active = max(0, self._total_active - 1)

    def sync_acquire(self, stage: str = "llm"):
        with self._lock:
            self._active[stage] = self._active.get(stage, 0) + 1
            self._total_active += 1

    def sync_release(self, stage: str = "llm"):
        with self._lock:
            self._active[stage] = max(0, self._active.get(stage, 0) - 1)
            self._total_active = max(0, self._total_active - 1)

    def wait_if_needed(self, provider="kling"):
        import time as _time
        limit = self.RPM_LIMITS.get(provider, 20)
        if limit >= 999:
            return
        with self._rpm_lock:
            now = _time.time()
            timestamps = self._rpm_count.get(provider, [])
            timestamps = [t for t in timestamps if now - t < 60]
            if len(timestamps) >= limit:
                wait = timestamps[0] + 60 - now
                if wait > 0:
                    self._rpm_lock.release()
                    logger.warning("[RATE] %s 已到限额，等 %.1fs", provider, wait)
                    _time.sleep(wait + 0.5)
                    self._rpm_lock.acquire()
            timestamps.append(now)
            self._rpm_count[provider] = timestamps

    def run_sync(self, stage: str, func: Callable, *args, **kwargs):
        self.sync_acquire(stage)
        try:
            return func(*args, **kwargs)
        finally:
            self.sync_release(stage)


# 全局实例（模块只初始化一次）
concurrency_pool = ConcurrencyPool(max_total=999)


