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


async def get_user_model_preference(user_id: str) -> tuple[str, str | None, str]:
    """Returns (provider, model_name, tool_call_mode). Defaults to
    ('anthropic', None, 'lenient') for a webhook-triggered run with no
    human user_id."""
    if user_id is None:
        return "anthropic", None, "lenient"
    pool = get_pool()
    row = await pool.fetchrow("SELECT model_provider, model_name, tool_call_mode FROM users WHERE id = $1", user_id)
    return (row["model_provider"], row["model_name"], row["tool_call_mode"]) if row else ("anthropic", None, "lenient")


async def get_flightplan(slug: str) -> dict[str, Any] | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM flightplans WHERE slug = $1", slug)
    return dict(row) if row else None


async def list_scheduled_flightplans() -> list[dict[str, Any]]:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT * FROM flightplans WHERE definition->'trigger'->>'type' = 'schedule'"
    )
    return [dict(r) for r in rows]


async def get_flightplan_by_id(flightplan_id: str) -> dict[str, Any] | None:
    pool = get_pool()
    row = await pool.fetchrow("SELECT * FROM flightplans WHERE id = $1", flightplan_id)
    return dict(row) if row else None


async def create_run(
    kind: str,
    triggered_by: str | None,
    agent_id: str | None = None,
    flightplan_id: str | None = None,
    prompt: str | None = None,
    inputs: dict[str, Any] | None = None,
) -> str:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO runs (kind, agent_id, flightplan_id, prompt, inputs, triggered_by)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        kind, agent_id, flightplan_id, prompt, inputs, triggered_by,
    )
    return str(row["id"])


async def mark_run_running(run_id: str) -> None:
    """Flips a run from 'queued', 'awaiting_approval', or 'awaiting_user_input'
    (chat resuming from an ask_user pause) to 'running' right as its
    background task actually starts executing steps. No-ops (via the WHERE
    guard) if the run was aborted in the gap between being queued and the
    task getting scheduled."""
    pool = get_pool()
    await pool.execute(
        "UPDATE runs SET status = 'running' WHERE id = $1 AND status IN ('queued', 'awaiting_approval', 'awaiting_user_input')",
        run_id,
    )


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
    """Aborts a run in 'queued', 'running', or 'awaiting_approval'. Returns
    False if the run has already finished. Flightplan execution runs as a
    background task (see orchestrator/graph.py) that checks this status
    between steps — so a 'running' run stops before its *next* step, not
    mid-step (no preemptive kill of an in-flight tool call)."""
    pool = get_pool()
    row = await pool.fetchrow(
        """
        UPDATE runs SET status = 'aborted', finished_at = NOW()
        WHERE id = $1 AND status IN ('queued', 'running', 'awaiting_approval', 'awaiting_user_input')
        RETURNING id
        """,
        run_id,
    )
    return row is not None


async def set_run_pending_state(run_id: str, state: dict[str, Any]) -> None:
    """Stashes the raw provider-format conversation (+ which agent/model it
    belongs to) into the otherwise-unused `runs.inputs` column while a chat
    run is paused at 'awaiting_user_input', so POST /chat/{id}/reply can
    resume the tool-use loop exactly where it left off."""
    pool = get_pool()
    await pool.execute("UPDATE runs SET inputs = $2 WHERE id = $1", run_id, state)


async def mark_run_awaiting_user_input(run_id: str, question: str) -> None:
    pool = get_pool()
    await pool.execute(
        "UPDATE runs SET status = 'awaiting_user_input' WHERE id = $1", run_id,
    )
    await add_chat_message(run_id, "agent", question)


async def add_chat_message(run_id: str, role: str, content: str) -> None:
    pool = get_pool()
    await pool.execute(
        "INSERT INTO chat_messages (run_id, role, content) VALUES ($1, $2, $3)",
        run_id, role, content,
    )


async def list_chat_messages(run_id: str) -> list[dict[str, Any]]:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT * FROM chat_messages WHERE run_id = $1 ORDER BY created_at", run_id,
    )
    return [dict(r) for r in rows]


async def count_run_steps(run_id: str) -> int:
    pool = get_pool()
    row = await pool.fetchrow("SELECT COUNT(*) AS n FROM run_steps WHERE run_id = $1", run_id)
    return int(row["n"])


async def write_audit_log(user_id: str | None, action: str, target: str, metadata: dict[str, Any]) -> None:
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO audit_logs (user_id, action, target, metadata)
        VALUES ($1, $2, $3, $4)
        """,
        user_id, action, target, metadata,
    )


async def list_users() -> list[dict[str, Any]]:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT id, email, full_name, role, is_active, last_login_at, created_at FROM users ORDER BY created_at"
    )
    return [dict(r) for r in rows]


async def create_user(email: str, password_hash: str, full_name: str | None, role: str) -> dict[str, Any]:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO users (email, password_hash, full_name, role)
        VALUES ($1, $2, $3, $4)
        RETURNING id, email, full_name, role, is_active, created_at
        """,
        email, password_hash, full_name, role,
    )
    return dict(row)


async def update_user_role(user_id: str, role: str) -> dict[str, Any] | None:
    pool = get_pool()
    row = await pool.fetchrow(
        "UPDATE users SET role = $2 WHERE id = $1 RETURNING id, email, role", user_id, role
    )
    return dict(row) if row else None


async def list_audit_log(action: str | None, user_id: str | None, limit: int) -> list[dict[str, Any]]:
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT * FROM audit_logs
        WHERE ($1::text IS NULL OR action = $1) AND ($2::uuid IS NULL OR user_id = $2)
        ORDER BY created_at DESC LIMIT $3
        """,
        action, user_id, limit,
    )
    return [dict(r) for r in rows]
