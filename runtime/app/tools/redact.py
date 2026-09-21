"""Masks credentials in tool output and final answers before they reach the
model, the run trace, or the dashboard. Pattern-based, so it is a safety net,
not a guarantee: it catches the common shapes (key=value secrets, bearer
tokens, AWS key ids, JWTs, passwords inside URLs, PEM private keys)."""

import re
from typing import Any

_SECRET_KEY = re.compile(r"(?i)^(password|passwd|pwd|secret|token|api_?key|access_?key|secret_?key|private_?key|authorization)$")
_TEXT_PATTERNS = [
    # before key=value, or "Authorization: Bearer <token>" would only mask the word "Bearer"
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED PRIVATE KEY]"),
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|secret[_-]?key|authorization)(['\"]?\s*[:=]\s*['\"]?)[^\s'\",;&}]+"), r"\1\2[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED AWS KEY ID]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[REDACTED JWT]"),
    (re.compile(r"(\b\w+://[^\s:/@]+:)[^\s@/]+@"), r"\1[REDACTED]@"),
]


def redact_text(text: str) -> str:
    for pattern, repl in _TEXT_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {
            k: "[REDACTED]" if isinstance(k, str) and _SECRET_KEY.match(k) and isinstance(v, str) and v else redact_obj(v)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj
