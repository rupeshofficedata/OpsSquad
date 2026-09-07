from app.agents.base import AgentContext, AgentResult, SimulatedAgent

DEFAULT_ERROR_RATE_THRESHOLD = 0.02


class VerifyAgent(SimulatedAgent):
    slug = "verify"

    async def run(self, ctx: AgentContext) -> AgentResult:
        smoke = await ctx.call("http.smoke_test", **ctx.params)
        metrics = await ctx.call("prometheus.query", **ctx.params)

        threshold = ctx.params.get("error_rate_threshold", DEFAULT_ERROR_RATE_THRESHOLD)
        error_rate = metrics.get("error_rate", 0.0)
        output = {
            "smoke_test_ok": smoke.get("ok"),
            "error_rate": error_rate,
            "error_rate_threshold": threshold,
            "p99_latency_ms": metrics.get("p99_latency_ms"),
        }

        if not smoke.get("ok"):
            return AgentResult(
                status="failed",
                output=output,
                reasoning=f"Smoke test failed: {', '.join(smoke.get('failures', []))}",
                tool_calls=ctx.tool_calls,
            )
        if error_rate > threshold:
            return AgentResult(
                status="failed",
                output=output,
                reasoning=f"Error rate {error_rate:.2%} exceeds threshold {threshold:.2%}.",
                tool_calls=ctx.tool_calls,
            )
        return AgentResult(
            status="success",
            output=output,
            reasoning=f"Smoke tests passed, error rate {error_rate:.2%} within threshold {threshold:.2%}.",
            tool_calls=ctx.tool_calls,
        )
