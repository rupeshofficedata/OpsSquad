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

import json
import re
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
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            # HTTPStatusError added after a live 500 from llama-server itself
            # (not a connection problem) crashed this endpoint outright —
            # any local-LLM failure should degrade to simulated, the same
            # way an unreachable server already does, not surface a raw 500.
            output, reasoning, tool_calls, status = await _run_simulated(agent, params, tool_names)
            reasoning = f"Local LLM at {settings.local_llm_base_url} failed ({exc}) — ran simulated instead. {reasoning}"
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


# Any (or no) fence language tag — observed live: ```xml wrapping a JSON
# payload, not just ```json.
_CODE_FENCE_RE = re.compile(r"```(?:\w+)?\s*(.*?)\s*```", re.DOTALL)
# A self-closing-ish XML tag with attributes — observed live in at least 3
# shapes: <function name="..." arguments="..."/>, <tool name="..."
# arguments="..."/>, and <secrets.scan arguments="..."/> (the tag name
# itself IS the tool name, no separate name= attribute at all).
_XML_TAG_RE = re.compile(r"<([\w][\w.]*)\s+([^<>]*?)/?>", re.DOTALL)
_XML_NAME_ATTR_RE = re.compile(r'\bname\s*=\s*(["\'])(.*?)\1', re.DOTALL)
_XML_ARGS_ATTR_START_RE = re.compile(r"\barguments\s*=\s*")
# Generic wrapper tag names that are never themselves a real tool name (a
# real tool name always has a dot, e.g. "kubectl.get") — for these the
# actual name must come from a name= attribute instead.
_GENERIC_XML_TAGS = {"tool", "tools", "function", "functions", "response", "call", "invoke"}


def _extract_attr_value_at(text: str, start: int) -> str | None:
    """The value of an XML-ish attribute at `start` (right after its `=`)
    — either quoted ("..."/'...') or, observed live, an unquoted bare
    {...} object (arguments={} with no quotes around it at all)."""
    if start >= len(text):
        return None
    ch = text[start]
    if ch in ("'", '"'):
        end = text.find(ch, start + 1)
        return text[start + 1:end] if end != -1 else None
    if ch == "{":
        objs = _find_balanced_json_objects(text[start:])
        return objs[0] if objs else None
    return None


def _parse_xml_style_call(tag_name: str, attrs_text: str) -> dict[str, Any] | None:
    args_match = _XML_ARGS_ATTR_START_RE.search(attrs_text)
    args_raw = _extract_attr_value_at(attrs_text, args_match.end()) if args_match else None
    if args_raw is None:
        return None
    name_match = _XML_NAME_ATTR_RE.search(attrs_text)
    name = (name_match.group(2) if name_match else None) or (
        tag_name if tag_name.lower() not in _GENERIC_XML_TAGS else None
    )
    if not name:
        return None
    # Python-dict-style single-quoted values observed live too
    # (arguments="{ 'key': 'value' }") — not valid JSON as-is.
    for candidate in (args_raw, args_raw.replace("'", '"')):
        try:
            return {"name": name, "arguments": json.loads(candidate) if candidate.strip() else {}}
        except json.JSONDecodeError:
            continue
    return {"name": name, "arguments": {}}


def _find_balanced_json_objects(text: str) -> list[str]:
    """Every top-level {...} substring via brace counting — robust against
    arbitrary wrapper noise around it (XML tags, prose, multiple objects
    concatenated without array brackets), unlike a single json.loads call."""
    objects: list[str] = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                objects.append(text[start:i + 1])
                start = None
    return objects


def _parse_tool_call_json(blob: str) -> dict[str, Any] | None:
    # {{...}} instead of {...} observed live too — a leaked Jinja
    # chat-template artifact, not deliberate JSON syntax; genuine JSON
    # essentially never has literal adjacent "{{"/"}}", so normalizing it
    # is safe.
    for candidate in (blob, blob.replace("{{", "{").replace("}}", "}")):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "name" in obj and "arguments" in obj:
            return obj
    return None


