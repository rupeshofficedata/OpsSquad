from fastapi import APIRouter, Depends, HTTPException

from app import repo
from app.models import AgentRunRequest
from app.orchestrator.executor import run_agent
from app.security import User, current_user, role_at_least

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
async def list_agents(user: User = Depends(current_user)):
    agents = await repo.list_agents()
    return [a for a in agents if role_at_least(user.role, a["min_role"])]


@router.post("/{slug}/run")
async def run_agent_direct(slug: str, req: AgentRunRequest, user: User = Depends(current_user)):
    agent = await repo.get_agent(slug)
    if agent is None:
        raise HTTPException(404, f"Agent '{slug}' not found")

    if not role_at_least(user.role, agent["min_role"]):
        raise HTTPException(403, f"role '{user.role}' cannot run '{slug}' (needs >= '{agent['min_role']}')")

    if agent["is_mutating"] and req.env == "prod":
        run_id = await repo.create_run(kind="chat", agent_id=agent["id"], triggered_by=user.id)
        await repo.finish_run(run_id, "awaiting_approval")
        await repo.write_audit_log(user.id, "agent.awaiting_approval", slug, {"run_id": run_id})
        return {"status": "awaiting_approval", "run_id": run_id}

    run_id = await repo.create_run(kind="chat", agent_id=agent["id"], triggered_by=user.id)
    model_provider, model_name, tool_call_mode = await repo.get_user_model_preference(user.id)
    result = await run_agent(agent, req.params, model_provider=model_provider, model_name=model_name, tool_call_mode=tool_call_mode)
    await repo.add_run_step(
        run_id, 0, slug, result["status"],
        input_data=req.params, output_data=result["output"],
        reasoning=result["reasoning"], tool_calls=result["tool_calls"],
        duration_ms=result["duration_ms"],
    )
    await repo.finish_run(run_id, result["status"], result["output"])
    await repo.write_audit_log(user.id, "agent.run", slug, {"run_id": run_id})

    return {"run_id": run_id, **result}
