from contextlib import asynccontextmanager

from fastapi import FastAPI

import asyncio

from app.config import INSECURE_DEFAULT_JWT_SECRET, check_approval_mode, settings
from app.db import close_pool, init_pool
from app.repo import fail_orphaned_runs
from app.routes import admin, agents, chat, flightplans, runs, webhooks
from app.scheduler import run_scheduler
from app.vault import load_secrets_from_vault


@asynccontextmanager
async def lifespan(app: FastAPI):
    await load_secrets_from_vault()  # no-op if VAULT_ADDR isn't set
    if not settings.jwt_secret or settings.jwt_secret == INSECURE_DEFAULT_JWT_SECRET:
        raise RuntimeError(
            "JWT_SECRET is not configured (or still the old public placeholder). "
            "Set it via .env or Vault — refusing to start with an unsafe default."
        )
    check_approval_mode(settings.approval_mode, settings.environment, settings.kube_namespace)
    await init_pool()
    await fail_orphaned_runs()
    scheduler_task = asyncio.create_task(run_scheduler())
    yield
    scheduler_task.cancel()
    await close_pool()


app = FastAPI(title="OpsSquad Agent Runtime", lifespan=lifespan)

app.include_router(chat.router)
app.include_router(agents.router)
app.include_router(flightplans.router)
app.include_router(runs.router)
app.include_router(admin.router)
app.include_router(webhooks.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "opssquad-runtime"}
