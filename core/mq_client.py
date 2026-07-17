"""
Redis MQ — Agent 通信总线 (Hermes 风格命名)
带自动重连和降级机制，Redis 挂了不崩溃
"""
import json
import logging
import time
import threading
from typing import Optional

try:
    import redis
except ImportError:
    redis = None

logger = logging.getLogger(__name__)

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
REDIS_DB = 0

AGENT_QUEUES = {
    "director":   "queue_director",
    "script":     "queue_script",
    "character":  "queue_character",
    "storyboard": "queue_storyboard",
    "scene":      "queue_scene",
    "audio":      "queue_audio",
    "video":      "queue_video",
    "composite":  "queue_composite",
}

COMPLETED_TOPIC = {
    "director":   "event:director.done",
    "script":     "event:script.done",
    "character":  "event:character.done",
    "storyboard": "event:storyboard.done",
    "scene":      "event:scene.done",
    "audio":      "event:audio.done",
    "video":      "event:video.done",
    "composite":  "event:composite.done",
}

# 内存降级队列（Redis 挂了时用）
_in_memory_queues = {}
_in_memory_lock = threading.Lock()


class MQ:
    def __init__(self, host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB):
        self.params = dict(host=host, port=port, db=db, decode_responses=True)
        self._conn = None
        self._connected = False
        self._reconnect_timer = None
        self._connect()

    def _connect(self):
        """尝试连接 Redis，失败不抛异常"""
        try:
            if redis is None:
                logger.warning("[MQ] redis 模块未安装，使用内存降级")
                self._connected = False
                return
            if self._conn is None:
                self._conn = redis.Redis(**self.params, socket_connect_timeout=5, socket_timeout=5)
            self._conn.ping()
            self._connected = True
        except Exception as e:
            if not self._connected:
                logger.warning(f"[MQ] Redis 连接失败: {e}，使用内存降级")
            self._connected = False
            # 5 秒后自动重连
            self._schedule_reconnect()

    def _schedule_reconnect(self):
        """定时重连 Redis"""
        if self._reconnect_timer:
            self._reconnect_timer.cancel()
        self._reconnect_timer = threading.Timer(5, self._connect)
        self._reconnect_timer.daemon = True
        self._reconnect_timer.start()

    def _get_conn(self):
        """获取连接，失败则触发重连"""
        if not self._connected:
            self._connect()
        if not self._connected:
            raise RedisUnavailable()
        return self.r

    @property
    def r(self):
        if self._conn is None:
            self._conn = redis.Redis(**self.params, socket_connect_timeout=5, socket_timeout=5)
        return self._conn

    def push(self, queue: str, data: dict):
        """推送到队列，Redis 挂了存内存"""
        try:
            self._get_conn()
            self.r.rpush(queue, json.dumps(data, ensure_ascii=False))
        except (RedisUnavailable, Exception) as e:
            logger.warning(f"[MQ] push 失败，降级到内存: {e}")
            with _in_memory_lock:
                if queue not in _in_memory_queues:
                    _in_memory_queues[queue] = []
                _in_memory_queues[queue].append(json.dumps(data, ensure_ascii=False))

    def pop(self, queue: str, timeout: int = 5) -> Optional[dict]:
        """从队列弹出，Redis 挂了从内存取"""
        # 先试内存队列
        with _in_memory_lock:
            if queue in _in_memory_queues and _in_memory_queues[queue]:
                raw = _in_memory_queues[queue].pop(0)
                if not _in_memory_queues[queue]:
                    del _in_memory_queues[queue]
                try:
                    return json.loads(raw)
                except:
                    pass

        # 再试 Redis
        try:
            self._get_conn()
            rv = self.r.blpop(queue, timeout=timeout)
            if rv:
                _, raw = rv
                return json.loads(raw)
        except (RedisUnavailable, Exception) as e:
            logger.warning(f"[MQ] pop 失败，降级到内存: {e}")
        return None

    def publish(self, channel: str, data: dict):
        """发布事件，Redis 挂了静默丢弃"""
        try:
            self._get_conn()
            self.r.publish(channel, json.dumps(data, ensure_ascii=False))
        except (RedisUnavailable, Exception) as e:
            logger.warning(f"[MQ] publish 失败: {e}")

    def subscribe(self, *channels):
        self._pubsub = self.r.pubsub()
        self._pubsub.subscribe(*channels)

    def get_message(self, timeout: float = 1.0) -> Optional[dict]:
        if not hasattr(self, '_pubsub') or self._pubsub is None:
            return None
        try:
            msg = self._pubsub.get_message(timeout=timeout)
            if msg and msg['type'] == 'message':
                return json.loads(msg['data'])
        except:
            pass
        return None

    def close(self):
        if hasattr(self, '_pubsub') and self._pubsub:
            self._pubsub.close()
        if self._conn:
            self._conn.close()


class RedisUnavailable(Exception):
    """Redis 不可用"""
    pass


mq = MQ()
