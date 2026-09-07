from app.agents.base import AgentContext, AgentResult, SimulatedAgent


class BuildAgent(SimulatedAgent):
    slug = "build"

    async def run(self, ctx: AgentContext) -> AgentResult:
        await ctx.call("docker.build", **ctx.params)
        await ctx.call("docker.tag", **ctx.params)
        await ctx.call("registry.push", **ctx.params)

        tag = ctx.params.get("tag", "latest")
        return AgentResult(
            status="success",
            output={"image_tag": tag},
            reasoning=f"Built and pushed image tagged '{tag}'.",
            tool_calls=ctx.tool_calls,
        )
