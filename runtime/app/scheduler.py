"""In-process cron scheduler for Flightplans with `trigger.type: schedule`
(e.g. cost-sweep.yaml's `schedule.cron`). No separate CronJob/container —
a single runtime replica makes an in-process asyncio loop safe, same
reasoning as the job-queue's background tasks (orchestrator/graph.py).

In-memory next-fire tracking only, no durable job store — an honest,
documented limitation (a restart near a fire time just recomputes the
next slot), not a silent gap.
"""

import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter

from app import repo
from app.orchestrator.graph import execute_flightplan, run_in_background

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 60


async def run_scheduler() -> None:
    last_checked: dict[str, datetime] = {}
    while True:
        try:
            await _tick(last_checked)
        except Exception:
            logger.exception("scheduler tick failed")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


async def _tick(last_checked: dict[str, datetime]) -> None:
    # Cron is re-read from the DB every tick (list_scheduled_flightplans
    # below), not cached at startup — an edited schedule takes effect on
    # the next tick, not only after a restart.
    now = datetime.now(timezone.utc)
    for flightplan in await repo.list_scheduled_flightplans():
        cron = flightplan["definition"].get("trigger", {}).get("cron")
        if not cron:
            continue
        slug = flightplan["slug"]
        since = last_checked.get(slug, now)
        next_fire = croniter(cron, since).get_next(datetime)
        last_checked[slug] = now
        if next_fire > now:
            continue

        run_id = await repo.create_run(
            kind="flightplan", flightplan_id=flightplan["id"], triggered_by=None, inputs={}
        )
        await repo.write_audit_log(None, "flightplan.execute", slug, {"run_id": run_id, "source": "scheduler"})
        asyncio.create_task(run_in_background(
            run_id, execute_flightplan(run_id, flightplan, {}, user_role="admin")
        ))
