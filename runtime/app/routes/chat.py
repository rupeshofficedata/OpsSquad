import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app import repo
from app.models import ChatReplyRequest, ChatRequest
from app.orchestrator.executor import build_resume_messages, execute_paused_round, run_agent
from app.orchestrator.graph import run_in_background
from app.security import User, current_user, role_at_least

router = APIRouter(prefix="/chat", tags=["chat"])

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

    run_id = await repo.create_run(kind="chat", agent_id=agent["id"], triggered_by=user.id, prompt=req.prompt)
    await repo.add_chat_message(run_id, "user", req.prompt)
    model_provider, model_name, tool_call_mode = await repo.get_user_model_preference(user.id)
    asyncio.create_task(run_in_background(run_id, _execute_chat(
        run_id, agent, {"prompt": req.prompt}, user.id, model_provider, model_name, tool_call_mode, user_role=user.role,
    )))
    return {"status": "queued", "run_id": run_id, "agent": agent["slug"]}


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
