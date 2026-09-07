from app.agents.base import AgentContext, AgentResult, SimulatedAgent


class CostAnalyzerAgent(SimulatedAgent):
    slug = "cost-analyzer"

    async def run(self, ctx: AgentContext) -> AgentResult:
        costs = await ctx.call("cloud.cost_explorer", **ctx.params)
        await ctx.call("kubectl.get", **ctx.params)
        await ctx.call("prometheus.query", **ctx.params)

        resources = costs.get("idle_resources", [])
        savings = costs.get("monthly_savings_usd", 0)
        return AgentResult(
            status="success",
            output={"idle_resources": resources, "monthly_savings_usd": savings},
            reasoning=f"Found {len(resources)} idle/oversized resource(s), ${savings}/mo in potential savings.",
            tool_calls=ctx.tool_calls,
        )
