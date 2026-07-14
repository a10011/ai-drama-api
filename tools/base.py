"""
智能体工具箱 v1.0 — 基础类
ToolResult + AgentTool + ToolSchema
"""
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field


@dataclass
class ToolResult:
    """工具执行结果 — 支持 dict 风格访问（兼容老代码）"""
    success: bool
    data: Dict[str, Any] = field(default_factory=dict)
    quality_score: float = 0.0
    suggestions: List[str] = field(default_factory=list)
    error: str = ""

    def get(self, key, default=None):
        """dict-like get: 优先从 data 取，兼容 success/error 等字段"""
        if key in self.data:
            return self.data[key]
        if hasattr(self, key):
            return getattr(self, key)
        return default

    def __getitem__(self, key):
        if key in self.data:
            return self.data[key]
        if hasattr(self, key):
            v = getattr(self, key)
            if callable(v):
                raise KeyError(key)
            return v
        raise KeyError(key)

    def __contains__(self, key):
        return key in self.data or hasattr(self, key)


class AgentTool:
    """智能体工具基类 — 每个工具继承此类"""
    name: str = "base_tool"
    description: str = ""
    category: str = "general"

    async def execute(self, **kwargs) -> ToolResult:
        raise NotImplementedError

    def validate(self, **kwargs) -> bool:
        return True

    def explain(self) -> Dict[str, Any]:
        """返回给LLM看的function calling格式"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {}
        }

    def _call_llm(self, prompt: str, system: str = "") -> str:
        """内部LLM调用 — UnifiedModel.llm 返回 ModelResult dict"""
        from services.model_client import UnifiedModel
        result = UnifiedModel.llm(prompt=prompt, system=system, max_tokens=4096)
        if isinstance(result, dict):
            text = result.get("text", result.get("content", ""))
            return str(text)
        return str(result)

    def _ok(self, data: dict, score: float = 80.0, tips: list = None) -> ToolResult:
        return ToolResult(success=True, data=data, quality_score=score, suggestions=tips or [])

    def _fail(self, err: str) -> ToolResult:
        return ToolResult(success=False, error=err, quality_score=0)
