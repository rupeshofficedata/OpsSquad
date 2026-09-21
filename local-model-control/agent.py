#!/usr/bin/env python3
"""Tiny host-side control agent for llama-server — run manually on the host
(NOT containerized, same category as llama-server itself). bff/runtime run
inside kind pods and have no access to this machine's process table; this
is the bridge that lets the dashboard list, load and stop the local models
in ~/models for real, reachable the same way llama-server already is (kind's
Docker bridge gateway, 172.18.0.1).

No app-level auth — network-scoped the same way llama-server already is
(bound 0.0.0.0; the real boundary is the existing ufw rule scoped to the
kind bridge subnet). Known trade-off, not an oversight: the only thing a caller
chooses is a file name from the fixed ~/models listing; the command itself is
hardcoded here.

Run: python3 local-model-control/agent.py
"""

import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONTROL_PORT = 8081
LLAMA_PORT = 8080
LLAMA_HEALTH_URL = f"http://127.0.0.1:{LLAMA_PORT}/health"
MODEL_DIR = os.path.expanduser("~/models")
DEFAULT_MODEL = "qwen2.5-coder-7b-instruct-q4_k_m.gguf"
STD_BIN = "/opt/llama-cpp-cuda-pascal/bin/llama-server"
# Ternary-Bonsai PTQ1_0/PQ2_0 quantizations only load in the PrismML fork.
PRISM_BIN = os.path.expanduser("~/llama-prism/build/bin/llama-server")
LOG_PATH = "/tmp/opssquad-llama.log"
_lock = threading.Lock()  # one load/stop at a time


def list_models() -> list[str]:
    """Every .gguf in MODEL_DIR. The dashboard can only ever choose from this
    list, so no caller-controlled path or command reaches the launcher."""
    try:
        return sorted(f for f in os.listdir(MODEL_DIR) if f.endswith(".gguf"))
    except OSError:
        return []


def model_cmd(name: str) -> list[str]:
    path = os.path.join(MODEL_DIR, name)
    common = ["-m", path, "--port", str(LLAMA_PORT), "--host", "0.0.0.0", "-ngl", "999", "-t", "6", "-c", "8192"]
    if "bonsai" in name.lower():
        # -np 1 saves VRAM; the reasoning flags stop the model thinking past the client timeout.
        return [PRISM_BIN, *common, "-np", "1", "--reasoning-effort", "medium", "--reasoning-budget", "700"]
    return [STD_BIN, *common]


def running_servers() -> list[tuple[int, str]]:
    """(pid, model file) of every llama-server serving LLAMA_PORT, read from
    the process table so it also sees a server started by hand."""
    found = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                argv = [a.decode(errors="replace") for a in f.read().split(b"\0")]
        except OSError:
            continue
        if argv and os.path.basename(argv[0]) == "llama-server" and "-m" in argv and "--port" in argv:
            if argv[argv.index("--port") + 1] == str(LLAMA_PORT):
                found.append((int(pid), os.path.basename(argv[argv.index("-m") + 1])))
    return found


def active_model() -> str | None:
    servers = running_servers()
    return servers[0][1] if servers else None


def stop_server(timeout: float = 30.0) -> None:
    pids = [pid for pid, _ in running_servers()]
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and any(_alive(p) for p in pids):
        time.sleep(0.5)
    for pid in pids:
        if _alive(pid):
            os.kill(pid, signal.SIGKILL)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # a zombie still answers kill(0); treat it as gone
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def load_model(name: str) -> str:
    """Switch the served model. Returns the resulting state."""
    with _lock:
        if active_model() == name:
            return check_status()
        stop_server()
        with open(LOG_PATH, "ab") as log:
            subprocess.Popen(model_cmd(name), stdout=log, stderr=log, start_new_session=True)
        return "loading"


def check_status() -> str:
    try:
        with urllib.request.urlopen(LLAMA_HEALTH_URL, timeout=2) as resp:
            return "running" if resp.status == 200 else "loading"
    except urllib.error.HTTPError as exc:
        return "loading" if exc.code == 503 else "stopped"
    except (urllib.error.URLError, socket.timeout, ConnectionRefusedError):
        return "stopped"


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj: dict, code: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        try:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return {}

    def do_GET(self) -> None:
        if self.path == "/status":
            self._json({"state": check_status(), "model": active_model()})
        elif self.path == "/models":
            models = [{"name": m, "size_mb": os.path.getsize(os.path.join(MODEL_DIR, m)) // 1048576} for m in list_models()]
            self._json({"models": models, "active": active_model(), "state": check_status()})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if self.path == "/start":  # kept for the old "Start model" button: start the default if nothing is up
            self._json({"state": check_status() if active_model() else load_model(DEFAULT_MODEL)})
        elif self.path == "/load":
            name = self._body().get("model")
            if name not in list_models():
                self._json({"error": f"unknown model {name!r}", "models": list_models()}, 400)
                return
            self._json({"state": load_model(name), "model": name})
        elif self.path == "/stop":
            with _lock:
                stop_server()
            self._json({"state": check_status()})
        else:
            self._json({"error": "not found"}, 404)

    def log_message(self, format: str, *args) -> None:
        pass  # ponytail: quiet by default — add real logging if this needs debugging


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", CONTROL_PORT), Handler)
    print(f"local-model-control listening on :{CONTROL_PORT}")
    server.serve_forever()
