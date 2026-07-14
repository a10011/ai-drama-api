"""
tracing.py — 分布式追踪系统
支持 span 嵌套、上下文传播、性能监控
"""
from __future__ import annotations
import uuid
import time
import logging
import asyncio
import threading
from contextvars import ContextVar
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from functools import wraps

logger = logging.getLogger(__name__)

_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
_span_id_var: ContextVar[str] = ContextVar("span_id", default="")
_parent_span_id_var: ContextVar[str] = ContextVar("parent_span_id", default="")


@dataclass
class Span:
    """追踪跨度"""
    span_id: str
    parent_span_id: str
    trace_id: str
    operation: str
    start_time: float
    end_time: Optional[float] = None
    status: str = "pending"
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    
    def finish(self, status: str = "success"):
        self.end_time = time.time()
        self.status = status
    
    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        self.events.append({
            "name": name,
            "timestamp": time.time(),
            "attributes": attributes or {}
        })
    
    @property
    def duration_ms(self) -> float:
        if self.end_time and self.start_time:
            return (self.end_time - self.start_time) * 1000
        return 0.0


class Tracer:
    """分布式追踪器 — 线程安全"""
    
    def __init__(self):
        self._spans: Dict[str, Span] = {}
        self._span_stack: Dict[str, List[str]] = {}
        self._lock = threading.RLock()
    
    def start_span(
        self,
        operation: str,
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        attributes: Optional[Dict[str, Any]] = None
    ) -> Span:
        span_id = str(uuid.uuid4())
        trace_id = trace_id or _trace_id_var.get() or str(uuid.uuid4())
        parent_span_id = parent_span_id or _span_id_var.get() or ""
        
        span = Span(
            span_id=span_id,
            parent_span_id=parent_span_id,
            trace_id=trace_id,
            operation=operation,
            start_time=time.time(),
            attributes=attributes or {}
        )
        
        _trace_id_var.set(trace_id)
        _parent_span_id_var.set(_span_id_var.get())
        _span_id_var.set(span_id)
        
        with self._lock:
            self._spans[span_id] = span
            if trace_id not in self._span_stack:
                self._span_stack[trace_id] = []
            self._span_stack[trace_id].append(span_id)
        
        logger.debug(f"Started span: {operation} ({span_id}) in trace {trace_id}")
        return span
    
    def end_span(self, span_id: str, status: str = "success"):
        with self._lock:
            span = self._spans.get(span_id)
            if not span:
                return
            
            span.finish(status)
            
            _span_id_var.set(span.parent_span_id)
            
            trace_id = span.trace_id
            if trace_id in self._span_stack:
                stack = self._span_stack[trace_id]
                if span_id in stack:
                    stack.remove(span_id)
        
        logger.debug(f"Ended span: {span.operation} ({span_id}) - {status} ({span.duration_ms:.2f}ms)")
    
    def get_current_span(self) -> Optional[Span]:
        span_id = _span_id_var.get()
        with self._lock:
            return self._spans.get(span_id)
    
    def get_trace_id(self) -> str:
        return _trace_id_var.get()
    
    def get_span_tree(self, trace_id: str) -> Dict[str, Any]:
        with self._lock:
            spans = [s for s in self._spans.values() if s.trace_id == trace_id]
            if not spans:
                return {}
            
            root = None
            children: Dict[str, List[Span]] = {}
            for span in spans:
                if not span.parent_span_id:
                    root = span
                else:
                    if span.parent_span_id not in children:
                        children[span.parent_span_id] = []
                    children[span.parent_span_id].append(span)
            
            def build_node(span: Span) -> Dict[str, Any]:
                node = {
                    "span_id": span.span_id,
                    "operation": span.operation,
                    "duration_ms": span.duration_ms,
                    "status": span.status,
                    "attributes": span.attributes,
                    "events": span.events,
                    "children": []
                }
                for child in children.get(span.span_id, []):
                    node["children"].append(build_node(child))
                return node
            
            if root:
                return build_node(root)
            return {}
    
    def clear(self):
        with self._lock:
            self._spans.clear()
            self._span_stack.clear()


tracer = Tracer()


def trace(operation: Optional[str] = None):
    """追踪装饰器"""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            span_name = operation or func.__name__
            span = tracer.start_span(span_name)
            try:
                result = await func(*args, **kwargs)
                tracer.end_span(span.span_id, "success")
                return result
            except Exception as e:
                span.add_event("error", {"error": str(e)})
                tracer.end_span(span.span_id, "error")
                raise
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            span_name = operation or func.__name__
            span = tracer.start_span(span_name)
            try:
                result = func(*args, **kwargs)
                tracer.end_span(span.span_id, "success")
                return result
            except Exception as e:
                span.add_event("error", {"error": str(e)})
                tracer.end_span(span.span_id, "error")
                raise
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator