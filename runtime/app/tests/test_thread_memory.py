"""assemble_thread_messages is the wire-format piece of the chained-memory
feature (routes/chat.py's build_thread_history) — pure and DB/network-free,
so it's covered here directly instead of needing a live DB fixture. Same
reasoning as test_ask_user_resume.py next to it."""

from app.routes.chat import assemble_thread_messages


def test_claude_no_summary():
    turns = [
        {"role": "user", "content": "scale payments-api to 3"},
        {"role": "agent", "content": "Scaled payments-api to 3 replicas."},
    ]
    history = assemble_thread_messages("anthropic", "You are ops.", None, turns, "now scale it back to 2")
    assert history == [
        {"role": "user", "content": "scale payments-api to 3"},
        {"role": "assistant", "content": "Scaled payments-api to 3 replicas."},
        {"role": "user", "content": "now scale it back to 2"},
    ]


def test_local_prepends_system_message():
    history = assemble_thread_messages("local", "You are ops.", None, [], "hi")
    assert history[0] == {"role": "system", "content": "You are ops."}
    assert history[-1] == {"role": "user", "content": "hi"}


def test_summary_injected_as_assistant_turn_before_recent_turns():
    turns = [{"role": "user", "content": "recent question"}]
    history = assemble_thread_messages("anthropic", "sys", "earlier: scaled payments-api", turns, "next question")
    assert history == [
        {"role": "assistant", "content": "earlier: scaled payments-api"},
        {"role": "user", "content": "recent question"},
        {"role": "user", "content": "next question"},
    ]
