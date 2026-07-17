"""
Redis MQ — Agent 通信总线 (Hermes 风格命名)
带自动重连和持久化降级机制，Redis 挂了不丢任务
"""
import json
import logging
import time
import threading
import os
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

# 持久化降级目录
_MQ_DIR = "/www/wwwroot/storage/mq_queue"
os.makedirs(_MQ_DIR, exist_ok=True)

# 内存降级队列（临时用）
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
                logger.warning("[MQ] redis 模块未安装，使用持久化降级")
                self._connected = False
                return
            if self._conn is None:
                self._conn = redis.Redis(**self.params, socket_connect_timeout=5, socket_timeout=5)
            self._conn.ping()
            self._connected = True
            logger.info("[MQ] Redis 连接成功")
        except Exception as e:
            if not self._connected:
                logger.warning(f"[MQ] Redis 连接失败: {e}，使用持久化降级")
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

    def _persist_push(self, queue: str, data: dict):
        """持久化推送到文件"""
        try:
            qdir = os.path.join(_MQ_DIR, queue)
            os.makedirs(qdir, exist_ok=True)
            fpath = os.path.join(qdir, f"{int(time.time()*1000)}_{threading.current_thread().ident}.json")
            with open(fpath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"[MQ] 持久化写入失败: {e}")
            return False

    def _persist_pop(self, queue: str) -> Optional[dict]:
        """从文件队列弹出（按文件名排序，保证 FIFO）"""
        try:
            qdir = os.path.join(_MQ_DIR, queue)
            if not os.path.exists(qdir):
                return None
            files = sorted(os.listdir(qdir))
            if not files:
                return None
            fpath = os.path.join(qdir, files[0])
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                os.remove(fpath)
                return data
            except Exception:
                os.remove(fpath)
                return None
        except Exception as e:
            logger.error(f"[MQ] 持久化读取失败: {e}")
            return None

    def _purge_old_files(self, queue: str, max_age_hours=24):
        """清理超过指定时间的降级文件，防止磁盘占满"""
        try:
            qdir = os.path.join(_MQ_DIR, queue)
            if not os.path.exists(qdir):
                return
            now = time.time()
            for fname in os.listdir(qdir):
                fpath = os.path.join(qdir, fname)
                if now - os.path.getmtime(fpath) > max_age_hours * 3600:
                    os.remove(fpath)
                    logger.info(f"[MQ] 清理过期降级文件: {fname}")
        except Exception as e:
            logger.warning(f"[MQ] 清理过期文件失败: {e}")

    def push(self, queue: str, data: dict):
        """推送到队列，Redis 挂了持久化到文件"""
        try:
            self._get_conn()
            self.r.rpush(queue, json.dumps(data, ensure_ascii=False))
        except (RedisUnavailable, Exception) as e:
            logger.warning(f"[MQ] push 失败，持久化到文件: {e}")
            self._persist_push(queue, data)

    def pop(self, queue: str, timeout: int = 5) -> Optional[dict]:
        """从队列弹出，优先 Redis，其次持久化文件，最后内存"""
        # 1. 先试 Redis
        try:
            self._get_conn()
            rv = self.r.blpop(queue, timeout=timeout)
            if rv:
                _, raw = rv
                return json.loads(raw)
        except (RedisUnavailable, Exception):
            pass

        # 2. 再试持久化文件队列
        file_task = self._persist_pop(queue)
        if file_task:
            return file_task

        # 3. 最后试内存队列（临时降级）
        with _in_memory_lock:
            if queue in _in_memory_queues and _in_memory_queues[queue]:
                raw = _in_memory_queues[queue].pop(0)
                if not _in_memory_queues[queue]:
                    del _in_memory_queues[queue]
                try:
                    return json.loads(raw)
                except:
                    pass

        # 4. 定期清理旧文件
        self._purge_old_files(queue)
        
        return None

    def publish(self, channel: str, data: dict):
        """发布事件，Redis 挂了持久化到文件"""
        try:
            self._get_conn()
            self.r.publish(channel, json.dumps(data, ensure_ascii=False))
        except (RedisUnavailable, Exception) as e:
            logger.warning(f"[MQ] publish 失败，持久化到文件: {e}")
            self._persist_push(f"topic_{channel}", data)

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
