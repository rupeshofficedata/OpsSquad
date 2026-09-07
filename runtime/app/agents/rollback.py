from app.agents.base import AgentContext, AgentResult, SimulatedAgent


class RollbackAgent(SimulatedAgent):
    slug = "rollback"

    async def run(self, ctx: AgentContext) -> AgentResult:
        await ctx.call("helm.rollback", **ctx.params)
        await ctx.call("argocd.rollback", **ctx.params)
        status = await ctx.call("kubectl.get", **ctx.params)

        return AgentResult(
            status="success",
            output={
                "replicas_ready": status.get("replicas_ready"),
                "replicas_desired": status.get("replicas_desired"),
            },
            reasoning="Reverted to the last known-good release.",
            tool_calls=ctx.tool_calls,
        )
