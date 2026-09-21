"""Smallest possible check for check_status()'s 3-way branch — no real
llama-server needed. Run: python3 local-model-control/test_agent.py"""

import urllib.error
from unittest.mock import patch

import agent
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

# --- model discovery / launch command
import tempfile, os, io, json
from http.server import HTTPServer
with tempfile.TemporaryDirectory() as d:
    for f in ("b.gguf", "a.gguf", "notes.txt", "c.gguf.part"):
        open(os.path.join(d, f), "w").write("x")
    with patch.object(agent, "MODEL_DIR", d):
        assert agent.list_models() == ["a.gguf", "b.gguf"]  # sorted, .gguf only, partial downloads ignored
    with patch.object(agent, "MODEL_DIR", "/nonexistent"):
        assert agent.list_models() == []

std = agent.model_cmd("qwen3-4b-q4_k_m.gguf")
assert std[0] == agent.STD_BIN and "-c" in std and "--reasoning-effort" not in std
bon = agent.model_cmd("ternary-bonsai-2-27b-ptq1_0.gguf")
assert bon[0] == agent.PRISM_BIN and "--reasoning-budget" in bon and bon[bon.index("--port") + 1] == "8080"

# --- process-table parsing: only llama-server on our port counts
def fake_proc(cmds):
    files = {f"/proc/{pid}/cmdline": "\0".join(argv).encode() for pid, argv in cmds.items()}
    def _open(path, mode="r", *a, **k):
        if path in files:
            return io.BytesIO(files[path])
        raise FileNotFoundError(path)
    return patch.object(agent, "open", _open, create=True), patch.object(agent.os, "listdir", lambda p: list(map(str, cmds)) + ["self"])

procs = {
    11: ["/usr/bin/llama-server", "-m", "/m/a.gguf", "--port", "8080"],
    12: ["/usr/bin/llama-server", "-m", "/m/b.gguf", "--port", "9999"],
    13: ["/usr/bin/python3", "-m", "http.server", "--port", "8080"],
}
p_open, p_ls = fake_proc(procs)
with p_open, p_ls:
    assert agent.running_servers() == [(11, "a.gguf")]
    assert agent.active_model() == "a.gguf"

# --- /load refuses anything not in the listing (no path traversal, no arbitrary file)
class _H(agent.Handler):
    def __init__(self, path, body):
        self.path, self.rfile, self.headers, self.sent = path, io.BytesIO(body), {"Content-Length": str(len(body))}, []
    def _json(self, obj, code=200):
        self.sent.append((code, obj))

with patch.object(agent, "list_models", lambda: ["a.gguf"]), patch.object(agent, "load_model") as load:
    h = _H("/load", json.dumps({"model": "../../etc/passwd"}).encode()); h.do_POST()
    assert h.sent[0][0] == 400 and not load.called
    load.return_value = "loading"
    h = _H("/load", json.dumps({"model": "a.gguf"}).encode()); h.do_POST()
    assert h.sent[0] == (200, {"state": "loading", "model": "a.gguf"}) and load.called

print("agent: all checks passed")
