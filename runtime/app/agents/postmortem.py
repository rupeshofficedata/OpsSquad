from app.agents.base import AgentContext, AgentResult, SimulatedAgent


class PostmortemAgent(SimulatedAgent):
    slug = "postmortem"

    async def run(self, ctx: AgentContext) -> AgentResult:
        await ctx.call("runs.read", **ctx.params)

        severity = ctx.params.get("severity", "unknown")
        action = ctx.params.get("remediation_action", "unknown")
        assign_to = ctx.params.get("assign_to", "on_call")

        markdown = (
            f"# Postmortem\n\n"
            f"**Severity:** {severity}\n\n"
            f"**Remediation taken:** {action}\n\n"
            f"**Assigned to:** {assign_to}\n\n"
            f"## Timeline\nSee the run's step trace for the full triage → diagnose → remediate sequence.\n\n"
            f"## Follow-ups\n- [ ] Confirm root cause with the on-call engineer\n- [ ] File a ticket for any recurring symptom\n"
        )
        return AgentResult(
            status="success",
            output={"markdown": markdown, "severity": severity},
            reasoning=f"Drafted a postmortem for a {severity} incident, assigned to {assign_to}.",
            tool_calls=ctx.tool_calls,
        )
