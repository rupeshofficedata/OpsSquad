from app.agents.base import AgentContext, AgentResult, SimulatedAgent


class DeployAgent(SimulatedAgent):
    slug = "deploy"

    async def run(self, ctx: AgentContext) -> AgentResult:
        await ctx.call("helm.upgrade", **ctx.params)
        await ctx.call("argocd.sync", **ctx.params)
        status = await ctx.call("kubectl.get", **ctx.params)

        namespace = ctx.params.get("namespace", "default")
        chart = ctx.params.get("chart", "unknown-chart")
        return AgentResult(
            status="success",
            output={
                "namespace": namespace,
                "chart": chart,
                "replicas_ready": status.get("replicas_ready"),
                "replicas_desired": status.get("replicas_desired"),
            },
            reasoning=f"Deployed '{chart}' to namespace '{namespace}'.",
            tool_calls=ctx.tool_calls,
        )
