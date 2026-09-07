import hashlib
import hmac

import requests
from flask import Blueprint, current_app, jsonify, request

bp = Blueprint("webhooks", __name__, url_prefix="/webhooks")


def _verify_github_signature(secret: str, payload: bytes, signature: str | None) -> bool:
    if not secret:
        return True  # no secret configured — dev mode only
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@bp.post("/github")
def github_webhook():
    secret = current_app.config["GITHUB_WEBHOOK_SECRET"]
    signature = request.headers.get("X-Hub-Signature-256")
    if not _verify_github_signature(secret, request.get_data(), signature):
        return jsonify(error="invalid signature"), 401

    event = request.headers.get("X-GitHub-Event", "unknown")
    payload = request.get_json(silent=True) or {}
    # A real implementation would map push/PR events to a `ship-to-prod` run.
    return jsonify(received=True, event=event, repo=payload.get("repository", {}).get("full_name")), 202


@bp.post("/alertmanager")
def alertmanager_webhook():
    payload = request.get_json(silent=True) or {}
    system_jwt = current_app.config["SYSTEM_JWT"]

    if not system_jwt:
        return jsonify(
            received=True,
            triggered=False,
            note="SYSTEM_JWT not configured — set it to auto-trigger the incident-response flightplan",
        ), 202

    resp = requests.post(
        f"{current_app.config['RUNTIME_URL']}/flightplans/incident-response/execute",
        json={"inputs": {"alert": payload}},
        headers={"Authorization": f"Bearer {system_jwt}"},
        timeout=10,
    )
    return jsonify(received=True, triggered=resp.ok, runtime_status=resp.status_code), 202
