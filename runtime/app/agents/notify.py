from app.agents.base import AgentContext, AgentResult, SimulatedAgent


class NotifyAgent(SimulatedAgent):
    slug = "notify"

    async def run(self, ctx: AgentContext) -> AgentResult:
        channel = ctx.params.get("channel", "#general")
        message = ctx.params.get(
            "message",
            f"Update on incident (severity {ctx.params.get('severity', 'unknown')}): "
            f"remediation action '{ctx.params.get('remediation_action', 'none')}' applied.",
        )
        await ctx.call("slack.post", channel=channel, message=message)

        return AgentResult(
            status="success",
            output={"channel": channel, "message": message},
            reasoning=f"Posted a status update to {channel}.",
            tool_calls=ctx.tool_calls,
        )
