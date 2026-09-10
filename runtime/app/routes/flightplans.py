from fastapi import APIRouter, Depends, HTTPException

from app import repo
from app.models import FlightplanExecuteRequest
from app.orchestrator.graph import execute_flightplan, resume_flightplan
from app.security import User, current_user, require_role, role_at_least

router = APIRouter(prefix="/flightplans", tags=["flightplans"])


@router.post("/{slug}/execute")
async def execute(slug: str, req: FlightplanExecuteRequest, user: User = Depends(current_user)):
    flightplan = await repo.get_flightplan(slug)
    if flightplan is None:
        raise HTTPException(404, f"Flightplan '{slug}' not found")

    # dev+ can trigger any Flightplan, including a prod one — a prod run
    # still stops at its own `type: approval` step (admin-only, enforced in
    # /runs/{id}/approve below) before any mutating step executes. Requiring
    # admin just to *start* a prod Flightplan would make that gate redundant
    # and contradicts the RBAC matrix in the README ("Execute Flightplan —
    # prod: dev ⚠️ needs approval", not "dev ❌").
    if not role_at_least(user.role, "dev"):
        raise HTTPException(403, f"role '{user.role}' cannot execute flightplans (needs >= 'dev')")

    run_id = await repo.create_run(
        kind="flightplan", flightplan_id=flightplan["id"], triggered_by=user.id, inputs=req.inputs
    )
    await repo.write_audit_log(user.id, "flightplan.execute", slug, {"run_id": run_id, "inputs": req.inputs})

    model_provider, model_name = await repo.get_user_model_preference(user.id)
    status = await execute_flightplan(
        run_id, flightplan, req.inputs, user_role=user.role,
        model_provider=model_provider, model_name=model_name,
    )
    return {"status": status, "run_id": run_id}


@router.post("/runs/{run_id}/approve")
async def approve(run_id: str, user: User = Depends(require_role("admin"))):
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    if run["status"] != "awaiting_approval":
        raise HTTPException(409, f"Run is '{run['status']}', not awaiting approval")

    flightplan = await repo.get_flightplan_by_id(run["flightplan_id"])
    if flightplan is None:
        raise HTTPException(404, "Flightplan not found")

    # Steps run under the *original triggering user's* privilege, not the
    # approver's — approval clears the human gate, it doesn't elevate who
    # the rest of the run executes as.
    triggering_role = await repo.get_user_role(run["triggered_by"]) or "viewer"
    model_provider, model_name = await repo.get_user_model_preference(run["triggered_by"])

    await repo.write_audit_log(user.id, "flightplan.approve", flightplan["slug"], {"run_id": run_id})
    status = await resume_flightplan(
        run_id, flightplan, run["inputs"] or {}, user_role=triggering_role,
        model_provider=model_provider, model_name=model_name,
    )
    return {"status": status, "run_id": run_id}
