"""
ToolRegistry - 集中式工具池
Orchestrator层统一管理，Agent按需声明工具
"""
from typing import Dict, List
from .base import AgentTool


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, AgentTool] = {}
        self._assignments: Dict[str, List[str]] = {}

    def register(self, tool: AgentTool):
        self._tools[tool.name] = tool

    def assign(self, agent_name: str, tool_names: List[str]):
        self._assignments[agent_name] = tool_names

    def get_agent_tools(self, agent_name: str) -> List[AgentTool]:
        names = self._assignments.get(agent_name, [])
        return [self._tools[n] for n in names if n in self._tools]

    def get_tool(self, name: str) -> AgentTool:
        return self._tools.get(name)

    def list_all(self) -> List[str]:
        return list(self._tools.keys())

    def list_agent(self, agent_name: str) -> List[str]:
        return self._assignments.get(agent_name, [])
