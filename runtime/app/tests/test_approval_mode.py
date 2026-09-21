"""APPROVAL_MODE=auto removes the human pause, so it must be impossible to
enable outside the disposable opssquad-test namespace."""

import pytest

from app.config import check_approval_mode


def test_manual_is_always_allowed():
    check_approval_mode("manual", "prod", "opssquad")
    check_approval_mode("manual", "test", "opssquad-test")


def test_auto_allowed_only_in_test_namespace():
    check_approval_mode("auto", "test", "opssquad-test")


@pytest.mark.parametrize("env,ns", [("prod", "opssquad"), ("test", "opssquad"), ("prod", "opssquad-test")])
def test_auto_refused_anywhere_else(env, ns):
    with pytest.raises(RuntimeError, match="only allowed"):
        check_approval_mode("auto", env, ns)


def test_unknown_mode_refused():
    with pytest.raises(RuntimeError):
        check_approval_mode("yolo", "test", "opssquad-test")
