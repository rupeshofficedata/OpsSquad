"""build_resume_messages is the one genuinely novel/fragile piece of the
ask_user multi-turn flow (provider-specific wire format for "here's the
user's answer to your question") — no live LLM needed to verify it."""

from app.orchestrator.executor import ASK_USER_TOOL_NAME, build_resume_messages


def test_resume_claude_format():
    messages = [
        {"role": "user", "content": "Task parameters: {}"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "I need to know which deployment."},
            {"type": "tool_use", "id": "toolu_123", "name": ASK_USER_TOOL_NAME, "input": {"question": "Which deployment?"}},
        ]},
    ]
    resumed = build_resume_messages("anthropic", messages, "runtime")
    assert resumed[:-1] == messages
    last = resumed[-1]
    assert last["role"] == "user"
    assert last["content"] == [{"type": "tool_result", "tool_use_id": "toolu_123", "content": "runtime"}]


def test_resume_local_openai_format():
    messages = [
        {"role": "system", "content": "..."},
        {"role": "user", "content": "Task parameters: {}"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "local-fallback-0", "type": "function",
             "function": {"name": ASK_USER_TOOL_NAME, "arguments": '{"question": "Which deployment?"}'}},
        ]},
    ]
    resumed = build_resume_messages("local", messages, "runtime")
    assert resumed[:-1] == messages
    assert resumed[-1] == {"role": "tool", "tool_call_id": "local-fallback-0", "content": "runtime"}
