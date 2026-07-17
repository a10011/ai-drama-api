"""
Redis MQ — Agent 通信总线 (Hermes 风格命名)
健康检查 + 智能降级，Redis 不存在也不影响服务
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


class MQ:
    def __init__(self, host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB):
        self.params = dict(host=host, port=port, db=db, decode_responses=True)
        self._conn = None
        self._connected = False
        self._connecting = False  # 防止并发重复连接
        self._health_check_interval = 30  # 30秒检查一次，不用频繁 ping
        self._health_timer = None
        # 启动时检查一次，不阻塞
        self._check_health(silent=True)
        # 定时健康检查
        self._schedule_health_check()

    def _check_health(self, silent=False):
        """健康检查，非阻塞"""
        if self._connecting:
            return
        self._connecting = True
        try:
            if redis is None:
                if not silent:
                    logger.warning("[MQ] redis 模块未安装，使用持久化降级")
                self._connected = False
                return
            # 如果已经连接且 ping 成功，不需要重连
            if self._connected and self._conn:
                try:
                    self._conn.ping()
                    return  # 仍然健康，什么都不做
                except:
                    pass  # 断了，下面重建
            # 尝试连接/重连
            self._conn = redis.Redis(**self.params, socket_connect_timeout=3, socket_timeout=3, retry_on_timeout=True)
            self._conn.ping()
            self._connected = True
            if not silent:
                logger.info("[MQ] Redis 连接成功")
        except Exception as e:
            self._connected = False
            self._conn = None
            if not silent:
                logger.warning(f"[MQ] Redis 不可用: {e}，使用持久化降级")
        finally:
            self._connecting = False

    def _schedule_health_check(self):
        """定时健康检查"""
        if self._health_timer:
            self._health_timer.cancel()
        self._health_timer = threading.Timer(self._health_check_interval, self._health_check)
        self._health_timer.daemon = True
        self._health_timer.start()

    def _ensure_connected(self):
        """确保连接可用，失败返回 False"""
        if not self._connected:
            self._check_health()
        return self._connected

    def push(self, queue: str, data: dict):
        """推送到队列，Redis 不可用时持久化到文件"""
        if self._ensure_connected():
            try:
                self._conn.rpush(queue, json.dumps(data, ensure_ascii=False))
                return
            except Exception as e:
                logger.warning(f"[MQ] push 失败: {e}，降级到文件")
        self._persist_push(queue, data)

    def pop(self, queue: str, timeout: int = 5) -> Optional[dict]:
        """从队列弹出"""
        # 1. 先试 Redis（非阻塞快速失败）
        if self._ensure_connected():
            try:
                rv = self._conn.blpop(queue, timeout=min(timeout, 2))
                if rv:
                    _, raw = rv
                    return json.loads(raw)
            except Exception:
                self._connected = False
                self._conn = None

        # 2. 试持久化文件队列
        file_task = self._persist_pop(queue)
        if file_task:
            return file_task

        # 3. 定期清理旧文件（每 10 次 pop 清一次）
        if hasattr(self, '_pop_count'):
            self._pop_count += 1
        else:
            self._pop_count = 1
        if self._pop_count % 10 == 0:
            self._purge_old_files()

        return None

    def publish(self, channel: str, data: dict):
        """发布事件，Redis 不可用时持久化到文件"""
        if self._ensure_connected():
            try:
                self._conn.publish(channel, json.dumps(data, ensure_ascii=False))
                return
            except Exception as e:
                logger.warning(f"[MQ] publish 失败: {e}，降级到文件")
        self._persist_push(f"topic_{channel}", data)

    def subscribe(self, *channels):
        """订阅频道（仅 Redis 可用时）"""
        if not self._ensure_connected():
            logger.warning("[MQ] Redis 不可用，无法订阅")
            return None
        try:
            pubsub = self._conn.pubsub()
            pubsub.subscribe(*channels)
            return pubsub
        except Exception as e:
            logger.warning(f"[MQ] subscribe 失败: {e}")
            return None

    def get_message(self, pubsub, timeout: float = 1.0) -> Optional[dict]:
        """从 pubsub 获取消息"""
        if pubsub is None:
            return None
        try:
            msg = pubsub.get_message(timeout=timeout)
            if msg and msg.get('type') == 'message':
                return json.loads(msg['data'])
        except Exception as e:
            logger.warning(f"[MQ] get_message 失败: {e}")
        return None

    def close(self):
        if self._conn:
            self._conn.close()
        if self._health_timer:
            self._health_timer.cancel()

    # ── 持久化降级方法 ──

    def _persist_push(self, queue: str, data: dict):
        """持久化推送到文件"""
        try:
            qdir = os.path.join(_MQ_DIR, queue)
            os.makedirs(qdir, exist_ok=True)
            fpath = os.path.join(qdir, f"{int(time.time()*1000)}.json")
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
                if os.path.exists(fpath):
                    os.remove(fpath)
                return None
        except Exception as e:
            logger.error(f"[MQ] 持久化读取失败: {e}")
            return None

    def _purge_old_files(self, max_age_hours=24):
        """清理超过指定时间的降级文件"""
        try:
            if not os.path.exists(_MQ_DIR):
                return
            now = time.time()
            for qname in os.listdir(_MQ_DIR):
                qdir = os.path.join(_MQ_DIR, qname)
                if not os.path.isdir(qdir):
                    continue
                for fname in os.listdir(qdir):
                    fpath = os.path.join(qdir, fname)
                    if now - os.path.getmtime(fpath) > max_age_hours * 3600:
                        os.remove(fpath)
        except Exception as e:
            logger.warning(f"[MQ] 清理过期文件失败: {e}")


mq = MQ()
