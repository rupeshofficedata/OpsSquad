"""Redaction, log/event/target summaries, and kubectl 'did you mean' hints."""

import asyncio

from app.tools import real
from app.tools.redact import redact_obj, redact_text


def test_redacts_common_secret_shapes():
    text = (
        "DATABASE_URL=postgresql://opssquad:hunter2@postgres:5432/db password=abc123 "
        "Authorization: Bearer abcdefghijklmnop key AKIAABCDEFGHIJKLMNOP "
        "jwt eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0NTY.SflKxwRJSMeKKF2QT4fw"
    )
    out = redact_text(text)
    for leaked in ("hunter2", "abc123", "abcdefghijklmnop", "AKIAABCDEFGHIJKLMNOP", "SflKxwRJSMeKKF2QT4fw"):
        assert leaked not in out
    assert "postgres:5432/db" in out  # host and db name stay readable


def test_redacts_secret_keys_in_structures_but_not_lookalikes():
    out = redact_obj({"token": "s3cr3t", "secrets_found": 2, "nested": [{"password": "x"}], "note": "ok"})
    assert out == {"token": "[REDACTED]", "secrets_found": 2, "nested": [{"password": "[REDACTED]"}], "note": "ok"}


def test_summarize_logs_keeps_the_failure_text():
    text = "\n".join(["starting"] * 100 + ["fatal: cannot open /etc/crashy/config.yaml: no such file"])
    s = real._summarize_logs(text)
    assert s["lines_returned"] == 101 and s["error_count"] == 1
    assert s["error_lines"] == ["fatal: cannot open /etc/crashy/config.yaml: no such file"]
    assert len(s["tail"]) == 40 and s["tail"][-1].startswith("fatal")


def test_summarize_events_sorts_limits_and_filters():
    ev = lambda t, r, ts: {"type": t, "reason": r, "lastTimestamp": ts, "message": "m", "involvedObject": {"kind": "Pod", "name": "p"}}
    items = [ev("Normal", "Pulled", "2026-01-01T00:00:03Z"), ev("Warning", "BackOff", "2026-01-01T00:00:01Z"), ev("Normal", "Started", "2026-01-01T00:00:02Z")]
    assert [e["reason"] for e in real._summarize_events(items)] == ["BackOff", "Started", "Pulled"]
    assert [e["reason"] for e in real._summarize_events(items, warnings_only=True)] == ["BackOff"]
    assert real._summarize_events(items, limit=1)[0]["object"] == "pod/p"


def test_summarize_targets():
    payload = {"data": {"activeTargets": [{"labels": {"job": "a", "instance": "x:1"}, "health": "down", "lastError": "refused"}]}}
    assert real._summarize_targets(payload) == [{"job": "a", "instance": "x:1", "health": "down", "last_error": "refused"}]


def test_notfound_lists_real_names(monkeypatch):
    async def fake_run(*args, **kw):
        return 0, "deployment.apps/postgres\ndeployment.apps/redis\n", ""
    monkeypatch.setattr(real, "_run", fake_run)
    msg = asyncio.run(real._with_hint('Error from server (NotFound): deployments.apps "postgres-c9d67bf79" not found', "deployment/postgres-c9d67bf79"))
    assert "Did you mean: deployment/postgres?" in msg
    assert asyncio.run(real._with_hint("connection refused", "deployment/x")) == "connection refused"


def test_trivy_refuses_paths_outside_the_scan_root():
    r = asyncio.run(real.RealTrivyScan().run(scan_path="/etc"))
    assert not r.ok and "outside" in r.error
