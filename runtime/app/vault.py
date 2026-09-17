"""Fetches secrets from Vault using the pod's own Kubernetes ServiceAccount
token — no static Vault credential is ever stored in this app's config.
No-op if VAULT_ADDR isn't set (plain docker-compose keeps using env vars,
untouched) or if not actually running in-cluster.
"""

import asyncio
from pathlib import Path

import httpx

from app.config import settings

SA_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")

# Dev-mode Vault (k8s/06-vault.yaml) is in-memory, so any Vault pod restart
# wipes its auth config until k8s/07-vault-init-job.yaml's CronJob rewrites
# it on its 2-minute cycle. A pod that boots inside that window must ride
# out transient 403s from the login call instead of crashing — 40 attempts
# * 5s covers the 2-minute cycle with margin.
MAX_LOGIN_ATTEMPTS = 40
LOGIN_RETRY_SECONDS = 5
_client_kwargs: dict = {}


async def _login(client: httpx.AsyncClient, sa_jwt: str) -> str:
    for attempt in range(MAX_LOGIN_ATTEMPTS):
        try:
            login = await client.post(
                "/v1/auth/kubernetes/login", json={"role": settings.vault_role, "jwt": sa_jwt}
            )
            login.raise_for_status()
            return login.json()["auth"]["client_token"]
        except httpx.HTTPStatusError:
            if attempt == MAX_LOGIN_ATTEMPTS - 1:
                raise
            await asyncio.sleep(LOGIN_RETRY_SECONDS)
    raise AssertionError("unreachable")  # pragma: no cover


async def load_secrets_from_vault() -> None:
    if not settings.vault_addr or not SA_TOKEN_PATH.exists():
        return

    sa_jwt = SA_TOKEN_PATH.read_text().strip()

    async with httpx.AsyncClient(base_url=settings.vault_addr, timeout=10, **_client_kwargs) as client:
        vault_token = await _login(client, sa_jwt)
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

            # Optional — empty in Vault by default. cloud.cost_explorer/
            # slack.post/pagerduty.read (app/tools/real.py) each fail
            # closed with a clear error if their own credential is unset.
            integrations = (await client.get("/v1/secret/data/opssquad/integrations", headers=headers)).json()["data"]["data"]
            settings.aws_access_key_id = integrations.get("aws_access_key_id", settings.aws_access_key_id)
            settings.aws_secret_access_key = integrations.get("aws_secret_access_key", settings.aws_secret_access_key)
            settings.aws_region = integrations.get("aws_region") or settings.aws_region
            settings.slack_webhook_url = integrations.get("slack_webhook_url", settings.slack_webhook_url)
            settings.pagerduty_api_token = integrations.get("pagerduty_api_token", settings.pagerduty_api_token)
