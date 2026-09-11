"""Smallest possible check for check_status()'s 3-way branch — no real
llama-server needed. Run: python3 local-model-control/test_agent.py"""

import urllib.error
from unittest.mock import patch

from agent import check_status


class _FakeResp:
    def __init__(self, status):
        self.status = status
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


with patch("agent.urllib.request.urlopen", return_value=_FakeResp(200)):
    assert check_status() == "running"

with patch("agent.urllib.request.urlopen", side_effect=urllib.error.HTTPError("u", 503, "loading", {}, None)):
    assert check_status() == "loading"

with patch("agent.urllib.request.urlopen", side_effect=ConnectionRefusedError()):
    assert check_status() == "stopped"

print("agent: all checks passed")
