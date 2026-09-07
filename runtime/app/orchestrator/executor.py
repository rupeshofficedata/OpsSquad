"""Runs a single agent to completion and returns its trace.

If ANTHROPIC_API_KEY is configured, the agent runs a real tool-use loop
against Claude. Otherwise it delegates to that agent's module in
app/agents/ — e.g. security-scan actually checks CVE severities against a
policy, verify checks a real error-rate threshold — so the rest of the
platform (RBAC, persistence, Flightplan branching) can be exercised with
realistic pass/fail behavior and no API key.
"""

import time
from typing import Any

from app.agents import AgentContext, get_agent_impl
from app.config import settings
from app.tools import get_tool

MAX_TOOL_ITERATIONS = 8


class AgentExecutionError(Exception):
    pass


async def run_agent(agent: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """agent: a row from the `agents` table (as dict). params: caller-supplied args."""
    start = time.monotonic()
    tool_names: list[str] = agent["tools"] if isinstance(agent["tools"], list) else []

    if settings.anthropic_api_key:
        output, reasoning, tool_calls = await _run_with_claude(agent, params, tool_names)
        status = "success"  # Claude reasons freely over the tools; we don't parse its text for pass/fail
    else:
        output, reasoning, tool_calls, status = await _run_simulated(agent, params, tool_names)

    duration_ms = int((time.monotonic() - start) * 1000)
    return {
        "status": status,
        "output": output,
        "reasoning": reasoning,
        "tool_calls": tool_calls,
        "duration_ms": duration_ms,
    }


async def _run_simulated(
    agent: dict[str, Any], params: dict[str, Any], tool_names: list[str]
) -> tuple[dict[str, Any], str, list[dict[str, Any]], str]:
    impl = get_agent_impl(agent["slug"])
    if impl is not None:
        ctx = AgentContext(params)
        result = await impl.run(ctx)
        return result.output, result.reasoning, result.tool_calls, result.status

    # Fallback for any agent row without a matching module in app/agents/ —
    # just invoke its declared tools and echo their (stub) output.
    tool_calls: list[dict[str, Any]] = []
    for name in tool_names:
        try:
            tool = get_tool(name)
            result = await tool.run(**params)
            tool_calls.append({"tool": name, "input": params, "result": result.model_dump()})
        except KeyError:
            tool_calls.append({"tool": name, "input": params, "result": {"ok": False, "error": "tool not registered"}})

    reasoning = (
        f"No app/agents/ module for '{agent['slug']}' — invoked its {len(tool_calls)} declared "
        f"tool(s) generically and echoed their stub output."
    )
    output = {
        "summary": f"Simulated run of '{agent['name']}' completed.",
        "tools_invoked": [tc["tool"] for tc in tool_calls],
    }
    return output, reasoning, tool_calls, "success"


async def _run_with_claude(
    agent: dict[str, Any], params: dict[str, Any], tool_names: list[str]
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    tool_defs = [
        {
            "name": name,
            "description": f"Invoke the '{name}' tool.",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": True},
        }
        for name in tool_names
    ]

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": f"Task parameters: {params}"}
    ]
    tool_calls: list[dict[str, Any]] = []
    reasoning_parts: list[str] = []
    final_text = ""

    for _ in range(MAX_TOOL_ITERATIONS):
        response = await client.messages.create(
            model=settings.agent_model,
            max_tokens=1024,
            system=agent["system_prompt"],
            tools=tool_defs,
            messages=messages,
        )

        text_blocks = [b.text for b in response.content if b.type == "text"]
        if text_blocks:
            reasoning_parts.append("\n".join(text_blocks))
            final_text = "\n".join(text_blocks)

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_use_blocks:
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in tool_use_blocks:
            try:
                tool = get_tool(block.name)
                result = await tool.run(**(block.input or {}))
                result_payload = result.model_dump()
            except KeyError:
                result_payload = {"ok": False, "error": f"tool '{block.name}' not registered"}

            tool_calls.append({"tool": block.name, "input": block.input, "result": result_payload})
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": str(result_payload)}
            )
        messages.append({"role": "user", "content": tool_results})

    return {"summary": final_text}, "\n".join(reasoning_parts), tool_calls
