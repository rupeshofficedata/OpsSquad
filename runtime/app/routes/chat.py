import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app import repo
from app.models import ChatReplyRequest, ChatRequest
from app.orchestrator.executor import build_resume_messages, execute_paused_round, run_agent, summarize_thread
from app.orchestrator.graph import run_in_background
from app.security import User, current_user, role_at_least

router = APIRouter(prefix="/chat", tags=["chat"])

# Once a thread passes this many runs, everything older than the most
# recent THREAD_SUMMARY_KEEP_RUNS gets folded into runs.thread_summary
# instead of replayed verbatim on every new message — see
# build_thread_history below.
THREAD_SUMMARY_THRESHOLD = 10
THREAD_SUMMARY_KEEP_RUNS = 10


async def build_thread_history(
    thread_root_id: str, new_prompt: str, agent: dict, model_provider: str, model_name: str | None,
) -> list[dict]:
    """Turns a persisted thread's chat_messages (plain role/content rows,
    one per run — see db/migrations/001_init.sql) into the seed `messages`
    list run_agent() expects for a *fresh* run, so a new /chat call can
    carry over everything a prior run in the same thread said. Unlike
    build_resume_messages (mid-run ask_user resume), there's no raw
    tool_use/tool_result state to replay across runs — chat_messages never
    captured that, only the human-readable turns — so this always produces
    plain user/assistant text turns, never tool-call blocks.

    Once the thread has grown past THREAD_SUMMARY_THRESHOLD runs, every run
    older than the most recent THREAD_SUMMARY_KEEP_RUNS is compressed into
    runs.thread_summary (recomputed each time the threshold is crossed
    again, not incrementally) instead of replayed verbatim — keeps context
    size bounded without a background job or an extra tracking column."""
    turns = await repo.list_thread_messages(thread_root_id)
    run_order = list(dict.fromkeys(t["run_id"] for t in turns))
    summary = None
    if len(run_order) > THREAD_SUMMARY_THRESHOLD:
        cutoff = set(run_order[:-THREAD_SUMMARY_KEEP_RUNS])
        old_turns = [t for t in turns if t["run_id"] in cutoff]
        turns = [t for t in turns if t["run_id"] not in cutoff]
        summary = await summarize_thread(
            [{"role": t["role"], "content": t["content"]} for t in old_turns], model_provider, model_name,
        )
        if summary:
            await repo.set_thread_summary(thread_root_id, summary)

    return assemble_thread_messages(model_provider, agent["system_prompt"], summary, turns, new_prompt)


def assemble_thread_messages(
    model_provider: str, agent_system_prompt: str, summary: str | None, turns: list[dict], new_prompt: str,
) -> list[dict]:
    """The wire-format part of build_thread_history above, split out
    because it's the one piece with actual format-correctness risk (same
    reasoning as build_resume_messages in executor.py) — pure and
    DB/network-free, so it's covered by a plain unit test instead of a live
    DB fixture. `turns`: [{"role": "user"|"agent", "content": str}, ...]
    already trimmed to whatever should be replayed verbatim."""
    history: list[dict] = []
    if model_provider == "local":
        history.append({"role": "system", "content": agent_system_prompt})
    if summary:
        history.append({"role": "assistant", "content": summary})
    for t in turns:
        history.append({"role": "assistant" if t["role"] == "agent" else "user", "content": t["content"]})
    history.append({"role": "user", "content": new_prompt})
    return history

# The one agent chat actually uses — full tool catalog, no per-prompt
# routing. See docs/superpowers/plans/generalist-chat-agent-and-routing-removal.md
# for why: keyword routing to one of the 15 specialists silently
# misrouted any prompt with no keyword overlap to agents[0], with no
# error, and RBAC-checked the wrong agent's min_role in the process.
CHAT_AGENT_SLUG = "assistant"


