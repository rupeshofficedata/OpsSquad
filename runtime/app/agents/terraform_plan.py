from app.agents.base import AgentContext, AgentResult, SimulatedAgent


class TerraformPlanAgent(SimulatedAgent):
    slug = "terraform-plan"

    async def run(self, ctx: AgentContext) -> AgentResult:
        await ctx.call("git.diff", **ctx.params)
        plan = await ctx.call("terraform.plan", **ctx.params)

        summary = f"+{plan.get('to_add', 0)} ~{plan.get('to_change', 0)} -{plan.get('to_destroy', 0)}"
        return AgentResult(
            status="success",
            output={**plan, "summary": summary},
            reasoning=f"Generated plan: {summary}. Never applies — a human or terraform-apply does that.",
            tool_calls=ctx.tool_calls,
        )
