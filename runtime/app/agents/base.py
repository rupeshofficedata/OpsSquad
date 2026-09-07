"""Base class for the per-agent modules in app/agents/.

Each agent's own module encodes what it actually *does* with its tools'
output — policy checks, guardrails, pass/fail logic — instead of every
agent being an interchangeable "call some tools, echo their output" loop.
This only runs in simulated mode (no ANTHROPIC_API_KEY): a real Claude run
reasons over the same tools itself via executor._run_with_claude and isn't
routed through these classes.

RBAC metadata (min_role, is_mutating, timeout, enabled) stays in the
`agents` Postgres table — that's what routes/security check against, and
what an admin could edit without a redeploy. These modules are purely about
execution *behavior* for a given slug.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from app.tools import get_tool


@dataclass
class AgentResult:
    status: str  # "success" | "failed"
    output: dict[str, Any]
    reasoning: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class AgentContext:
    """Bookkeeping wrapper an agent's `run()` uses to call its declared
    tools — every call is recorded so the persisted run_step trace looks
    the same shape regardless of which agent produced it."""

    def __init__(self, params: dict[str, Any]):
        self.params = params
        self.tool_calls: list[dict[str, Any]] = []

    async def call(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        tool = get_tool(tool_name)
        result = await tool.run(**kwargs)
        self.tool_calls.append({"tool": tool_name, "input": kwargs, "result": result.model_dump()})
        return result.data or {}


class SimulatedAgent(ABC):
    slug: str

    @abstractmethod
    async def run(self, ctx: AgentContext) -> AgentResult:
        ...
