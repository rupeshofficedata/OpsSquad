from fastapi import APIRouter, Depends, HTTPException

from app import repo
from app.models import ChatRequest
from app.orchestrator.executor import run_agent
from app.orchestrator.router import classify
from app.security import User, current_user, role_at_least

router = APIRouter(prefix="/chat", tags=["chat"])


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
    result = await run_agent(agent, match.params)
    await repo.add_run_step(
        run_id, 0, agent["slug"], result["status"],
        input_data=match.params, output_data=result["output"],
        reasoning=result["reasoning"], tool_calls=result["tool_calls"],
        duration_ms=result["duration_ms"],
    )
    await repo.finish_run(run_id, result["status"], result["output"])
    await repo.write_audit_log(user.id, "chat.run", agent["slug"], {"run_id": run_id})

    return {
        "status": result["status"],
        "run_id": run_id,
        "agent": agent["slug"],
        "output": result["output"],
        "reasoning": result["reasoning"],
        "tool_calls": result["tool_calls"],
    }
