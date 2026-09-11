#!/usr/bin/env python3
"""Tiny host-side control agent for llama-server — run manually on the host
(NOT containerized, same category as llama-server itself). bff/runtime run
inside kind pods and have no access to this machine's process table; this
is the bridge that lets the dashboard's "Start model" button actually start
something real, reachable the same way llama-server already is (kind's
Docker bridge gateway, 172.18.0.1).

No app-level auth — network-scoped the same way llama-server already is
(bound 0.0.0.0; the real boundary is the existing ufw rule scoped to the
kind bridge subnet). Known trade-off, not an oversight: this can start a
fixed, hardcoded command, nothing caller-controlled.

Run: python3 local-model-control/agent.py
"""

import json
import os
import socket
import subprocess
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

CONTROL_PORT = 8081
LLAMA_HEALTH_URL = "http://127.0.0.1:8080/health"
MODEL_PATH = os.path.expanduser("~/models/qwen2.5-coder-7b-instruct-q4_k_m.gguf")
MODEL_CMD = [
    "/opt/llama-cpp-cuda-pascal/bin/llama-server",
    "-m", MODEL_PATH, "--port", "8080", "--host", "0.0.0.0",
    "-c", "4096", "-ngl", "999", "-t", "6",
]


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

    def do_GET(self) -> None:
        if self.path == "/status":
            self._json({"state": check_status()})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if self.path != "/start":
            self._json({"error": "not found"}, 404)
            return
        state = check_status()
        if state == "stopped":
            subprocess.Popen(MODEL_CMD, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            state = "loading"
        self._json({"state": state})

    def log_message(self, format: str, *args) -> None:
        pass  # ponytail: quiet by default — add real logging if this needs debugging


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", CONTROL_PORT), Handler)
    print(f"local-model-control listening on :{CONTROL_PORT}")
    server.serve_forever()
