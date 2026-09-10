"""Runs a single agent to completion and returns its trace.

Each user picks a model_provider ('anthropic' or 'local', see
users.model_provider) that decides how their agent runs execute:

- 'anthropic': a real tool-use loop against Claude, if ANTHROPIC_API_KEY is
  configured — otherwise falls back to simulated (see below).
- 'local': a real tool-use loop against a self-hosted OpenAI-compatible
  server (e.g. `llama-server`), at LOCAL_LLM_BASE_URL — falls back to
  simulated if that server isn't reachable.
- simulated (the fallback for either provider without a working backend, and
  the only mode when nothing is configured): delegates to that agent's
  module in app/agents/ — e.g. security-scan actually checks CVE severities
  against a policy, verify checks a real error-rate threshold — so the rest
  of the platform (RBAC, persistence, Flightplan branching) can be exercised
  with realistic pass/fail behavior and no LLM at all.
"""

import time
from typing import Any

import httpx

from app.agents import AgentContext, get_agent_impl
from app.config import settings
from app.tools import get_tool

MAX_TOOL_ITERATIONS = 8
LOCAL_LLM_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)


class AgentExecutionError(Exception):
    pass


async def run_agent(
    agent: dict[str, Any],
    params: dict[str, Any],
    model_provider: str = "anthropic",
    model_name: str | None = None,
) -> dict[str, Any]:
    """agent: a row from the `agents` table (as dict). params: caller-supplied args."""
    start = time.monotonic()
    tool_names: list[str] = agent["tools"] if isinstance(agent["tools"], list) else []

    if model_provider == "local":
        try:
            output, reasoning, tool_calls = await _run_with_local_llm(agent, params, tool_names, model_name)
            status = "success"  # same caveat as Claude below — we don't parse free text for pass/fail
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            output, reasoning, tool_calls, status = await _run_simulated(agent, params, tool_names)
            reasoning = f"Local LLM at {settings.local_llm_base_url} unreachable ({exc}) — ran simulated instead. {reasoning}"
    elif model_provider == "anthropic" and settings.anthropic_api_key:
        output, reasoning, tool_calls = await _run_with_claude(agent, params, tool_names, model_name)
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
    agent: dict[str, Any], params: dict[str, Any], tool_names: list[str], model_name: str | None
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
            model=model_name or settings.agent_model,
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


async def _run_with_local_llm(
    agent: dict[str, Any], params: dict[str, Any], tool_names: list[str], model_name: str | None
) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
    """Same tool-use loop as Claude, against an OpenAI-compatible
    /chat/completions endpoint (llama-server, Ollama, vLLM, ...). The wire
    format differs from Anthropic's: tool calls arrive as
    message.tool_calls[].function.{name,arguments} (arguments is a JSON
    string, not a parsed object), and results go back as role="tool"
    messages keyed by tool_call_id rather than role="user" tool_result blocks.
    """
    import json

    tool_defs = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": f"Invoke the '{name}' tool.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": True},
            },
        }
        for name in tool_names
    ]

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": agent["system_prompt"]},
        {"role": "user", "content": f"Task parameters: {params}"},
    ]
    tool_calls: list[dict[str, Any]] = []
    reasoning_parts: list[str] = []
    final_text = ""

    async with httpx.AsyncClient(timeout=LOCAL_LLM_TIMEOUT) as client:
        for _ in range(MAX_TOOL_ITERATIONS):
            resp = await client.post(
                f"{settings.local_llm_base_url}/chat/completions",
                json={
                    "model": model_name or "local-model",
                    "messages": messages,
                    "tools": tool_defs,
                    "tool_choice": "auto",
                },
            )
            resp.raise_for_status()
            message = resp.json()["choices"][0]["message"]

            if message.get("content"):
                reasoning_parts.append(message["content"])
                final_text = message["content"]

            calls = message.get("tool_calls") or []
            if not calls:
                break

            messages.append(message)
            for call in calls:
                name = call["function"]["name"]
                try:
                    call_args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    call_args = {}

                try:
                    tool = get_tool(name)
                    result = await tool.run(**call_args)
                    result_payload = result.model_dump()
                except KeyError:
                    result_payload = {"ok": False, "error": f"tool '{name}' not registered"}

                tool_calls.append({"tool": name, "input": call_args, "result": result_payload})
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": str(result_payload)})

    return {"summary": final_text}, "\n".join(reasoning_parts), tool_calls
