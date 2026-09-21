from typing import Any

from pydantic import BaseModel


class ChatRequest(BaseModel):
    prompt: str
    env: str = "staging"
    # The root run's id of an existing thread to continue — omit/null to
    # start a new thread (see routes/chat.py's build_thread_history).
    thread_id: str | None = None


class ChatReplyRequest(BaseModel):
    reply: str


class AgentRunRequest(BaseModel):
    params: dict[str, Any] = {}
    env: str = "staging"


class FlightplanExecuteRequest(BaseModel):
    inputs: dict[str, Any] = {}
