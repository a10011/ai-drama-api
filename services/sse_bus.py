"""
SSE 事件总线 — 跨模块实时推送
pipeline.py 注册队列，agent_scene.py 等模块通过 emit 推事件
"""
import queue as _queue
import threading
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_sse_queues: Dict[str, List[_queue.Queue]] = {}
_sse_lock = threading.Lock()


def subscribe(project_id: str) -> _queue.Queue:
    """注册 SSE 订阅，返回队列"""
    q = _queue.Queue(maxsize=200)
    with _sse_lock:
        _sse_queues.setdefault(project_id, []).append(q)
    return q


def unsubscribe(project_id: str, q: _queue.Queue):
    """取消订阅"""
    with _sse_lock:
        queues = _sse_queues.get(project_id, [])
        if q in queues:
            queues.remove(q)
        if not queues:
            _sse_queues.pop(project_id, None)


def emit(project_id: str, stage: str, status: str, data: Any = None):
    """向某项目的所有 SSE 订阅者推送事件"""
    event = {"stage": stage, "status": status, "data": data}
    with _sse_lock:
        queues = list(_sse_queues.get(project_id, []))
    for q in queues:
        try:
            q.put_nowait(event)
        except _queue.Full:
            pass
