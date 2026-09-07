from app.agents.base import AgentContext, AgentResult, SimulatedAgent


class TerraformApplyAgent(SimulatedAgent):
    slug = "terraform-apply"

    async def run(self, ctx: AgentContext) -> AgentResult:
        await ctx.call("terraform.apply", **ctx.params)
        return AgentResult(
            status="success",
            output={"applied": True},
            reasoning="Applied the approved Terraform plan.",
            tool_calls=ctx.tool_calls,
        )
