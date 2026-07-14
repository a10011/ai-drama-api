"""
Redis MQ — Agent 通信总线 (Hermes 风格命名)
"""
import json
import logging
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
    "director":  "queue_director",
    "script":    "queue_script",
    "character": "queue_character",
    "storyboard": "queue_storyboard",
    "scene":     "queue_scene",
    "audio":     "queue_audio",
    "video":     "queue_video",
    "composite": "queue_composite",
}

COMPLETED_TOPIC = {
    "director":  "event:director.done",
    "script":    "event:script.done",
    "character": "event:character.done",
    "storyboard": "event:storyboard.done",
    "scene":     "event:scene.done",
    "audio":     "event:audio.done",
    "video":     "event:video.done",
    "composite": "event:composite.done",
}


class MQ:
    def __init__(self, host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB):
        self.params = dict(host=host, port=port, db=db, decode_responses=True)
        self._conn = None

    @property
    def r(self):
        if self._conn is None:
            self._conn = redis.Redis(**self.params)
        return self._conn

    def push(self, queue: str, data: dict):
        self.r.rpush(queue, json.dumps(data, ensure_ascii=False))

    def pop(self, queue: str, timeout: int = 5) -> Optional[dict]:
        rv = self.r.blpop(queue, timeout=timeout)
        if rv:
            _, raw = rv
            return json.loads(raw)
        return None

    def publish(self, channel: str, data: dict):
        self.r.publish(channel, json.dumps(data, ensure_ascii=False))

    def subscribe(self, *channels):
        self._pubsub = self.r.pubsub()
        self._pubsub.subscribe(*channels)

    def get_message(self, timeout: float = 1.0) -> Optional[dict]:
        if not hasattr(self, '_pubsub') or self._pubsub is None:
            return None
        msg = self._pubsub.get_message(timeout=timeout)
        if msg and msg['type'] == 'message':
            return json.loads(msg['data'])
        return None

    def close(self):
        if hasattr(self, '_pubsub') and self._pubsub:
            self._pubsub.close()
        if self._conn:
            self._conn.close()


mq = MQ()
