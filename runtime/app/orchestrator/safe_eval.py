"""Restricted expression evaluator for Flightplan `when`/`policy` fields.

Replaces the old `eval(expr, {"__builtins__": {}}, ...)` approach, which
blocks builtin *names* but not Python's real attribute protocol —
`().__class__.__bases__[0].__subclasses__()` reaches arbitrary classes
without ever touching `__builtins__`, a well-known sandbox escape. Since
Flightplan creation only needs `dev` role, that escape was a real
privilege-escalation path to code execution in the runtime container.

This evaluator walks the AST itself against a strict node-type allowlist,
and — the actual fix — resolves attribute access as a **dict-key lookup**
(`obj.get(name)`), never real `getattr()`. There is no live Python object
to walk in the first place, so the escape class is structurally
impossible here, not merely denylisted. No `Call` node is permitted at
all, since nothing a Flightplan expression legitimately needs calls a
function.
"""

import ast
from typing import Any

_COMPARE_OPS = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}


class UnsafeExpressionError(Exception):
    pass


def evaluate(expr: str, context: dict[str, Any]) -> Any:
    try:
        # mode="eval" just tells ast.parse to expect a single expression
        # (vs. a module/statements) — no code executes here. The actual
        # evaluation below (_eval) never calls the real eval()/exec().
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise UnsafeExpressionError(f"invalid expression: {exc}") from exc
    return _eval(tree.body, context)


def _eval(node: ast.AST, ctx: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        if node.id not in ctx:
            raise UnsafeExpressionError(f"unknown name: {node.id}")
        return ctx[node.id]

    if isinstance(node, ast.Attribute):
        obj = _eval(node.value, ctx)
        if obj is None:
            return None
        if not isinstance(obj, dict):
            raise UnsafeExpressionError(f"cannot access .{node.attr} on a {type(obj).__name__}")
        return obj.get(node.attr)

    if isinstance(node, ast.Subscript):
        obj = _eval(node.value, ctx)
        if isinstance(node.slice, ast.Slice):
            bounds = [
                _eval(b, ctx) if b is not None else None
                for b in (node.slice.lower, node.slice.upper, node.slice.step)
            ]
            if any(b is not None and not isinstance(b, int) for b in bounds):
                raise UnsafeExpressionError("slice bounds must be int")
            return obj[bounds[0]:bounds[1]:bounds[2]]
        return obj[_eval(node.slice, ctx)]

    if isinstance(node, ast.Compare):
        left = _eval(node.left, ctx)
        for op, comparator in zip(node.ops, node.comparators):
            apply = _COMPARE_OPS.get(type(op))
            if apply is None:
                raise UnsafeExpressionError(f"disallowed comparison: {type(op).__name__}")
            right = _eval(comparator, ctx)
            if not apply(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.BoolOp):
        values = (_eval(v, ctx) for v in node.values)
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
        raise UnsafeExpressionError("disallowed bool op")

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval(node.operand, ctx)

    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e, ctx) for e in node.elts]

    raise UnsafeExpressionError(f"disallowed expression: {type(node).__name__}")


if __name__ == "__main__":
    # Every expression actually used in db/seed.sql's flightplans — must
    # keep working identically to the old eval()-based version.
    ctx = {
        "inputs": {"repo": "payments-api", "commit": "abc1234567"},
        "steps": {
            "verify": {"status": "failed"},
            "triage": {"severity": "P1"},
            "diagnose": {"recommended_action": "restart_pod", "suggested_replicas": 6},
            "remediate": {"action": "restart_pod"},
        },
    }
    assert evaluate("inputs.repo", ctx) == "payments-api"
    assert evaluate("inputs.commit[:7]", ctx) == "abc1234"
    assert evaluate("steps.verify.status == 'failed'", ctx) is True
    assert evaluate("steps.triage.severity in ['P1','P2']", ctx) is True
    assert evaluate("steps.triage.severity in ['P3','P4']", ctx) is False
    assert evaluate("steps.diagnose.recommended_action", ctx) == "restart_pod"
    assert evaluate("steps.diagnose.suggested_replicas", ctx) == 6
    assert evaluate("steps.missing.status == 'failed'", ctx) is False  # graceful, not a crash

    # The actual escape this replaces, plus variants — must never yield a
    # real Python object. Some are rejected outright (Call nodes, unknown
    # names); "steps.__class__" isn't rejected but is harmless: dict.get()
    # on a nonexistent key just returns None, since attribute access here
    # is never real getattr() — there's no class object to reach either way.
    rejected = [
        "().__class__.__bases__[0].__subclasses__()",
        "__import__('os').system('id')",
        "(lambda: 1)()",
    ]
    for payload in rejected:
        try:
            evaluate(payload, ctx)
            raise AssertionError(f"payload should have been rejected: {payload!r}")
        except UnsafeExpressionError:
            pass
    # Dunder-named dict keys aren't rejected outright, but they're inert:
    # attribute access is dict.get(), never real getattr(), so there's no
    # class/method object to reach regardless of the key name.
    assert evaluate("steps.__class__", ctx) is None
    assert evaluate("steps.verify.__init__", ctx) is None

    print("safe_eval: all checks passed")