async def _execute_chat(
    run_id: str, agent: dict, params: dict, user_id: str,
    model_provider: str, model_name: str | None, tool_call_mode: str,
    user_role: str | None = None, history: list | None = None,
) -> str:
    """Runs the agent (fresh, or resumed from a prior ask_user/mutating-
    command pause) as a background task — see POST /chat, POST /chat/
    {run_id}/reply, and POST /chat/{run_id}/{approve,deny}-command, all of
    which return immediately with {"status": "queued", "run_id": ...} and
    let the caller watch progress over the run-stream WS (same one
    RunDetail.jsx already uses for Flightplan runs). Each tool-use round is
    persisted as its own run_steps row (via on_step) as it happens, not just
    the final aggregated result, so that stream actually has something to
    show live: user input -> model reasoning -> tool call -> result, per
    round, not one blob at the end."""
    await repo.mark_run_running(run_id)
    step_order = await repo.count_run_steps(run_id)

    async def on_step(reasoning: str, tool_calls: list[dict]) -> None:
        nonlocal step_order
        await repo.add_run_step(
            run_id, step_order, agent["slug"], "running",
            reasoning=reasoning or None, tool_calls=tool_calls or None,
        )
        step_order += 1

    result = await run_agent(
        agent, params, model_provider=model_provider, model_name=model_name,
        tool_call_mode=tool_call_mode, allow_ask_user=True, history=history, on_step=on_step,
        user_role=user_role, pre_approved=False,
    )

    output = result["output"]
    if result["asked_question"]:
        output = {**output, "question": result["asked_question"]}
    if result["pending_command"]:
        output = {**output, "pending_command": result["pending_command"]}
    if result["simulated"]:
        # No model actually ran (no Anthropic key, no reachable local
        # server — see run_agent's `simulated` flag). output["summary"] is
        # a canned "Simulated run of ... completed." string, not a real
        # answer — the frontend must show that plainly instead of
        # rendering it like a genuine response.
        output = {**output, "simulated": True}
    # reasoning/tool_calls are deliberately omitted here — every round's
    # already been persisted live via on_step above; repeating the full
    # joined reasoning + every tool call again in this final marker row
    # would just duplicate the whole loop a second time in the UI. This row
    # exists only to carry the final output/status/duration.
    await repo.add_run_step(
        run_id, step_order, agent["slug"], result["status"],
        input_data=params, output_data=output,
        duration_ms=result["duration_ms"],
    )

    pending_state = {
        "messages": result["messages"], "agent_slug": agent["slug"],
        "model_provider": model_provider, "model_name": model_name, "tool_call_mode": tool_call_mode,
    }
    if result["status"] == "awaiting_user_input":
        await repo.set_run_pending_state(run_id, pending_state)
        await repo.mark_run_awaiting_user_input(run_id, result["asked_question"])
    elif result["status"] == "awaiting_command_approval":
        await repo.set_run_pending_state(run_id, pending_state)
        await repo.mark_run_awaiting_command_approval(run_id, result["pending_command"])
    else:
        await repo.finish_run(run_id, result["status"], output)
        # Feeds a future /chat call in this thread (build_thread_history)
        # — never for a simulated run: that's a canned string, not
        # something the model actually said, and persisting it would
        # corrupt every later turn's context with fabricated history.
        answer = output.get("summary") if isinstance(output, dict) else None
        if answer and not result["simulated"]:
            await repo.add_chat_message(run_id, "agent", answer)
    await repo.write_audit_log(user_id, "chat.run", agent["slug"], {"run_id": run_id})
    return result["status"]


@router.post("")
async def chat(req: ChatRequest, user: User = Depends(current_user)):
    agent = await repo.get_agent(CHAT_AGENT_SLUG)
    if agent is None:
        raise HTTPException(503, f"Chat agent '{CHAT_AGENT_SLUG}' not registered — run db/seed.sql")

    # RBAC — re-checked here, never trusted from the BFF alone. Always
    # passes today (min_role='viewer' on the chat agent) — kept for
    # consistency/defense-in-depth rather than special-cased away.
    if not role_at_least(user.role, agent["min_role"]):
        raise HTTPException(403, f"role '{user.role}' cannot run '{agent['slug']}' (needs >= '{agent['min_role']}')")

    thread_root_id = None
    if req.thread_id:
        run = await repo.get_thread_root(req.thread_id)
        # 404, not 403, for the not-owner case too — same reasoning as
        # /reply below: don't leak whether a thread_id exists to a user
        # who doesn't own it.
        if run is None or str(run["triggered_by"]) != user.id:
            raise HTTPException(404, "Thread not found")
        # Resolve to the thread's actual root even if the caller passed a
        # non-root run's own id (e.g. an older Dashboard link, or a client
        # bug) — get_thread_root/list_thread_messages only match rows where
        # id == this id OR thread_id == this id, so a stray non-root id
        # would otherwise silently see just that one run's turns.
        thread_root_id = run["thread_id"] or req.thread_id

    model_provider, model_name, tool_call_mode = await repo.get_user_model_preference(user.id)
    history = (
        await build_thread_history(thread_root_id, req.prompt, agent, model_provider, model_name)
        if thread_root_id else None
    )

    run_id = await repo.create_run(
        kind="chat", agent_id=agent["id"], triggered_by=user.id, prompt=req.prompt, thread_id=thread_root_id,
    )
    await repo.add_chat_message(run_id, "user", req.prompt)
    asyncio.create_task(run_in_background(run_id, _execute_chat(
        run_id, agent, {"prompt": req.prompt}, user.id, model_provider, model_name, tool_call_mode,
        user_role=user.role, history=history,
    )))
    return {"status": "queued", "run_id": run_id, "agent": agent["slug"], "thread_id": thread_root_id or run_id}


