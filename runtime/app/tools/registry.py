from app.config import settings
from app.tools.base import Tool
from app.tools.real import REAL_TOOLS
from app.tools.stubs import SIMULATED_TOOLS, SimulatedTool

TOOL_REGISTRY: dict[str, Tool] = {
    name: SimulatedTool(name, desc) for name, desc in SIMULATED_TOOLS.items()
}

if settings.real_tools_enabled:
    TOOL_REGISTRY.update(REAL_TOOLS)


def get_tool(name: str) -> Tool:
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        raise KeyError(f"Unknown tool '{name}'")
    return tool
