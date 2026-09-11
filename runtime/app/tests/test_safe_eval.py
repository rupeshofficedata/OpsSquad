"""Real pytest coverage for orchestrator/safe_eval.py — the real target
for test.run/test.select (app/tools/real.py). Same cases as safe_eval.py's
own __main__ self-check, split into real test functions instead of one
assert block."""

import pytest

from app.orchestrator.safe_eval import UnsafeExpressionError, evaluate

CTX = {
    "inputs": {"repo": "payments-api", "commit": "abc1234567"},
    "steps": {
        "verify": {"status": "failed"},
        "triage": {"severity": "P1"},
        "diagnose": {"recommended_action": "restart_pod", "suggested_replicas": 6},
        "remediate": {"action": "restart_pod"},
    },
}


def test_template_field_access():
    assert evaluate("inputs.repo", CTX) == "payments-api"


def test_slice():
    assert evaluate("inputs.commit[:7]", CTX) == "abc1234"


def test_compare_and_membership():
    assert evaluate("steps.verify.status == 'failed'", CTX) is True
    assert evaluate("steps.triage.severity in ['P1','P2']", CTX) is True
    assert evaluate("steps.triage.severity in ['P3','P4']", CTX) is False


def test_nested_field_access():
    assert evaluate("steps.diagnose.recommended_action", CTX) == "restart_pod"
    assert evaluate("steps.diagnose.suggested_replicas", CTX) == 6


def test_missing_step_is_graceful():
    assert evaluate("steps.missing.status == 'failed'", CTX) is False


@pytest.mark.parametrize("payload", [
    "().__class__.__bases__[0].__subclasses__()",
    "__import__('os').system('id')",
    "(lambda: 1)()",
])
def test_escape_payloads_rejected(payload):
    with pytest.raises(UnsafeExpressionError):
        evaluate(payload, CTX)


def test_dunder_keys_are_inert_not_rejected():
    # Not rejected outright, but harmless — attribute access is dict.get(),
    # never real getattr(), so there's no class/method object to reach.
    assert evaluate("steps.__class__", CTX) is None
    assert evaluate("steps.verify.__init__", CTX) is None
