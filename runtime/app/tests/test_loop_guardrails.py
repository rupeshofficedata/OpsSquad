"""Agent-loop guardrails: denial memory, history trimming, forced conclusion."""

import asyncio
import json

import httpx

from app.orchestrator import executor
from app.orchestrator.executor import _denied_tools, _trim_old_tool_output


def test_denied_tools_local_format():
    msgs = [
        {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "kubectl.scale"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "{'ok': False, 'error': 'Denied by user.'}"},
        {"role": "assistant", "tool_calls": [{"id": "c2", "function": {"name": "kubectl.get"}}]},
        {"role": "tool", "tool_call_id": "c2", "content": "{'ok': True}"},
    ]
    assert _denied_tools(msgs) == {"kubectl.scale"}


def test_denied_tools_claude_format():
    msgs = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "helm.rollback", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{'error': 'Denied by user.'}"}]},
    ]
    assert _denied_tools(msgs) == {"helm.rollback"}


def test_trim_shortens_oldest_tool_output_but_never_the_latest_round():
    big = "x" * 5000
    msgs = [
        {"role": "assistant", "tool_calls": []}, {"role": "tool", "content": big},
        {"role": "assistant", "tool_calls": []}, {"role": "tool", "content": big},
    ]
    out = _trim_old_tool_output(msgs, 6000)
    assert out[1]["content"].endswith("[older output trimmed]") and len(out[1]["content"]) < 400
    assert out[3]["content"] == big
    assert msgs[1]["content"] == big  # input untouched
    assert _trim_old_tool_output(msgs, 100000) is msgs


def test_repeated_tool_hits_the_cap_then_gets_a_tool_less_final_answer(monkeypatch):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if "tools" not in body:  # the forced-conclusion call
            return httpx.Response(200, json={"choices": [{"message": {"content": "FINAL ANSWER"}}]})
        n = len(requests)
        call = {"id": f"c{n}", "type": "function", "function": {"name": "git.log", "arguments": json.dumps({"n": n})}}
        return httpx.Response(200, json={"choices": [{"message": {"content": "", "tool_calls": [call]}}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(executor.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    agent = {"system_prompt": "s", "tools": ["git.log"]}
    out, _, calls, _, _, _ = asyncio.run(executor._run_with_local_llm(agent, {}, ["git.log"], None))
    assert out["summary"] == "FINAL ANSWER"
    assert len(calls) == executor.MAX_CALLS_PER_TOOL  # stopped looping instead of burning all 8 rounds
    assert "tools" not in requests[-1]


def test_model_failure_after_tools_ran_keeps_the_real_results(monkeypatch):
    n = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        n["calls"] += 1
        if n["calls"] == 1:
            call = {"id": "c1", "type": "function", "function": {"name": "git.log", "arguments": "{}"}}
            return httpx.Response(200, json={"choices": [{"message": {"content": "", "tool_calls": [call]}}]})
        raise httpx.ReadTimeout("model too slow", request=request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(executor.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    agent = {"system_prompt": "s", "tools": ["git.log"]}
    out, _, calls, _, _, _ = asyncio.run(executor._run_with_local_llm(agent, {}, ["git.log"], None))
    assert len(calls) == 1
    assert "ReadTimeout" in out["summary"] and "git.log" in out["summary"]


def test_model_failure_before_any_tool_still_raises_for_the_simulated_fallback(monkeypatch):
    import pytest

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(executor.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(httpx.ConnectError):
        asyncio.run(executor._run_with_local_llm({"system_prompt": "s", "tools": []}, {}, [], None))


def test_length_finish_reason_is_flagged_as_cut_off(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": "Here are all 13 pods, with two of "}}]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(executor.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    out, *_ = asyncio.run(executor._run_with_local_llm({"system_prompt": "s", "tools": []}, {}, [], None))
    assert out["summary"].startswith("Here are all 13 pods") and "cut off" in out["summary"]
