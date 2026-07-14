"""
errors.py — 统一错误处理
分层错误体系：FrameworkError → ServiceError / AgentError / PipelineError / ConfigError
"""
from __future__ import annotations
from typing import Optional, Dict, Any
from uuid import uuid4


class FrameworkError(Exception):
    """框架基础错误"""
    
    def __init__(
        self,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        cause: Optional[Exception] = None
    ):
        self.code = code
        self.message = message
        self.details = details or {}
        self.trace_id = trace_id or str(uuid4())
        self.cause = cause
        super().__init__(self.message)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "trace_id": self.trace_id
        }
    
    def __str__(self) -> str:
        return f"[{self.code}] {self.message} (trace: {self.trace_id})"


class ServiceError(FrameworkError):
    """服务层错误"""
    
    def __init__(
        self,
        service: str,
        code: str,
        message: str,
        retryable: bool = False,
        details: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        cause: Optional[Exception] = None
    ):
        self.service = service
        self.retryable = retryable
        super().__init__(code, message, details, trace_id, cause)
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result["service"] = self.service
        result["retryable"] = self.retryable
        return result


class AgentError(FrameworkError):
    """智能体层错误"""
    
    def __init__(
        self,
        agent_id: str,
        node_id: str,
        code: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        cause: Optional[Exception] = None
    ):
        self.agent_id = agent_id
        self.node_id = node_id
        super().__init__(code, message, details, trace_id, cause)
    
    def to_dict(self) -> Dict[str, Any]:
        result = super().to_dict()
        result["agent_id"] = self.agent_id
        result["node_id"] = self.node_id
        return result


class ValidationError(ServiceError):
    """输入校验错误"""
    
    def __init__(
        self,
        field: str,
        expected: str,
        actual: Any,
        message: Optional[str] = None,
        trace_id: Optional[str] = None
    ):
        self.field = field
        self.expected = expected
        self.actual = actual
        msg = message or f"Validation failed for {field}: expected {expected}, got {type(actual).__name__}"
        super().__init__(
            service="validation",
            code="VALIDATION_ERROR",
            message=msg,
            details={"field": field, "expected": expected, "actual": str(actual)},
            trace_id=trace_id
        )


class ProviderError(ServiceError):
    """外部 Provider 错误"""
    
    def __init__(
        self,
        provider: str,
        status_code: int,
        message: str,
        retryable: bool = True,
        details: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        cause: Optional[Exception] = None
    ):
        self.provider = provider
        self.status_code = status_code
        super().__init__(
            service=f"provider:{provider}",
            code=f"PROVIDER_ERROR_{status_code}",
            message=message,
            retryable=retryable,
            details=details or {"status_code": status_code},
            trace_id=trace_id,
            cause=cause
        )


class PipelineError(FrameworkError):
    """流水线执行错误"""
    
    def __init__(
        self,
        pipeline_id: str,
        node_id: str,
        message: str,
        details: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        cause: Optional[Exception] = None
    ):
        self.pipeline_id = pipeline_id
        self.node_id = node_id
        super().__init__(
            code="PIPELINE_ERROR",
            message=message,
            details=details or {"pipeline_id": pipeline_id, "node_id": node_id},
            trace_id=trace_id,
            cause=cause
        )


class ConfigError(FrameworkError):
    """配置错误"""
    
    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            code="CONFIG_ERROR",
            message=message,
            details=details
        )