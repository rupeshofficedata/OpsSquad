from pydantic_settings import BaseSettings

# The string this repo used to hard-default jwt_secret to — now blocklisted
# explicitly (not just "must be non-empty") since it's public in git
# history and .env.example/k8s manifests could still be copied with it
# unedited.
INSECURE_DEFAULT_JWT_SECRET = "change-me-to-a-long-random-string"


class Settings(BaseSettings):
    database_url: str = "postgresql://opssquad:change-me@localhost:5432/opssquad"
    redis_url: str = "redis://localhost:6379"
    # No usable default on purpose — a real value arrives either from this
    # env var or, in k8s, from Vault (see app/vault.py, loaded into this
    # field at startup before it's ever read). main.py's lifespan refuses
    # to start if it's still empty/the old placeholder after that.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    anthropic_api_key: str = ""
    intent_router_model: str = "claude-haiku-4-5-20251001"
    agent_model: str = "claude-sonnet-5"
    mutating_agents_enabled: bool = True
    github_webhook_secret: str = ""
    alertmanager_webhook_token: str = ""
    # OpenAI-compatible endpoint for a self-hosted model, e.g. `llama-server
    # --port 8080` speaks this API natively. Used only when a user's
    # model_provider is 'local'.
    local_llm_base_url: str = "http://localhost:8080/v1"
    # Read timeout per completion (a long-thinking model needs more than 120s)
    # and the chars of message history sent per request before old tool output
    # is trimmed (keeps the prompt inside the server's context window).
    local_llm_read_timeout: float = 120.0
    local_llm_max_history_chars: int = 10000
    # A run paused on approval / ask_user is auto-aborted after this long.
    paused_run_ttl_minutes: int = 30

    # Real tool integrations (kubectl/terraform/trivy) — see app/tools/real.py
    # for exactly what each targets and why. False keeps every tool
    # simulated, the safe default the rest of this scaffold was built and
    # tested against.
    real_tools_enabled: bool = False
    kube_namespace: str = "opssquad"
    kube_read_target: str = "deployment/runtime"
    kube_mutate_target: str = "deployment/redis"
    terraform_dir: str = "terraform-demo"
    trivy_scan_path: str = "/app"

    # cloud.cost_explorer/slack.post/pagerduty.read — empty by default (no
    # usable safe self-contained target exists for these, unlike kubectl/
    # terraform/trivy/docker/helm/argocd above); each tool fails closed with
    # a clear error when unconfigured, same shape as ALERTMANAGER_WEBHOOK_TOKEN.
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    slack_webhook_url: str = ""
    pagerduty_api_token: str = ""

    # Set only in k8s — empty here means "no Vault, use plain env vars"
    # (the docker-compose path, untouched).
    vault_addr: str = ""
    vault_role: str = "opssquad-runtime"

    # Test-namespace switches (k8s/test/). Defaults keep production behavior.
    # approval_mode='auto' skips the per-command approval *pause* (the
    # dev/admin role check still applies); check_approval_mode() refuses it
    # anywhere but the opssquad-test namespace.
    environment: str = "prod"
    approval_mode: str = "manual"  # manual | auto
    # Base URL / target overrides so the tools can point at sandbox services.
    pagerduty_api_url: str = "https://api.pagerduty.com"
    argocd_app: str = "argocd-demo"
    argocd_namespace: str = "argocd"

    class Config:
        env_file = ".env"
        extra = "ignore"



TEST_NAMESPACE = "opssquad-test"


def check_approval_mode(mode: str, environment: str, namespace: str) -> None:
    """approval_mode='auto' lets the model run mutating commands with no human
    pause, so it is only ever allowed inside the disposable test namespace."""
    if mode not in ("manual", "auto"):
        raise RuntimeError(f"APPROVAL_MODE must be 'manual' or 'auto', got {mode!r}")
    if mode == "auto" and not (environment == "test" and namespace == TEST_NAMESPACE):
        raise RuntimeError(
            f"APPROVAL_MODE=auto is only allowed with ENVIRONMENT=test in the {TEST_NAMESPACE!r} "
            f"namespace (got ENVIRONMENT={environment!r}, KUBE_NAMESPACE={namespace!r})"
        )


settings = Settings()
