from fastapi import APIRouter, Depends, HTTPException

from app import repo
from app.models import ChatReplyRequest, ChatRequest
from app.orchestrator.executor import build_resume_messages, run_agent
from app.orchestrator.router import classify
from app.security import User, current_user, role_at_least

router = APIRouter(prefix="/chat", tags=["chat"])


async def _run_and_record(
    run_id: str, agent: dict, params: dict, user_id: str,
    model_provider: str, model_name: str | None, tool_call_mode: str,
    history: list | None = None,
) -> dict:
    """Runs the agent (fresh or resumed from a prior ask_user pause),
    records the step + finishes/re-pauses the run. Shared by POST /chat and
    POST /chat/{run_id}/reply so both stay in sync. allow_ask_user=True
    always — both are chat-originated, the only run kind that gate covers
    (see ASK_USER_TOOL_NAME in executor.py)."""
    result = await run_agent(
        agent, params, model_provider=model_provider, model_name=model_name,
        tool_call_mode=tool_call_mode, allow_ask_user=True, history=history,
    )
    step_order = await repo.count_run_steps(run_id)
    await repo.add_run_step(
        run_id, step_order, agent["slug"], result["status"],
        input_data=params, output_data=result["output"],
        reasoning=result["reasoning"], tool_calls=result["tool_calls"],
        duration_ms=result["duration_ms"],
    )
    if result["status"] == "awaiting_user_input":
        await repo.set_run_pending_state(run_id, {
            "messages": result["messages"], "agent_slug": agent["slug"],
            "model_provider": model_provider, "model_name": model_name, "tool_call_mode": tool_call_mode,
        })
        await repo.mark_run_awaiting_user_input(run_id, result["asked_question"])
    else:
        await repo.finish_run(run_id, result["status"], result["output"])
    await repo.write_audit_log(user_id, "chat.run", agent["slug"], {"run_id": run_id})
    return result


def _response(run_id: str, agent_slug: str, result: dict) -> dict:
    return {
        "status": result["status"],
        "run_id": run_id,
        "agent": agent_slug,
        "output": result["output"],
        "reasoning": result["reasoning"],
        "tool_calls": result["tool_calls"],
        "question": result["asked_question"],
    }


@router.post("")
async def chat(req: ChatRequest, user: User = Depends(current_user)):
    agents = await repo.list_agents()
    if not agents:
        raise HTTPException(503, "No agents registered — run db/seed.sql")

    match = await classify(req.prompt, agents)
    agent = await repo.get_agent(match.agent_slug)
    if agent is None:
        raise HTTPException(404, f"Routed agent '{match.agent_slug}' not found")

    # RBAC — re-checked here, never trusted from the BFF alone.
    if not role_at_least(user.role, agent["min_role"]):
        raise HTTPException(403, f"role '{user.role}' cannot run '{agent['slug']}' (needs >= '{agent['min_role']}')")

    # Mutating agents in prod always need an approval record, never run inline.
    if agent["is_mutating"] and req.env == "prod":
        run_id = await repo.create_run(kind="chat", agent_id=agent["id"], triggered_by=user.id, prompt=req.prompt)
        await repo.finish_run(run_id, "awaiting_approval")
        await repo.write_audit_log(user.id, "chat.awaiting_approval", agent["slug"], {"run_id": run_id})
        return {"status": "awaiting_approval", "run_id": run_id, "agent": agent["slug"]}

    run_id = await repo.create_run(kind="chat", agent_id=agent["id"], triggered_by=user.id, prompt=req.prompt)
    await repo.add_chat_message(run_id, "user", req.prompt)
    model_provider, model_name, tool_call_mode = await repo.get_user_model_preference(user.id)
    result = await _run_and_record(run_id, agent, match.params, user.id, model_provider, model_name, tool_call_mode)

    return _response(run_id, agent["slug"], result)


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
    result = await _run_and_record(
        run_id, agent, {}, user.id,
        state["model_provider"], state.get("model_name"), state.get("tool_call_mode", "lenient"),
        history=history,
    )

    return _response(run_id, agent["slug"], result)
