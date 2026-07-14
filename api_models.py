from pydantic import BaseModel
from typing import Optional, Dict, Any, List


class AgentExecute(BaseModel):
    agent_id: str
    action: str
    params: Optional[Dict[str, Any]] = {}


class PipelineRequest(BaseModel):
    project_id: str
    genre: Optional[str] = "古装"
    theme: Optional[str] = ""
    auto_start: Optional[bool] = True


class CharacterRequest(BaseModel):
    project_id: str
    characters: Optional[List[Dict[str, Any]]] = []
