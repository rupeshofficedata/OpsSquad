from app.agents.base import AgentContext, AgentResult, SimulatedAgent


class CodeReviewAgent(SimulatedAgent):
    slug = "code-review"

    async def run(self, ctx: AgentContext) -> AgentResult:
        await ctx.call("git.diff", **ctx.params)
        secrets = await ctx.call("secrets.scan", **ctx.params)
        lint = await ctx.call("lint.run", **ctx.params)

        found = secrets.get("secrets_found", [])
        if found:
            kinds = ", ".join(f"{s['kind']} in {s['file']}:{s['line']}" for s in found)
            return AgentResult(
                status="failed",
                output={"secrets_found": found, "lint_warnings": lint.get("warnings", 0)},
                reasoning=f"Blocked: found {len(found)} committed secret(s) — {kinds}",
                tool_calls=ctx.tool_calls,
            )

        warnings = lint.get("warnings", 0)
        return AgentResult(
            status="success",
            output={"secrets_found": [], "lint_warnings": warnings},
            reasoning=f"No secrets found. {warnings} lint warning(s).",
            tool_calls=ctx.tool_calls,
        )
