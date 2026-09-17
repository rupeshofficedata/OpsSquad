"""Regression test for a live incident: _run_simulated's generic fallback
(for any agent with no app/agents/ module, e.g. 'assistant') used to loop
over every declared tool with zero gating. Against a real cluster
(REAL_TOOLS_ENABLED=true, the default) this genuinely executed real
kubectl/terraform/docker/helm/argocd/registry mutations in one shot from
a single chat message with no LLM decision and no approval — confirmed
live via `kubectl get events` showing a real redis rollout restart.
No pytest-asyncio dependency — asyncio.run() is enough."""

import asyncio

from app.orchestrator.executor import MUTATING_TOOLS, _run_simulated


def test_mutating_tools_refused_without_touching_the_real_registry(monkeypatch):
    calls: list[str] = []

    def fake_get_tool(name):
        calls.append(name)
        raise AssertionError(f"get_tool() must never be called for a mutating tool, got: {name}")

    monkeypatch.setattr("app.orchestrator.executor.get_tool", fake_get_tool)

    agent = {"slug": "not-a-real-module", "name": "Test Agent"}
    mutating_name = next(iter(MUTATING_TOOLS))
    output, reasoning, tool_calls, status = asyncio.run(
        _run_simulated(agent, {"prompt": "do something"}, [mutating_name])
    )

    assert calls == []
    assert status == "success"
    assert len(tool_calls) == 1
    assert tool_calls[0]["tool"] == mutating_name
    assert tool_calls[0]["result"]["ok"] is False
    assert "refused" in tool_calls[0]["result"]["error"]


def test_non_mutating_tools_still_run_generically(monkeypatch):
    class FakeResult:
        def model_dump(self):
            return {"ok": True, "data": {"stub": True}, "error": None}

    class FakeTool:
        async def run(self, **kwargs):
            return FakeResult()

    monkeypatch.setattr("app.orchestrator.executor.get_tool", lambda name: FakeTool())

    agent = {"slug": "not-a-real-module", "name": "Test Agent"}
    output, reasoning, tool_calls, status = asyncio.run(
        _run_simulated(agent, {}, ["kubectl.get"])
    )

    assert tool_calls[0]["tool"] == "kubectl.get"
    assert tool_calls[0]["result"]["ok"] is True
