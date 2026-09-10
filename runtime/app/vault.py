"""Fetches secrets from Vault using the pod's own Kubernetes ServiceAccount
token — no static Vault credential is ever stored in this app's config.
No-op if VAULT_ADDR isn't set (plain docker-compose keeps using env vars,
untouched) or if not actually running in-cluster.
"""

from pathlib import Path

import httpx

from app.config import settings

SA_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")


async def load_secrets_from_vault() -> None:
    if not settings.vault_addr or not SA_TOKEN_PATH.exists():
        return

    sa_jwt = SA_TOKEN_PATH.read_text().strip()

    async with httpx.AsyncClient(base_url=settings.vault_addr, timeout=10) as client:
        login = await client.post(
            "/v1/auth/kubernetes/login", json={"role": settings.vault_role, "jwt": sa_jwt}
        )
        login.raise_for_status()
        vault_token = login.json()["auth"]["client_token"]
        headers = {"X-Vault-Token": vault_token}

        jwt_data = (await client.get("/v1/secret/data/opssquad/jwt", headers=headers)).json()["data"]["data"]
        settings.jwt_secret = jwt_data.get("secret", settings.jwt_secret)

        # Only the runtime role's policy can read these two paths — a bff
        # pod's token would get a 403 here, which is the point (least
        # privilege enforced by Vault itself, not just by this code).
        if settings.vault_role == "opssquad-runtime":
            llm = (await client.get("/v1/secret/data/opssquad/llm", headers=headers)).json()["data"]["data"]
            settings.anthropic_api_key = llm.get("anthropic_api_key", settings.anthropic_api_key)

            webhooks = (await client.get("/v1/secret/data/opssquad/webhooks", headers=headers)).json()["data"]["data"]
            settings.github_webhook_secret = webhooks.get("github_secret", settings.github_webhook_secret)
            settings.alertmanager_webhook_token = webhooks.get("alertmanager_token", settings.alertmanager_webhook_token)
