import hashlib
import hmac

from fastapi import APIRouter, Header, HTTPException, Request

from app import repo
from app.config import settings
from app.orchestrator.graph import execute_flightplan

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _verify_github_signature(secret: str, payload: bytes, signature: str | None) -> bool:
    if not secret:
        return True  # no secret configured — dev mode only
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/github")
async def github_webhook(request: Request, x_hub_signature_256: str | None = Header(default=None), x_github_event: str = Header(default="unknown")):
    body = await request.body()
    if not _verify_github_signature(settings.github_webhook_secret, body, x_hub_signature_256):
        raise HTTPException(401, "invalid signature")

    payload = await request.json()
    # A real implementation would map push/PR events to a `ship-to-prod` run.
    return {"received": True, "event": x_github_event, "repo": payload.get("repository", {}).get("full_name")}


@router.post("/alertmanager")
async def alertmanager_webhook(request: Request):
    payload = await request.json()

    flightplan = await repo.get_flightplan("incident-response")
    if flightplan is None:
        return {"received": True, "triggered": False, "note": "incident-response flightplan not found"}

    # Alertmanager isn't a human user — no triggered_by, and it runs with
    # admin-equivalent trust (same as the old SYSTEM_JWT it replaces).
    run_id = await repo.create_run(
        kind="flightplan", flightplan_id=flightplan["id"], triggered_by=None, inputs={"alert": payload}
    )
    await repo.write_audit_log(None, "flightplan.execute", "incident-response", {"run_id": run_id, "source": "alertmanager"})
    status = await execute_flightplan(run_id, flightplan, {"alert": payload}, user_role="admin")

    return {"received": True, "triggered": True, "run_id": run_id, "status": status}
