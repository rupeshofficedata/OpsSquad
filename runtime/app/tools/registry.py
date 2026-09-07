from app.tools.base import Tool
from app.tools.stubs import SIMULATED_TOOLS, SimulatedTool

TOOL_REGISTRY: dict[str, Tool] = {
    name: SimulatedTool(name, desc) for name, desc in SIMULATED_TOOLS.items()
}


def get_tool(name: str) -> Tool:
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        raise KeyError(f"Unknown tool '{name}'")
    return tool
