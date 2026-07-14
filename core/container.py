"""
container.py — 依赖注入容器
线程安全，支持单例、工厂、类型自动解析
"""
from __future__ import annotations
from typing import Dict, Any, Type, Callable, Optional
from functools import wraps
import inspect
import asyncio
import threading


class DependencyContainer:
    """线程安全的依赖注入容器"""
    
    def __init__(self):
        self._services: Dict[str, Any] = {}
        self._factories: Dict[str, Callable] = {}
        self._singletons: Dict[str, Any] = {}
        self._lock = threading.RLock()
    
    def register(self, name: str, instance: Any):
        """注册一个服务实例"""
        with self._lock:
            self._services[name] = instance
    
    def register_factory(self, name: str, factory: Callable, singleton: bool = True):
        """注册工厂方法"""
        with self._lock:
            self._factories[name] = factory
            if singleton:
                self._singletons[name] = None
    
    def register_type(self, type_: Type, name: Optional[str] = None):
        """注册类型用于自动解析"""
        name = name or type_.__name__
        self._factories[name] = lambda: self._resolve_type(type_)
        self._singletons[name] = None
    
    def get(self, name: str) -> Any:
        """获取服务（线程安全）"""
        with self._lock:
            if name in self._services:
                return self._services[name]
            
            if name in self._factories:
                if name in self._singletons and self._singletons[name] is not None:
                    return self._singletons[name]
                
                instance = self._factories[name]()
                if name in self._singletons:
                    self._singletons[name] = instance
                return instance
            
            raise KeyError(f"Service '{name}' not registered")
    
    def get_or_none(self, name: str) -> Optional[Any]:
        """安全获取服务"""
        try:
            return self.get(name)
        except KeyError:
            return None
    
    def _resolve_type(self, type_: Type) -> Any:
        """通过构造函数参数自动解析类型"""
        sig = inspect.signature(type_.__init__)
        params = {}
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            if param.annotation != inspect.Parameter.empty:
                try:
                    params[param_name] = self.get(param.annotation.__name__)
                except KeyError:
                    if param.default != inspect.Parameter.empty:
                        params[param_name] = param.default
                    else:
                        raise ValueError(f"Cannot resolve parameter '{param_name}' for {type_.__name__}")
            elif param.default != inspect.Parameter.empty:
                params[param_name] = param.default
        
        return type_(**params)
    
    def clear(self):
        """清空所有注册"""
        with self._lock:
            self._services.clear()
            self._factories.clear()
            self._singletons.clear()


container = DependencyContainer()


def inject(func):
    """自动依赖注入装饰器"""
    @wraps(func)
    async def async_wrapper(*args, **kwargs):
        sig = inspect.signature(func)
        bound = sig.bind_partial(*args, **kwargs)
        
        for param_name, param in sig.parameters.items():
            if param_name not in bound.arguments:
                if param.annotation != inspect.Parameter.empty:
                    try:
                        bound.arguments[param_name] = container.get(param.annotation.__name__)
                    except KeyError:
                        if param.default != inspect.Parameter.empty:
                            bound.arguments[param_name] = param.default
                        else:
                            raise ValueError(f"Cannot inject '{param_name}' for {func.__name__}")
                elif param.default != inspect.Parameter.empty:
                    bound.arguments[param_name] = param.default
        
        return await func(*bound.args, **bound.kwargs)
    
    @wraps(func)
    def sync_wrapper(*args, **kwargs):
        sig = inspect.signature(func)
        bound = sig.bind_partial(*args, **kwargs)
        
        for param_name, param in sig.parameters.items():
            if param_name not in bound.arguments:
                if param.annotation != inspect.Parameter.empty:
                    try:
                        bound.arguments[param_name] = container.get(param.annotation.__name__)
                    except KeyError:
                        if param.default != inspect.Parameter.empty:
                            bound.arguments[param_name] = param.default
                        else:
                            raise ValueError(f"Cannot inject '{param_name}' for {func.__name__}")
                elif param.default != inspect.Parameter.empty:
                    bound.arguments[param_name] = param.default
        
        return func(*bound.args, **bound.kwargs)
    
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    return sync_wrapper