from app.config import settings
from app.tools.base import Tool, ToolResult
from app.tools.redact import redact_obj, redact_text
from app.tools.real import REAL_TOOLS
from app.tools.stubs import SIMULATED_TOOLS, SimulatedTool



class _Redacted(Tool):
    """Every tool's result passes through here, so no caller (chat loop,
    Flightplan, direct run) can see a credential a tool happened to print."""

    def __init__(self, inner: Tool):
        self.inner, self.name = inner, inner.name

    async def run(self, **kwargs) -> ToolResult:
        r = await self.inner.run(**kwargs)
        return ToolResult(ok=r.ok, data=redact_obj(r.data), error=redact_text(r.error) if r.error else None)


TOOL_REGISTRY: dict[str, Tool] = {
    name: SimulatedTool(name, desc) for name, desc in SIMULATED_TOOLS.items()
}

if settings.real_tools_enabled:
    TOOL_REGISTRY.update(REAL_TOOLS)

TOOL_REGISTRY = {name: _Redacted(tool) for name, tool in TOOL_REGISTRY.items()}


def get_tool(name: str) -> Tool:
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        raise KeyError(f"Unknown tool '{name}'")
    return tool
