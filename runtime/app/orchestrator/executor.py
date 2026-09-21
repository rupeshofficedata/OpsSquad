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
from typing import Any, Awaitable, Callable

import httpx

from app.agents import AgentContext, get_agent_impl
from app.config import settings
from app.tools import get_tool
from app.tools.schemas import TOOL_SCHEMAS
from app.tools.stubs import SIMULATED_TOOLS

MAX_TOOL_ITERATIONS = 8
LOCAL_LLM_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)

# A real declared tool, not prose-parsing — lets the model pause a run and
# ask the person something instead of guessing. Only ever added to tool_defs
# for allow_ask_user=True (chat-originated runs — see routes/chat.py). A
# cron/webhook/Flightplan run never gets it: nobody's there to answer, and
# the run would just hang at 'awaiting_user_input' forever.
ASK_USER_TOOL_NAME = "ask_user"
ASK_USER_SCHEMA = {
    "type": "object",
    "properties": {"question": {"type": "string", "description": "The question to ask the user before continuing"}},
    "required": ["question"],
}
ASK_USER_DESCRIPTION = "Ask the user a clarifying question and pause until they reply. Only use when you genuinely can't proceed without their input."

# Any tool that changes real state — everything else (reads/scans) never
# gates. Checked per round, not per agent: the old is_mutating+prod gate
# (routes/flightplans.py) is whole-agent and only covers prod; this is
# finer-grained and applies to chat runs regardless of env.
MUTATING_TOOLS = {
    "kubectl.restart", "kubectl.scale",
    "terraform.apply",
    "helm.upgrade", "helm.rollback",
    "argocd.sync", "argocd.rollback",
    "docker.build", "docker.tag",
    "registry.push",
}
MUTATION_ROLE_BAR = ("dev", "admin")


class AgentExecutionError(Exception):
    pass


NARRATION_SYSTEM_PROMPT = (
    "You write short, clear dashboard summaries of DevOps automation runs for a human "
    "operator. You'll be given the real step-by-step results (JSON) of a Flightplan run — "
    "what each step actually did and returned. Write a concise markdown summary: what "
    "happened, what succeeded, what failed, and any real numbers/targets worth calling out. "
    "Use **bold** labels and '- ' bullet points. Never invent a value that isn't in the data. "
    "Keep it under 150 words."
)


