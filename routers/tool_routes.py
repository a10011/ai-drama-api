"""工具API端点"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from fastapi import APIRouter
from tools.init_tools import create_registry

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])

_registry = None

def get_registry():
    global _registry
    if _registry is None:
        _registry = create_registry()
    return _registry

@router.get("/list")
async def list_tools():
    reg = get_registry()
    return {"success": True, "data": {"tools": reg.list_all()}}

@router.get("/agent/{agent_name}")
async def agent_tools(agent_name: str):
    reg = get_registry()
    tools = reg.list_agent(agent_name)
    if not tools:
        return {"success": False, "error": f"Agent {agent_name} not found"}
    return {"success": True, "data": {"agent": agent_name, "tools": tools}}

@router.get("/{tool_name}")
async def tool_info(tool_name: str):
    reg = get_registry()
    tool = reg.get_tool(tool_name)
    if not tool:
        return {"success": False, "error": f"Tool {tool_name} not found"}
    return {"success": True, "data": tool.explain()}
