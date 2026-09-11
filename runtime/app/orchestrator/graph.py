"""Executes a Flightplan's step graph.

The step graph is stored as JSON (converted from the YAML definitions in
flightplans/*.yaml). Steps run in dependency order (`needs`); a step with
`type: approval` pauses the run until an admin approves it via
POST /api/runs/:id/approve on the BFF, which calls resume_flightplan below.
`when`/`policy` expressions run through app.orchestrator.safe_eval — a
real restricted evaluator (AST allowlist, attribute access is dict-key
lookup, never getattr()), not eval(). See that module's docstring for why.
"""

import logging
import re
from typing import Any, Coroutine

from app import repo
from app.config import settings
from app.orchestrator.executor import run_agent
from app.orchestrator.safe_eval import evaluate as safe_evaluate
from app.security import role_at_least

logger = logging.getLogger(__name__)

TEMPLATE_RE = re.compile(r"\$\{([^}]+)\}")


async def run_in_background(run_id: str, coro: Coroutine[Any, Any, str]) -> None:
    """Runs a Flightplan execution/resume coroutine as a background task
    (see routes/flightplans.py and routes/webhooks.py) instead of blocking
    the HTTP request until every step finishes. Nothing else awaits this
    task, so an unexpected exception here would otherwise leave the run
    stuck 'running' forever — caught and recorded as 'failed' instead."""
    try:
        await coro
    except Exception:
        logger.exception("flightplan run %s crashed", run_id)
        await repo.finish_run(run_id, "failed")


