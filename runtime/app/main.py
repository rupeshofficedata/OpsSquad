from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import close_pool, init_pool
from app.routes import agents, chat, flightplans, runs


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(title="OpsSquad Agent Runtime", lifespan=lifespan)

app.include_router(chat.router)
app.include_router(agents.router)
app.include_router(flightplans.router)
app.include_router(runs.router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "opssquad-runtime"}
