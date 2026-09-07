from app.agents.base import AgentContext, AgentResult, SimulatedAgent


class DiagnoseAgent(SimulatedAgent):
    slug = "diagnose"

    async def run(self, ctx: AgentContext) -> AgentResult:
        logs = await ctx.call("kubectl.logs", **ctx.params)
        status = await ctx.call("kubectl.get", **ctx.params)
        await ctx.call("prometheus.query", **ctx.params)
        await ctx.call("git.log", **ctx.params)

        desired = status.get("replicas_desired", 0)
        ready = status.get("replicas_ready", 0)
        error_count = logs.get("error_count", 0)

        # A simple, explainable rule the downstream `remediate` step can act
        # on — not a real diagnosis engine, but it makes the recommendation
        # traceable to the symptoms that produced it.
        suggested_replicas = None
        if ready < desired:
            action, reason = "restart_pod", f"only {ready}/{desired} replicas ready"
        elif error_count > 8:
            action = "scale_hpa"
            suggested_replicas = desired * 3  # deliberately aggressive, to exercise remediate's max_replicas guardrail
            reason = f"{error_count} errors in recent logs suggest overload"
        else:
            action, reason = "restart_pod", "no clear resource pressure found; restart as a safe default"

        return AgentResult(
            status="success",
            output={
                "replicas_ready": ready,
                "replicas_desired": desired,
                "error_count": error_count,
                "recommended_action": action,
                "suggested_replicas": suggested_replicas,
            },
            reasoning=f"Recommending '{action}': {reason}.",
            tool_calls=ctx.tool_calls,
        )
