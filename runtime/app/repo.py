from typing import Any

from app.db import get_pool


async def get_agent(slug: str) -> dict[str, Any] | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM agents WHERE slug = $1 AND enabled", slug)
    return dict(row) if row else None


async def list_agents() -> list[dict[str, Any]]:
    pool = get_pool()
    rows = await pool.fetch("SELECT * FROM agents WHERE enabled ORDER BY slug")
    return [dict(r) for r in rows]


async def get_user_role(user_id: str) -> str | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT role FROM users WHERE id = $1", user_id)
    return row["role"] if row else None


async def get_flightplan(slug: str) -> dict[str, Any] | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM flightplans WHERE slug = $1", slug)
    return dict(row) if row else None


async def get_flightplan_by_id(flightplan_id: str) -> dict[str, Any] | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM flightplans WHERE id = $1", flightplan_id)
    return dict(row) if row else None


async def create_run(
    kind: str,
    triggered_by: str,
    agent_id: str | None = None,
    flightplan_id: str | None = None,
    prompt: str | None = None,
    inputs: dict[str, Any] | None = None,
) -> str:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO runs (kind, agent_id, flightplan_id, prompt, inputs, status, triggered_by)
        VALUES ($1, $2, $3, $4, $5, 'running', $6)
        RETURNING id
        """,
        kind, agent_id, flightplan_id, prompt, inputs, triggered_by,
    )
    return str(row["id"])


async def finish_run(run_id: str, status: str, result: dict[str, Any] | None = None) -> None:
    pool = get_pool()
    await pool.execute(
        """
        UPDATE runs SET status = $2, finished_at = NOW(), result = $3
        WHERE id = $1
        """,
        run_id, status, result,
    )


async def add_run_step(
    run_id: str,
    step_order: int,
    agent_slug: str,
    status: str,
    step_id: str | None = None,
    input_data: dict[str, Any] | None = None,
    output_data: dict[str, Any] | None = None,
    reasoning: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    duration_ms: int | None = None,
) -> str:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO run_steps
            (run_id, step_order, step_id, agent_slug, input, output, reasoning, tool_calls, status, duration_ms)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        RETURNING id
        """,
        run_id, step_order, step_id, agent_slug,
        input_data, output_data, reasoning, tool_calls,
        status, duration_ms,
    )
    return str(row["id"])


async def list_run_steps(run_id: str) -> list[dict[str, Any]]:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT * FROM run_steps WHERE run_id = $1 ORDER BY step_order", run_id
    )
    return [dict(r) for r in rows]


async def get_run(run_id: str) -> dict[str, Any] | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM runs WHERE id = $1", run_id)
    return dict(row) if row else None


async def abort_run(run_id: str) -> bool:
    """Aborts a run still sitting in 'queued' or 'awaiting_approval'. Returns
    False if the run has already finished or isn't in an abortable state —
    there is no background worker in this scaffold, so a run actively
    executing inside a request handler can't be interrupted mid-flight."""
    pool = get_pool()
    row = await pool.fetchrow(
        """
        UPDATE runs SET status = 'aborted', finished_at = NOW()
        WHERE id = $1 AND status IN ('queued', 'awaiting_approval')
        RETURNING id
        """,
        run_id,
    )
    return row is not None


async def count_run_steps(run_id: str) -> int:
    pool = get_pool()
    row = await pool.fetchrow("SELECT COUNT(*) AS n FROM run_steps WHERE run_id = $1", run_id)
    return int(row["n"])


async def write_audit_log(user_id: str, action: str, target: str, metadata: dict[str, Any]) -> None:
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO audit_logs (user_id, action, target, metadata)
        VALUES ($1, $2, $3, $4)
        """,
        user_id, action, target, metadata,
    )
