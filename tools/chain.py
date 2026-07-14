"""
ToolChain — 工具链式组合
brainstorm → quality_filter → plot_twist → audience_simulate → grade
"""
from typing import List, Dict, Any
from .base import AgentTool, ToolResult


class ToolChain:
    def __init__(self, name: str, tools: List[AgentTool]):
        self.name = name
        self.tools = tools

    async def run(self, initial_input: Dict[str, Any]) -> List[ToolResult]:
        results = []
        ctx = dict(initial_input)
        for tool in self.tools:
            result = await tool.execute(**ctx)
            results.append(result)
            if not result.success:
                break
            ctx.update(result.data)
        return results
