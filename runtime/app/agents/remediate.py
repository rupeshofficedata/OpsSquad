from app.agents.base import AgentContext, AgentResult, SimulatedAgent

DEFAULT_ACTION = "restart_pod"


class RemediateAgent(SimulatedAgent):
    slug = "remediate"

    async def run(self, ctx: AgentContext) -> AgentResult:
        action = ctx.params.get("action", DEFAULT_ACTION)
        # A Flightplan step can declare `guardrails: {...}` — graph.py merges
        # that into params under "guardrails". Direct chat/agent invocation
        # has no such context, so an ungated call is allowed to proceed.
        guardrails = ctx.params.get("guardrails", {})
        allowed = guardrails.get("allowed_actions")

        if allowed is not None and action not in allowed:
            return AgentResult(
                status="failed",
                output={"action": action},
                reasoning=f"Blocked: action '{action}' is not in the allowed set {allowed}.",
                tool_calls=ctx.tool_calls,
            )

        if action == "scale_hpa":
            target = ctx.params.get("target_replicas", 4)
            max_replicas = guardrails.get("max_replicas")
            if max_replicas is not None and target > max_replicas:
                clamped = max_replicas
                await ctx.call("kubectl.scale", replicas=clamped, **ctx.params)
                return AgentResult(
                    status="success",
                    output={"action": action, "requested_replicas": target, "applied_replicas": clamped},
                    reasoning=f"Requested {target} replicas exceeded guardrail max_replicas={max_replicas} — clamped to {clamped}.",
                    tool_calls=ctx.tool_calls,
                )
            await ctx.call("kubectl.scale", replicas=target, **ctx.params)
            return AgentResult(
                status="success",
                output={"action": action, "applied_replicas": target},
                reasoning=f"Scaled to {target} replicas.",
                tool_calls=ctx.tool_calls,
            )

        if action == "rollback_release":
            await ctx.call("helm.rollback", **ctx.params)
            return AgentResult(
                status="success",
                output={"action": action},
                reasoning="Rolled back to the last known-good release.",
                tool_calls=ctx.tool_calls,
            )

        await ctx.call("kubectl.restart", **ctx.params)
        return AgentResult(
            status="success",
            output={"action": "restart_pod"},
            reasoning="Restarted the affected pod.",
            tool_calls=ctx.tool_calls,
        )
