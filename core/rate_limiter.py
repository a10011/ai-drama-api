#!/usr/bin/env python3
"""
两层速率限制 + 背压控制：
1. 用户Key→模型：每个 (user_id, model) 最多 3 并发，第 4 个排队
2. 模型级 RPM：令牌桶控制
3. 背压：模型返回前不塞新请求
"""
import threading
import time
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

MAX_CONCURRENT_PER_KEY_MODEL = 3  # 每个 Key 对每个模型最多 3 并发


class BackpressureManager:
    """背压管理器：跟踪每个 (user_id, model) 的活跃请求"""
    
    def __init__(self):
        self._active = defaultdict(int)  # key -> 当前活跃请求数
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
    
    def wait_for_slot(self, key: str, timeout: float = 600):
        """等待活跃请求降为 0 后再继续"""
        with self._condition:
            deadline = time.time() + timeout
            while self._active[key] >= MAX_CONCURRENT_PER_KEY_MODEL:
                remaining = deadline - time.time()
                if remaining <= 0:
                    logger.warning(f"[Backpressure] {key} 等待超时")
                    return False
                self._condition.wait(timeout=min(remaining, 1.0))
            self._active[key] += 1
            return True
    
    def release(self, key: str):
        """请求完成，释放槽位"""
        with self._condition:
            if self._active[key] > 0:
                self._active[key] -= 1
            self._condition.notify_all()


class PerModelConcurrencyLimiter:
    """
    每个 (user_id, model) 最多 MAX_CONCURRENT_PER_KEY_MODEL 个并发。
    第 N+1 个请求阻塞直到有槽释放。
    """
    
    def __init__(self, max_per=MAX_CONCURRENT_PER_KEY_MODEL):
        self.max_per = max_per
        self._semaphores = {}  # key -> Semaphore
        self._lock = threading.Lock()
    
    def _get_sem(self, key: str) -> threading.BoundedSemaphore:
        with self._lock:
            if key not in self._semaphores:
                self._semaphores[key] = threading.BoundedSemaphore(self.max_per)
            return self._semaphores[key]
    
    def acquire(self, user_id: int, model: str, timeout: float = 600) -> bool:
        """获取一个并发槽，最多等 timeout 秒"""
        key = f"{user_id}:{model}"
        sem = self._get_sem(key)
        acquired = sem.acquire(timeout=timeout)
        if acquired:
            logger.debug(f"[RateLimit] {key} 获取槽（剩余 {sem._value}）")
        else:
            logger.warning(f"[RateLimit] {key} 等待超时（{timeout}s）")
        return acquired
    
    def release(self, user_id: int, model: str):
        key = f"{user_id}:{model}"
        sem = self._get_sem(key)
        sem.release()
        logger.debug(f"[RateLimit] {key} 释放槽")


class TokenBucket:
    """令牌桶：每秒填充 rate/60 个令牌"""
    
    def __init__(self, rate: float):
        self.rate = rate  # RPM
        self.max_tokens = max(int(rate), 1)
        self.tokens = self.max_tokens
        self.last_refill = time.time()
        self._lock = threading.Lock()
    
    def consume(self, tokens: int = 1) -> float:
        """消费 tokens，返回需等待秒数（0=立刻通过）"""
        with self._lock:
            now = time.time()
            elapsed = now - self.last_refill
            self.tokens = min(self.max_tokens, self.tokens + elapsed * (self.rate / 60.0))
            self.last_refill = now
            
            if self.tokens >= tokens:
                self.tokens -= tokens
                return 0.0
            else:
                wait = (tokens - self.tokens) * (60.0 / self.rate)
                return wait


class ModelRpmLimiter:
    """模型级 RPM 限流"""
    
    DEFAULT_RPM = {
        "doubao-pro-256k": 100,
        "doubao-seedream-5-0": 5,
        "doubao-seedance-1-5-pro": 1,
        "cosyvoice-v2": 10,
        "doubao-music-v1": 5,
    }
    
    def __init__(self):
        self._buckets = {}
        self._lock = threading.Lock()
    
    def _get_bucket(self, key: str, rpm: float) -> TokenBucket:
        with self._lock:
            if key not in self._buckets:
                self._buckets[key] = TokenBucket(rpm)
            return self._buckets[key]
    
    def wait_and_consume(self, user_id: int, model: str, rpm: float = None):
        if rpm is None:
            rpm = self.DEFAULT_RPM.get(model, 10)
        key = f"rpm:{user_id}:{model}"
        bucket = self._get_bucket(key, rpm)
        wait = bucket.consume()
        if wait > 0:
            logger.info(f"[RPM] {model} 等待 {wait:.1f}s")
            time.sleep(wait)


class RateLimiter:
    """统一速率限制器 + 背压"""
    
    def __init__(self):
        self.model_concurrency = PerModelConcurrencyLimiter()
        self.model_rpm = ModelRpmLimiter()
        self.backpressure = BackpressureManager()
    
    def execute(self, user_id: int, model: str, rpm: float, func, *args, **kwargs):
        """
        带限流的执行：
        1. 背压：等模型返回前不塞新请求
        2. 拿 (user_id, model) 并发槽（最多等 10 分钟）
        3. RPM 令牌桶
        4. 执行函数
        """
        key = f"{user_id}:{model}"
        
        # 1. 背压控制：等当前活跃请求降到上限以下
        if not self.backpressure.wait_for_slot(key, timeout=600):
            raise TimeoutError(f"({user_id},{model}) 背压等待超时")
        
        try:
            # 2. 并发限制
            if not self.model_concurrency.acquire(user_id, model, timeout=600):
                raise TimeoutError(f"({user_id},{model}) 并发槽等待超时")
            
            try:
                # 3. RPM 限流
                self.model_rpm.wait_and_consume(user_id, model, rpm)
                
                # 4. 执行
                return func(*args, **kwargs)
            finally:
                self.model_concurrency.release(user_id, model)
        finally:
            # 5. 释放背压槽位，让下一个请求进来
            self.backpressure.release(key)


# 全局单例
rate_limiter = RateLimiter()
