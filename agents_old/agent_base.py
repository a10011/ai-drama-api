# agents/base.py
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import logging

from core.errors import AgentError
from core.tracing import tracer, trace
from models.agent_models import AgentInput, AgentOutput, AgentType
from services.model_client_v2 import ModelClient
from tools.registry_v2 import ToolRegistry

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Base class for all agents"""
    
    def __init__(
        self,
        agent_type: AgentType,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
        config: Optional[Dict[str, Any]] = None
    ):
        self.agent_type = agent_type
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.config = config or {}
        self._initialized = False
    
    @abstractmethod
    async def execute(self, input_data: AgentInput) -> AgentOutput:
        """Execute the agent's main logic"""
        pass
    
    async def initialize(self):
        """Initialize agent resources"""
        self._initialized = True
        logger.info(f"Agent {self.agent_type} initialized")
    
    async def cleanup(self):
        """Cleanup agent resources"""
        self._initialized = False
        logger.info(f"Agent {self.agent_type} cleaned up")
    
    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tools: Optional[List[Dict]] = None
    ) -> Dict[str, Any]:
        """Call LLM with standard error handling"""
        trace_id = tracer.get_trace_id()
        
        try:
            response = await self.model_client.chat_completion(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                tools=tools,
                trace_id=trace_id
            )
            
            return response
            
        except Exception as e:
            raise AgentError(
                agent_id=self.agent_type.value,
                node_id="llm_call",
                code="LLM_CALL_FAILED",
                message=f"LLM call failed: {str(e)}",
                trace_id=trace_id,
                cause=e
            )
    
    async def _execute_tool(self, tool_name: str, **kwargs) -> Any:
        """Execute a tool with error handling"""
        trace_id = tracer.get_trace_id()
        
        try:
            result = await self.tool_registry.execute(tool_name, **kwargs)
            return result
            
        except Exception as e:
            raise AgentError(
                agent_id=self.agent_type.value,
                node_id=f"tool_{tool_name}",
                code="TOOL_EXECUTION_FAILED",
                message=f"Tool '{tool_name}' execution failed: {str(e)}",
                trace_id=trace_id,
                cause=e
            )
    
    def _validate_input(self, input_data: AgentInput) -> bool:
        """Validate agent input"""
        if not input_data.project_id:
            raise AgentError(
                agent_id=self.agent_type.value,
                node_id="validation",
                code="INVALID_INPUT",
                message="project_id is required",
                trace_id=input_data.trace_id
            )
        return True
    
    def _create_output(
        self,
        success: bool,
        data: Optional[Dict[str, Any]] = None,
        errors: Optional[List[str]] = None
    ) -> AgentOutput:
        """Create standard agent output"""
        return AgentOutput(
            success=success,
            data=data or {},
            errors=errors or [],
            metrics={}
        )
