from app.agents.base import AgentContext, AgentResult, SimulatedAgent

DEFAULT_BLOCK_ON = ["CRITICAL"]


class SecurityScanAgent(SimulatedAgent):
    slug = "security-scan"

    async def run(self, ctx: AgentContext) -> AgentResult:
        await ctx.call("registry.pull", **ctx.params)
        scan = await ctx.call("trivy.scan", **ctx.params)
        iac = await ctx.call("iac.scan", **ctx.params)

        # A Flightplan step can declare `policy: { block_on: [...] }` — graph.py
        # merges that into params under "policy". Direct chat/agent invocation
        # has no such context, so fall back to blocking CRITICAL by default.
        block_on = set(ctx.params.get("policy", {}).get("block_on", DEFAULT_BLOCK_ON))
        cves = scan.get("cves", [])
        blocking = [c for c in cves if c["severity"] in block_on]
        misconfigs = iac.get("misconfigurations", [])

        output = {"cves": cves, "counts": scan.get("counts", {}), "misconfigurations": misconfigs}
        if blocking:
            ids = ", ".join(f"{c['id']} ({c['severity']}, {c['package']})" for c in blocking)
            return AgentResult(
                status="failed",
                output=output,
                reasoning=f"Blocked by policy {sorted(block_on)}: {ids}",
                tool_calls=ctx.tool_calls,
            )
        return AgentResult(
            status="success",
            output=output,
            reasoning=f"No CVEs at or above policy threshold {sorted(block_on)}. Found {len(cves)} total, {len(misconfigs)} IaC misconfiguration(s).",
            tool_calls=ctx.tool_calls,
        )