def _resolve_templates(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str):
        match = TEMPLATE_RE.fullmatch(value.strip())
        if match:
            return _eval_expr(match.group(1), context)
        return TEMPLATE_RE.sub(lambda m: str(_eval_expr(m.group(1), context)), value)
    if isinstance(value, dict):
        return {k: _resolve_templates(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_templates(v, context) for v in value]
    return value


def _eval_expr(expr: str, context: dict[str, Any]) -> Any:
    try:
        return safe_evaluate(expr, context)
    except Exception:
        return None


def _topo_order(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {s["id"]: s for s in steps}
    visited: set[str] = set()
    ordered: list[dict[str, Any]] = []

    def visit(step_id: str) -> None:
        if step_id in visited:
            return
        visited.add(step_id)
        step = by_id[step_id]
        for dep in step.get("needs", []):
            visit(dep)
        ordered.append(step)

    for s in steps:
        visit(s["id"])
    return ordered


async def _rebuild_context(run_id: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Reconstructs step outputs from persisted run_steps so a resumed run
    (after an approval gate) can still resolve ${steps.x.*} templates."""
    context: dict[str, Any] = {"inputs": inputs, "steps": {}}
    for row in await repo.list_run_steps(run_id):
        if not row["step_id"]:
            continue
        context["steps"][row["step_id"]] = {"status": row["status"], **(row["output"] or {})}
    return context


async def execute_flightplan(
    run_id: str, flightplan: dict[str, Any], inputs: dict[str, Any], user_role: str,
    model_provider: str = "anthropic", model_name: str | None = None,
) -> str:
    """Runs steps from the beginning. Returns 'success' | 'failed' | 'awaiting_approval'."""
    context: dict[str, Any] = {"inputs": inputs, "steps": {}}
    return await _run_steps(
        run_id, flightplan, context, start_at=0,
        user_role=user_role, model_provider=model_provider, model_name=model_name,
    )


async def resume_flightplan(
    run_id: str, flightplan: dict[str, Any], inputs: dict[str, Any], user_role: str,
    model_provider: str = "anthropic", model_name: str | None = None,
) -> str:
    """Resumes a run that's sitting at 'awaiting_approval', continuing past
    the approval step it paused on."""
    context = await _rebuild_context(run_id, inputs)
    resume_from = await repo.count_run_steps(run_id) + 1  # +1 skips the approval step itself
    return await _run_steps(
        run_id, flightplan, context, start_at=resume_from,
        user_role=user_role, model_provider=model_provider, model_name=model_name,
    )


async def _run_steps(
    run_id: str, flightplan: dict[str, Any], context: dict[str, Any], start_at: int, user_role: str,
    model_provider: str = "anthropic", model_name: str | None = None,
) -> str:
    await repo.mark_run_running(run_id)

    steps = _topo_order(flightplan["definition"]["steps"])
    # Derived from `context` (not a fresh False) so a resume after an
    # approval gate still remembers a failure from *before* the pause —
    # execute_flightplan starts with an empty context so this is False
    # there; resume_flightplan's rebuilt context carries prior statuses.
    any_step_failed = any(s.get("status") == "failed" for s in context["steps"].values())

    for order, step in enumerate(steps):
        if order < start_at:
            continue

        # Cooperative abort: checked between steps, not preemptively — a
        # step already running (an agent's tool call) finishes first. See
        # repo.abort_run. finish_run isn't called here since abort_run
        # already set status + finished_at.
        run = await repo.get_run(run_id)
        if run is not None and run["status"] == "aborted":
            return "aborted"

        if step.get("when") is not None:
            should_run = _eval_expr(step["when"].strip("${}"), context)
            if not should_run:
                context["steps"][step["id"]] = {"status": "skipped"}
                await repo.add_run_step(
                    run_id, order, step.get("agent", step["id"]), "skipped", step_id=step["id"]
                )
                continue

        if step.get("type") == "approval":
            await repo.finish_run(run_id, "awaiting_approval")
            return "awaiting_approval"

        agent_slug = step["agent"]
        agent = await repo.get_agent(agent_slug)
        if agent is None:
            await repo.add_run_step(run_id, order, agent_slug, "failed", step_id=step["id"], reasoning="agent not found")
            await repo.finish_run(run_id, "failed")
            return "failed"

        if not role_at_least(user_role, agent["min_role"]):
            await repo.add_run_step(
                run_id, order, agent_slug, "failed", step_id=step["id"],
                reasoning=f"triggering user's role '{user_role}' cannot run agent '{agent_slug}' (needs >= '{agent['min_role']}')",
            )
            await repo.finish_run(run_id, "failed")
            return "failed"

        if agent["is_mutating"] and not settings.mutating_agents_enabled:
            await repo.add_run_step(
                run_id, order, agent_slug, "failed", step_id=step["id"],
                reasoning="kill switch: mutating agents disabled",
            )
            await repo.finish_run(run_id, "failed")
            return "failed"

        # `with` supplies the agent's normal params; `policy` and
        # `guardrails` are sibling step keys (not user input) that
        # security_scan.py and remediate.py read to decide pass/fail —
        # merged in here so both are visible on ctx.params.
        params = _resolve_templates(step.get("with", {}), context)
        if "policy" in step:
            params["policy"] = _resolve_templates(step["policy"], context)
        if "guardrails" in step:
            params["guardrails"] = _resolve_templates(step["guardrails"], context)

        result = await run_agent(agent, params, model_provider=model_provider, model_name=model_name)
        step_status = result["status"]

        await repo.add_run_step(
            run_id, order, agent_slug, step_status, step_id=step["id"],
            input_data=params, output_data=result["output"],
            reasoning=result["reasoning"], tool_calls=result["tool_calls"],
            duration_ms=result["duration_ms"],
        )
        context["steps"][step["id"]] = {"status": step_status, **result["output"]}

        if step_status == "failed":
            any_step_failed = True

        # A `policy.block_on` breach means "stop the pipeline" by definition,
        # even on a step that didn't separately declare fail_fast.
        stops_pipeline = step.get("fail_fast") or step.get("policy", {}).get("block_on")
        if step_status == "failed" and stops_pipeline:
            await repo.finish_run(run_id, "failed")
            return "failed"

    # A step can fail without stopping the pipeline (no fail_fast/block_on),
    # e.g. `verify` failing but `rollback` handling it — that's still a
    # failed run overall, not a clean success, even though nothing aborted.
    final_status = "failed" if any_step_failed else "success"
    await repo.finish_run(run_id, final_status)
    return final_status
