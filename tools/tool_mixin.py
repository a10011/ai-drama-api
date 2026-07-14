"""
AgentBase 工具扩展 — 通过 mixin 方式添加工具支持
不改动原有 agent_base.py，在 pipeline 层注入
"""
from typing import Dict, List, Optional
from tools.base import AgentTool, ToolResult


class ToolMixin:
    """混入BaseAgent，添加工具能力"""
    tool_registry = None
    agent_name_for_tools: str = ""

    def set_tools(self, registry, agent_name: str):
        """注入工具注册表和Agent名称"""
        self.tool_registry = registry
        self.agent_name_for_tools = agent_name

    async def use_tool(self, tool_name: str, **kwargs) -> ToolResult:
        """Agent调用工具的统一入口"""
        if not self.tool_registry:
            return ToolResult(success=False, error="tool_registry未初始化")
        tool = self.tool_registry.get_tool(tool_name)
        if not tool:
            return ToolResult(success=False, error=f"工具{tool_name}不存在")
        if not tool.validate(**kwargs):
            return ToolResult(success=False, error=f"工具{tool_name}参数验证失败")
        return await tool.execute(**kwargs)

    def get_tool_functions(self) -> List[Dict]:
        """返回当前Agent可用工具的function calling格式，供LLM选择"""
        if not self.tool_registry:
            return []
        tools = self.tool_registry.get_agent_tools(self.agent_name_for_tools)
        return [t.explain() for t in tools]

    def list_tools(self) -> List[str]:
        if not self.tool_registry:
            return []
        return self.tool_registry.list_agent(self.agent_name_for_tools)