@router.post("/{run_id}/reply")
async def reply(run_id: str, req: ChatReplyRequest, user: User = Depends(current_user)):
    """Resumes a run the model paused via ask_user, with the full prior
    tool-use history, until it finishes normally or asks another question."""
    run = await repo.get_run(run_id)
    # 404, not 403, for the not-owner case too — don't leak whether a
    # run_id exists to a user who doesn't own it.
    if run is None or str(run["triggered_by"]) != user.id:
        raise HTTPException(404, "Run not found")
    if run["status"] != "awaiting_user_input":
        raise HTTPException(409, f"Run is '{run['status']}', not awaiting a reply")

    state = run["inputs"] or {}
    agent = await repo.get_agent(state["agent_slug"])
    if agent is None:
        raise HTTPException(404, f"Agent '{state['agent_slug']}' not found")

    await repo.add_chat_message(run_id, "user", req.reply)
    history = build_resume_messages(state["model_provider"], state["messages"], req.reply)
    asyncio.create_task(run_in_background(run_id, _execute_chat(
        run_id, agent, {}, user.id,
        state["model_provider"], state.get("model_name"), state.get("tool_call_mode", "lenient"),
        user_role=user.role, history=history,
    )))
    return {"status": "queued", "run_id": run_id, "agent": agent["slug"]}


@router.get("/threads/{thread_id}")
async def get_thread(thread_id: str, user: User = Depends(current_user)):
    """Lets the frontend hydrate a Chat page from an existing thread — on
    reload (thread_id kept in the URL) or from Dashboard's 'Continue' link.
    Every message returned here was written by add_chat_message with a real
    model's own text (see the `simulated` guard in _execute_chat above) —
    never the no-model fallback's canned string."""
    run = await repo.get_thread_root(thread_id)
    if run is None or str(run["triggered_by"]) != user.id:
        raise HTTPException(404, "Thread not found")
    resolved_id = run["thread_id"] or thread_id
    messages = await repo.list_thread_messages(resolved_id)
    return {"thread_id": resolved_id, "messages": [{"role": m["role"], "content": m["content"]} for m in messages]}


async def _decide_command(run_id: str, user: User, approved: bool) -> dict:
    run = await repo.get_run(run_id)
    if run is None or str(run["triggered_by"]) != user.id:
        raise HTTPException(404, "Run not found")
    if run["status"] != "awaiting_command_approval":
        raise HTTPException(409, f"Run is '{run['status']}', not awaiting a command decision")

    state = run["inputs"] or {}
    agent = await repo.get_agent(state["agent_slug"])
    if agent is None:
        raise HTTPException(404, f"Agent '{state['agent_slug']}' not found")

    # Actually runs the paused round's tool call(s) now (the gated one per
    # `approved`, any others in the same round normally — see
    # execute_paused_round), persists that round as its own step, then hands
    # the resulting history to the normal loop continuation.
    resumed_messages, round_tool_calls = await execute_paused_round(state["model_provider"], state["messages"], approved)
    step_order = await repo.count_run_steps(run_id)
    await repo.add_run_step(run_id, step_order, agent["slug"], "running", tool_calls=round_tool_calls or None)

    asyncio.create_task(run_in_background(run_id, _execute_chat(
        run_id, agent, {}, user.id,
        state["model_provider"], state.get("model_name"), state.get("tool_call_mode", "lenient"),
        user_role=user.role, history=resumed_messages,
    )))
    return {"status": "queued", "run_id": run_id, "agent": agent["slug"]}


@router.post("/{run_id}/approve-command")
async def approve_command(run_id: str, user: User = Depends(current_user)):
    return await _decide_command(run_id, user, approved=True)


@router.post("/{run_id}/deny-command")
async def deny_command(run_id: str, user: User = Depends(current_user)):
    return await _decide_command(run_id, user, approved=False)
