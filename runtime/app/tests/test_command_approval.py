"""execute_paused_round is the one genuinely new piece of the per-command
mutation gate — denial never touches a real tool, so it's fully testable
without a live cluster; approval just needs to not crash structurally.
No pytest-asyncio dependency — asyncio.run() is enough for a handful of
one-shot async calls."""

import asyncio

from app.orchestrator.executor import MUTATING_TOOLS, execute_paused_round


def test_kubectl_scale_is_mutating():
    assert "kubectl.scale" in MUTATING_TOOLS
    assert "kubectl.get" not in MUTATING_TOOLS


def test_deny_claude_format_never_calls_the_tool():
    messages = [
        {"role": "user", "content": "Task parameters: {}"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "kubectl.scale", "input": {"target": "deployment/redis", "replicas": 5}},
        ]},
    ]
    resumed, round_tool_calls = asyncio.run(execute_paused_round("anthropic", messages, approved=False))
    assert round_tool_calls == [{"tool": "kubectl.scale", "input": {"target": "deployment/redis", "replicas": 5}, "result": {"ok": False, "error": "Denied by user."}}]
    assert resumed[-1] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "{'ok': False, 'error': 'Denied by user.'}"}]}


def test_deny_local_format_never_calls_the_tool():
    messages = [
        {"role": "system", "content": "..."},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "kubectl.scale", "arguments": '{"target": "deployment/redis", "replicas": 5}'}},
        ]},
    ]
    resumed, round_tool_calls = asyncio.run(execute_paused_round("local", messages, approved=False))
    assert round_tool_calls[0]["result"] == {"ok": False, "error": "Denied by user."}
    assert resumed[-1] == {"role": "tool", "tool_call_id": "call_1", "content": "{'ok': False, 'error': 'Denied by user.'}"}


def test_deny_only_gates_the_mutating_call_others_still_run():
    messages = [
        {"role": "user", "content": "Task parameters: {}"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1", "name": "git.log", "input": {}},
            {"type": "tool_use", "id": "toolu_2", "name": "kubectl.scale", "input": {"target": "deployment/redis", "replicas": 5}},
        ]},
    ]
    resumed, round_tool_calls = asyncio.run(execute_paused_round("anthropic", messages, approved=False))
    assert round_tool_calls[0]["tool"] == "git.log"
    assert round_tool_calls[0]["result"] != {"ok": False, "error": "Denied by user."}
    assert round_tool_calls[1] == {"tool": "kubectl.scale", "input": {"target": "deployment/redis", "replicas": 5}, "result": {"ok": False, "error": "Denied by user."}}


def test_restart_refuses_own_deployment():
    from app.tools.real import RealKubectlRestart

    for target in ("deployment/runtime", "deployments.apps/runtime", "runtime"):
        r = asyncio.run(RealKubectlRestart().run(target=target))
        assert not r.ok and "runtime deployment" in r.error