async def summarize_run(steps: dict[str, Any], model_provider: str, model_name: str | None) -> str | None:
    """A single one-shot completion (no tool loop) turning a Flightplan run's
    raw step JSON into a short human-readable dashboard summary — chat runs
    already get this for free from the agent's own final answer; Flightplan
    runs never had any narration at all, just raw JSON. Best-effort:
    narration is decorative, so any failure here (model down, timeout, bad
    response) returns None rather than breaking the run that's finishing."""
    prompt = f"Run results:\n{json.dumps(steps, default=str)[:6000]}"
    try:
        if model_provider == "local":
            async with httpx.AsyncClient(timeout=LOCAL_LLM_TIMEOUT) as client:
                resp = await client.post(
                    f"{settings.local_llm_base_url}/chat/completions",
                    json={
                        "model": model_name or "local-model",
                        "messages": [
                            {"role": "system", "content": NARRATION_SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                    },
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"].get("content") or None
        elif model_provider == "anthropic" and settings.anthropic_api_key:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            response = await client.messages.create(
                model=model_name or settings.agent_model,
                max_tokens=400,
                system=NARRATION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text_blocks = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_blocks) or None
    except Exception:
        return None
    return None


THREAD_SUMMARY_SYSTEM_PROMPT = (
    "You compress an in-progress chat conversation between an operator and a DevOps "
    "assistant into a short memory note for yourself to keep reading. Preserve concrete "
    "facts, decisions, and anything left unresolved a later reply would need. Drop small "
    "talk and restating tool output already acted on. Plain prose, under 200 words."
)


async def summarize_thread(turns: list[dict[str, str]], model_provider: str, model_name: str | None) -> str | None:
    """Same one-shot-completion, best-effort-None-on-failure shape as
    summarize_run above, but compressing older turns of a chat thread (see
    build_thread_history in routes/chat.py) instead of a Flightplan run's
    step JSON. `turns`: [{"role": "user"|"agent", "content": str}, ...]."""
    transcript = "\n".join(f"{t['role']}: {t['content']}" for t in turns)
    prompt = f"Conversation so far:\n{transcript[:8000]}"
    try:
        if model_provider == "local":
            async with httpx.AsyncClient(timeout=LOCAL_LLM_TIMEOUT) as client:
                resp = await client.post(
                    f"{settings.local_llm_base_url}/chat/completions",
                    json={
                        "model": model_name or "local-model",
                        "messages": [
                            {"role": "system", "content": THREAD_SUMMARY_SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                    },
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"].get("content") or None
        elif model_provider == "anthropic" and settings.anthropic_api_key:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
            response = await client.messages.create(
                model=model_name or settings.agent_model,
                max_tokens=400,
                system=THREAD_SUMMARY_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text_blocks = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_blocks) or None
    except Exception:
        return None
    return None


def build_resume_messages(model_provider: str, messages: list[dict[str, Any]], reply: str) -> list[dict[str, Any]]:
    """`messages` is the raw provider-format history returned by run_agent()
    when it paused on ask_user (its last entry is the assistant turn that
    called ask_user, still unanswered). Appends the user's reply as the
    matching tool result so the loop can resume exactly where it paused —
    each provider requires that shape before it will accept the next turn."""
    last = messages[-1]
    if model_provider == "anthropic":
        ask_block = next(b for b in last["content"] if b.get("type") == "tool_use" and b.get("name") == ASK_USER_TOOL_NAME)
        return messages + [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": ask_block["id"], "content": reply}]}]
    call = next(c for c in last["tool_calls"] if c["function"]["name"] == ASK_USER_TOOL_NAME)
    return messages + [{"role": "tool", "tool_call_id": call["id"], "content": reply}]


async def execute_paused_round(
    model_provider: str, messages: list[dict[str, Any]], approved: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resumes a round paused on a mutating-command approval (see
    MUTATING_TOOLS above). `messages[-1]` is the still-unanswered assistant
    turn — derives every call proposed that round (same pattern as
    build_resume_messages), executes each for real: the mutating one per
    `approved`, everything else normally, since it was never denied, just
    held alongside it (a provider requires every tool call in one turn
    answered before the next). Returns (updated messages, the tool_calls
    actually executed this round) for the caller to persist/stream the same
    as a normal on_step round.

    ponytail: if a round proposes two different mutating calls, both are
    approved/denied together as one decision — no per-call granularity.
    Revisit only if that's actually hit in practice."""
    last = messages[-1]
    round_tool_calls: list[dict[str, Any]] = []

    async def run_one(name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name in MUTATING_TOOLS and not approved:
            return {"ok": False, "error": "Denied by user."}
        try:
            tool = get_tool(name)
            result = await tool.run(**args)
            return result.model_dump()
        except KeyError:
            return {"ok": False, "error": f"tool '{name}' not registered"}

    if model_provider == "anthropic":
        tool_use_blocks = [b for b in last["content"] if b.get("type") == "tool_use"]
        tool_results = []
        for block in tool_use_blocks:
            args = block.get("input") or {}
            result_payload = await run_one(block["name"], args)
            round_tool_calls.append({"tool": block["name"], "input": args, "result": result_payload})
            tool_results.append({"type": "tool_result", "tool_use_id": block["id"], "content": str(result_payload)})
        messages = messages + [{"role": "user", "content": tool_results}]
    else:
        appended = []
        for call in last.get("tool_calls") or []:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result_payload = await run_one(name, args)
            round_tool_calls.append({"tool": name, "input": args, "result": result_payload})
            appended.append({"role": "tool", "tool_call_id": call["id"], "content": str(result_payload)})
        messages = messages + appended

    return messages, round_tool_calls


# Called after each tool-use round with (that round's reasoning text, that
# round's tool calls) — lets a caller (routes/chat.py) persist/stream
# progress live instead of only seeing the final aggregated result.
OnStep = Callable[[str, list[dict[str, Any]]], Awaitable[None]]


async def run_agent(
    agent: dict[str, Any],
    params: dict[str, Any],
    model_provider: str = "anthropic",
    model_name: str | None = None,
    tool_call_mode: str = "lenient",
    allow_ask_user: bool = False,
    history: list[dict[str, Any]] | None = None,
    on_step: OnStep | None = None,
    user_role: str | None = None,
    pre_approved: bool = False,
) -> dict[str, Any]:
    """agent: a row from the `agents` table (as dict). params: caller-supplied args.

    tool_call_mode: local-model only. 'lenient' (default) keeps every
    fallback tool-call parser; 'strict' trusts only the structured
    tool_calls field, same rule Claude already follows.
    allow_ask_user / history: see ASK_USER_TOOL_NAME above and
    routes/chat.py's POST /chat + POST /chat/{run_id}/reply.
    on_step: see OnStep above.
    user_role / pre_approved: see MUTATING_TOOLS above. pre_approved=True
    for Flightplan runs (the YAML step declaring a mutating tool is the
    human's advance sign-off — see graph.py) — chat runs pause instead.
    """
    start = time.monotonic()
    tool_names: list[str] = agent["tools"] if isinstance(agent["tools"], list) else []
    asked_question: str | None = None
    pending_command: dict[str, Any] | None = None
    messages_out: list[dict[str, Any]] | None = None
    # True whenever output/reasoning came from _run_simulated — no LLM
    # actually ran, so output["summary"] (if the fallback even sets one) is
    # a canned string like "Simulated run of 'x' completed.", not a real
    # answer. Callers that persist a run's answer as conversation memory
    # (routes/chat.py's build_thread_history) must check this and skip —
    # feeding that fake text back to a model as if it were a prior real
    # turn would corrupt everything downstream.
    simulated = False

    if model_provider == "local":
        try:
            output, reasoning, tool_calls, asked_question, pending_command, messages_out = await _run_with_local_llm(
                agent, params, tool_names, model_name, tool_call_mode, allow_ask_user, history, on_step,
                user_role, pre_approved,
            )
            status = "success"  # same caveat as Claude below — we don't parse free text for pass/fail
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError) as exc:
            # HTTPStatusError added after a live 500 from llama-server itself
            # (not a connection problem) crashed this endpoint outright —
            # any local-LLM failure should degrade to simulated, the same
            # way an unreachable server already does, not surface a raw 500.
            output, reasoning, tool_calls, status = await _run_simulated(agent, params, tool_names)
            reasoning = f"Local LLM at {settings.local_llm_base_url} failed ({exc}) — ran simulated instead. {reasoning}"
            simulated = True
    elif model_provider == "anthropic" and settings.anthropic_api_key:
        output, reasoning, tool_calls, asked_question, pending_command, messages_out = await _run_with_claude(
            agent, params, tool_names, model_name, allow_ask_user, history, on_step, user_role, pre_approved,
        )
        status = "success"  # Claude reasons freely over the tools; we don't parse its text for pass/fail
    else:
        output, reasoning, tool_calls, status = await _run_simulated(agent, params, tool_names)
        simulated = True

    if pending_command:
        status = "awaiting_command_approval"
    elif asked_question:
        status = "awaiting_user_input"

    duration_ms = int((time.monotonic() - start) * 1000)
    return {
        "status": status,
        "output": output,
        "reasoning": reasoning,
        "tool_calls": tool_calls,
        "duration_ms": duration_ms,
        "asked_question": asked_question,
        "pending_command": pending_command,
        "messages": messages_out,
        "simulated": simulated,
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
    # just invoke its declared tools and echo their (stub) output. No LLM
    # is making a decision here (that's why we're in this branch at all),
    # so there is nobody to ask before running a mutating tool and no
    # in-progress run to pause/resume the way the LLM loops do — refuse
    # every MUTATING_TOOLS call outright, always, regardless of role or
    # env. Found live: this generic loop had zero gating and, invoked
    # against the 'assistant' agent's full tool catalog on a model-
    # provider fallback (no API key configured), really executed every
    # declared tool including real kubectl/terraform/docker/helm/argocd/
    # registry mutations in one shot — see
    # docs/superpowers/plans/generalist-chat-agent-and-routing-removal.md.
    tool_calls: list[dict[str, Any]] = []
    for name in tool_names:
        if name in MUTATING_TOOLS:
            tool_calls.append({
                "tool": name, "input": params,
                "result": {"ok": False, "error": "refused: no model available to decide whether to run this mutating action"},
            })
            continue
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
    agent: dict[str, Any], params: dict[str, Any], tool_names: list[str], model_name: str | None,
    allow_ask_user: bool = False, history: list[dict[str, Any]] | None = None, on_step: "OnStep | None" = None,
    user_role: str | None = None, pre_approved: bool = False,
) -> tuple[dict[str, Any], str, list[dict[str, Any]], str | None, dict[str, Any] | None, list[dict[str, Any]]]:
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    tool_defs = [
        {"name": name, "description": SIMULATED_TOOLS[name], "input_schema": TOOL_SCHEMAS[name]}
        for name in tool_names
    ]
    if allow_ask_user:
        tool_defs.append({"name": ASK_USER_TOOL_NAME, "description": ASK_USER_DESCRIPTION, "input_schema": ASK_USER_SCHEMA})

    messages: list[dict[str, Any]] = history or [
        {"role": "user", "content": f"Task parameters: {params}"}
    ]
    tool_calls: list[dict[str, Any]] = []
    reasoning_parts: list[str] = []
    final_text = ""
    asked_question: str | None = None
    pending_command: dict[str, Any] | None = None

    for _ in range(MAX_TOOL_ITERATIONS):
        response = await client.messages.create(
            model=model_name or settings.agent_model,
            max_tokens=1024,
            system=agent["system_prompt"],
            tools=tool_defs,
            messages=messages,
        )

        text_blocks = [b.text for b in response.content if b.type == "text"]
        round_text = "\n".join(text_blocks)
        if text_blocks:
            reasoning_parts.append(round_text)
            final_text = round_text

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_use_blocks:
            if on_step:
                await on_step(round_text, [])
            break

        # Stored as plain dicts (not the SDK's pydantic content-block
        # objects) so this history round-trips through the `runs.inputs`
        # JSONB column when a run pauses on ask_user and resumes later
        # (see build_resume_messages / routes/chat.py's /reply).
        messages.append({"role": "assistant", "content": [b.model_dump() for b in response.content]})

        ask_block = next((b for b in tool_use_blocks if b.name == ASK_USER_TOOL_NAME), None)
        if ask_block:
            asked_question = (ask_block.input or {}).get("question", "")
            if on_step:
                await on_step(round_text, [])
            break

        # A round proposing any mutating call must pause entirely, before
        # running ANYTHING in it — the API requires every tool_use in this
        # turn get an answer before the next one, so partial execution
        # would leave the round structurally unresumable.
        if not pre_approved:
            gate_block = next((b for b in tool_use_blocks if b.name in MUTATING_TOOLS), None)
            if gate_block and (user_role in MUTATION_ROLE_BAR):
                pending_command = {"tool": gate_block.name, "input": gate_block.input}
                if on_step:
                    await on_step(round_text, [])
                break

        round_tool_calls: list[dict[str, Any]] = []
        tool_results = []
        for block in tool_use_blocks:
            if block.name in MUTATING_TOOLS and not pre_approved and user_role not in MUTATION_ROLE_BAR:
                result_payload = {"ok": False, "error": f"tool '{block.name}' requires dev role or higher (you are '{user_role or 'viewer'}')"}
            else:
                try:
                    tool = get_tool(block.name)
                    result = await tool.run(**(block.input or {}))
                    result_payload = result.model_dump()
                except KeyError:
                    result_payload = {"ok": False, "error": f"tool '{block.name}' not registered"}

            entry = {"tool": block.name, "input": block.input, "result": result_payload}
            tool_calls.append(entry)
            round_tool_calls.append(entry)
            tool_results.append(
                {"type": "tool_result", "tool_use_id": block.id, "content": str(result_payload)}
            )
        messages.append({"role": "user", "content": tool_results})
        if on_step:
            await on_step(round_text, round_tool_calls)

    return {"summary": final_text}, "\n".join(reasoning_parts), tool_calls, asked_question, pending_command, messages


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
    agent: dict[str, Any], params: dict[str, Any], tool_names: list[str], model_name: str | None,
    tool_call_mode: str = "lenient", allow_ask_user: bool = False, history: list[dict[str, Any]] | None = None,
    on_step: "OnStep | None" = None, user_role: str | None = None, pre_approved: bool = False,
) -> tuple[dict[str, Any], str, list[dict[str, Any]], str | None, dict[str, Any] | None, list[dict[str, Any]]]:
    """Same tool-use loop as Claude, against an OpenAI-compatible
    /chat/completions endpoint (llama-server, Ollama, vLLM, ...). The wire
    format differs from Anthropic's: tool calls arrive as
    message.tool_calls[].function.{name,arguments} (arguments is a JSON
    string, not a parsed object), and results go back as role="tool"
    messages keyed by tool_call_id rather than role="user" tool_result blocks.

    tool_call_mode='strict' skips every fallback below (content-text
    parsing, repeat-loop breaker, unexecuted-final-text rewrite) and only
    trusts the structured tool_calls field — same rule _run_with_claude
    already follows, for a model that reliably emits it.
    """
    lenient = tool_call_mode != "strict"
    tool_defs = [
        {
            "type": "function",
            "function": {"name": name, "description": SIMULATED_TOOLS[name], "parameters": TOOL_SCHEMAS[name]},
        }
        for name in tool_names
    ]
    if allow_ask_user:
        tool_defs.append({"type": "function", "function": {"name": ASK_USER_TOOL_NAME, "description": ASK_USER_DESCRIPTION, "parameters": ASK_USER_SCHEMA}})

    messages: list[dict[str, Any]] = history or [
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
    asked_question: str | None = None
    pending_command: dict[str, Any] | None = None
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
            round_text = message.get("content") or ""

            if message.get("content"):
                reasoning_parts.append(message["content"])
                final_text = message["content"]

            calls = message.get("tool_calls") or []
            if lenient and not calls and message.get("content"):
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
                if on_step:
                    await on_step(round_text, [])
                break

            if lenient:
                # Small local models frequently fail to recognize a tool
                # result as an answer and re-issue the exact same call
                # forever instead of stopping (observed with
                # Qwen2.5-Coder-7B) — without this, that burns every
                # iteration on identical repeated no-op calls.
                signatures = {call["function"]["name"] + "|" + (call["function"].get("arguments") or "") for call in calls}
                if signatures <= seen_call_signatures:
                    stopped_cleanly = True
                    if on_step:
                        await on_step(round_text, [])
                    break
                seen_call_signatures |= signatures

            messages.append(message)

            ask_call = next((c for c in calls if c["function"]["name"] == ASK_USER_TOOL_NAME), None)
            if ask_call:
                try:
                    asked_question = json.loads(ask_call["function"].get("arguments") or "{}").get("question", "")
                except json.JSONDecodeError:
                    asked_question = ""
                if on_step:
                    await on_step(round_text, [])
                break

            # Same all-or-nothing-per-round rule as Claude: a round
            # proposing any mutating call must pause entirely before
            # running anything in it, since every call in this message
            # needs an answer before the next one.
            if not pre_approved:
                gate_call = next((c for c in calls if c["function"]["name"] in MUTATING_TOOLS), None)
                if gate_call and (user_role in MUTATION_ROLE_BAR):
                    try:
                        gate_args = json.loads(gate_call["function"].get("arguments") or "{}")
                    except json.JSONDecodeError:
                        gate_args = {}
                    pending_command = {"tool": gate_call["function"]["name"], "input": gate_args}
                    if on_step:
                        await on_step(round_text, [])
                    break

            round_tool_calls: list[dict[str, Any]] = []
            for call in calls:
                name = call["function"]["name"]
                try:
                    call_args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    call_args = {}

                if name in MUTATING_TOOLS and not pre_approved and user_role not in MUTATION_ROLE_BAR:
                    result_payload = {"ok": False, "error": f"tool '{name}' requires dev role or higher (you are '{user_role or 'viewer'}')"}
                else:
                    try:
                        tool = get_tool(name)
                        result = await tool.run(**call_args)
                        result_payload = result.model_dump()
                    except KeyError:
                        result_payload = {"ok": False, "error": f"tool '{name}' not registered"}

                entry = {"tool": name, "input": call_args, "result": result_payload}
                tool_calls.append(entry)
                round_tool_calls.append(entry)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": str(result_payload)})
            if on_step:
                await on_step(round_text, round_tool_calls)

    if lenient:
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
    elif not tool_calls and _extract_content_tool_calls(final_text):
        # Strict mode's whole point is to ignore a call the model only wrote
        # as text — but silently returning that text as if it were a real
        # answer looks like "the tool ran and this is empty" rather than
        # "nothing ran." Flag it plainly instead (observed live: local
        # models essentially always write calls this way, never the real
        # structured field, so this fires often under strict mode).
        final_text += "\n\n⚠️ Model tried to call a tool as plain text, not a real structured tool call — strict mode ignored it. Nothing was executed. Switch to lenient mode, or use Claude, if you want this to actually run."

    return {"summary": final_text}, "\n".join(reasoning_parts), tool_calls, asked_question, pending_command, messages
