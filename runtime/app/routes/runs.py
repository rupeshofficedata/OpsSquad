from fastapi import APIRouter, Depends, HTTPException

from app import repo
from app.security import User, require_role

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post("/{run_id}/abort")
async def abort(run_id: str, user: User = Depends(require_role("dev"))):
    run = await repo.get_run(run_id)
    if run is None:
        raise HTTPException(404, "Run not found")

    aborted = await repo.abort_run(run_id)
    if not aborted:
        raise HTTPException(409, f"Run is '{run['status']}' and can no longer be aborted")

    await repo.write_audit_log(user.id, "run.abort", run_id, {})
    return {"status": "aborted", "run_id": run_id}
