from app.agents.base import AgentContext, AgentResult, SimulatedAgent


class TestRunnerAgent(SimulatedAgent):
    slug = "test-runner"

    async def run(self, ctx: AgentContext) -> AgentResult:
        await ctx.call("git.diff", **ctx.params)
        await ctx.call("test.select", **ctx.params)
        result = await ctx.call("test.run", **ctx.params)

        failed = result.get("failed", 0)
        output = {
            "total": result.get("total", 0),
            "passed": result.get("passed", 0),
            "failed": failed,
            "failures": result.get("failures", []),
        }
        if failed:
            return AgentResult(
                status="failed",
                output=output,
                reasoning=f"{failed} test(s) failed: {', '.join(output['failures'])}",
                tool_calls=ctx.tool_calls,
            )
        return AgentResult(
            status="success",
            output=output,
            reasoning=f"All {output['total']} tests passed.",
            tool_calls=ctx.tool_calls,
        )
