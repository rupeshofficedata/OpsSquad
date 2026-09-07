from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class ToolResult(BaseModel):
    ok: bool
    data: Any = None
    error: str | None = None


class Tool(ABC):
    """A single named capability an agent can invoke (kubectl, trivy, git, ...).

    Real implementations shell out to the actual CLI/API. Each tool should be
    given the narrowest credential it needs — never a shared god credential.
    """

    name: str

    @abstractmethod
    async def run(self, **kwargs: Any) -> ToolResult:
        ...
