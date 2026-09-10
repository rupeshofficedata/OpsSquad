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

    # Set only in k8s — empty here means "no Vault, use plain env vars"
    # (the docker-compose path, untouched).
    vault_addr: str = ""
    vault_role: str = "opssquad-runtime"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
