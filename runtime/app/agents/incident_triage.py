from app.agents.base import AgentContext, AgentResult, SimulatedAgent


class IncidentTriageAgent(SimulatedAgent):
    slug = "incident-triage"

    async def run(self, ctx: AgentContext) -> AgentResult:
        alert = await ctx.call("alertmanager.read", **ctx.params)
        await ctx.call("pagerduty.read", **ctx.params)

        # Flat top-level `severity` is intentional: Flightplan `when` clauses
        # reference it directly as ${steps.triage.severity}.
        return AgentResult(
            status="success",
            output={
                "severity": alert.get("severity"),
                "alertname": alert.get("alertname"),
                "summary": alert.get("summary"),
            },
            reasoning=f"Triaged '{alert.get('alertname')}' as {alert.get('severity')}.",
            tool_calls=ctx.tool_calls,
        )
