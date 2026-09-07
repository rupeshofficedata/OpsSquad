from typing import Any

from pydantic import BaseModel


class ChatRequest(BaseModel):
    prompt: str
    env: str = "staging"


class AgentRunRequest(BaseModel):
    params: dict[str, Any] = {}
    env: str = "staging"


class FlightplanExecuteRequest(BaseModel):
    inputs: dict[str, Any] = {}