def _extract_content_tool_calls(content: str) -> list[dict[str, Any]]:
    """Best-effort parse of tool-call-shaped data a local model wrote into
    its text response instead of the structured tool_calls field — this
    7B model has been observed live using at least 4 different malformed
    shapes for the same intent: bare JSON, a ```json fence, a ```xml fence
    wrapping JSON (sometimes with doubled braces or multiple concatenated
    objects), and a Hermes-style <function name=... arguments=.../> tag.
    Handles all of them via brace-counting rather than one regex per shape,
    since a small model's inconsistency here isn't a fixed, enumerable set."""
    text = content.strip()

    found: list[dict[str, Any]] = []
    for tag_name, attrs_text in _XML_TAG_RE.findall(text):
        call = _parse_xml_style_call(tag_name, attrs_text)
        if call:
            found.append(call)

    fences = _CODE_FENCE_RE.findall(text)
    for candidate in (fences if fences else [text]):
        blobs = _find_balanced_json_objects(candidate) or [candidate]
        for blob in blobs:
            obj = _parse_tool_call_json(blob)
            if obj:
                found.append(obj)
    return found


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
        # Smaller local models are inconsistent about initiating tool use on
        # a bare params dump (observed: describing what it would do in
        # prose instead of calling anything) — Claude doesn't need this
        # nudge, but it's harmless there too.
        {"role": "user", "content": f"Task parameters: {params}\n\nUse the available tools to complete this task, then summarize the result."},
    ]
    tool_calls: list[dict[str, Any]] = []
    reasoning_parts: list[str] = []
    final_text = ""
    seen_call_signatures: set[str] = set()
    fallback_call_counter = 0
    stopped_cleanly = False

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
            if not calls and message.get("content"):
                # Small/local models frequently don't emit the structured
                # OpenAI tool_calls field even with --jinja and a tools
                # payload — they write the call(s) as JSON text instead
                # (observed with Qwen2.5-Coder via llama-server), sometimes
                # several fenced calls in one message. Fall back to parsing
                # those out of content rather than treating a clear
                # tool-call attempt as "no tool calls, we're done."
                for fallback in _extract_content_tool_calls(message["content"]):
                    calls.append({
                        "id": f"local-fallback-{fallback_call_counter}",
                        "type": "function",
                        "function": {"name": fallback["name"], "arguments": json.dumps(fallback.get("arguments", {}))},
                    })
                    fallback_call_counter += 1
                if calls:
                    # The model never actually emitted a structured
                    # tool_calls field, so message.tool_calls is still
                    # missing/empty — appending message as-is to history
                    # would then pair the tool-result messages below with a
                    # tool_call_id the assistant never declared, which
                    # local models frequently fail to recognize as "this
                    # already ran" and just re-propose it forever instead
                    # of stopping. Declare it properly before it's appended.
                    message = {**message, "tool_calls": calls}
            if not calls:
                stopped_cleanly = True
                break

            # Small local models frequently fail to recognize a tool result
            # as an answer and re-issue the exact same call forever instead
            # of stopping (observed with Qwen2.5-Coder-7B) — without this,
            # that burns every iteration on identical repeated no-op calls.
            signatures = {call["function"]["name"] + "|" + (call["function"].get("arguments") or "") for call in calls}
            if signatures <= seen_call_signatures:
                stopped_cleanly = True
                break
            seen_call_signatures |= signatures

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

    # A "clean" stop isn't always a real answer — observed live: the
    # model's last proposal used invalid JSON (Python-style single-quoted
    # list) so it silently failed to parse into a call, the loop correctly
    # saw "no calls" and stopped, but final_text is still that broken
    # proposal, not prose. Treat that the same as running out of
    # iterations: prefer the real tool results already collected.
    final_looks_unexecuted = "<function " in final_text or (
        "```" in final_text and "arguments" in final_text and ('"name"' in final_text or "'name'" in final_text)
    )
    if tool_calls and (not stopped_cleanly or final_looks_unexecuted):
        final_text = f"Model did not produce a final summary after {MAX_TOOL_ITERATIONS} tool-call rounds. Real tool results:\n" + "\n".join(
            f"- {tc['tool']}({tc['input']}) -> {tc['result'].get('data') or tc['result'].get('error')}" for tc in tool_calls
        )

    return {"summary": final_text}, "\n".join(reasoning_parts), tool_calls
