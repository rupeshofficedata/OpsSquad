"""Stand-ins for the external services the runtime tools call, so the test
namespace has no "credential/service not configured" blockers. Every response
is deterministic and the mock says so via /mock/*; nothing here is a real
account. Stdlib only.

  /pagerduty/incidents[/<id>]   PagerDuty REST v2 (needs `Token token=test-token`)
  /slack/webhook (POST)         Slack incoming webhook; posts kept for /mock/slack
  POST /  + X-Amz-Target        AWS Cost Explorer GetCostAndUsage
  /metrics                      Prometheus text; checkout error ratio is over the alert threshold
  /sink (POST)                  Alertmanager webhook receiver; kept for /mock/sink
  /mock/slack, /mock/sink       what the mock has received (for post-run checks)
"""
import json
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = "Token token=test-token"
NOW = datetime.now(timezone.utc)
INCIDENTS = {
    "PTEST001": {"id": "PTEST001", "type": "incident", "status": "triggered", "urgency": "high",
                 "title": "Checkout error ratio above 5%", "service": {"summary": "checkout"},
                 "created_at": (NOW - timedelta(minutes=12)).isoformat(),
                 "assignments": [{"assignee": {"summary": "oncall-primary"}}]},
    "PTEST002": {"id": "PTEST002", "type": "incident", "status": "resolved", "urgency": "low",
                 "title": "Disk usage warning on build node", "service": {"summary": "ci"},
                 "created_at": (NOW - timedelta(days=2)).isoformat(),
                 "assignments": []},
}
COSTS = [("Amazon EC2", "412.50"), ("Amazon RDS", "188.20"), ("Amazon S3", "36.75"), ("AWS Lambda", "0.00")]
SLACK, SINK = [], []


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/health":
            return self._send(200, {"ok": True})
        if p == "/metrics":
            return self._send(200, (
                "# TYPE checkout_error_ratio gauge\ncheckout_error_ratio 0.08\n"
                "# TYPE checkout_requests_total counter\ncheckout_requests_total 48210\n"), "text/plain")
        if p == "/mock/slack":
            return self._send(200, SLACK)
        if p == "/mock/sink":
            return self._send(200, SINK)
        if p.startswith("/pagerduty/"):
            if self.headers.get("Authorization") != TOKEN:
                return self._send(401, {"error": {"message": "Unauthorized", "code": 2006}})
            rest = p[len("/pagerduty/"):]
            if rest == "incidents":
                wanted = parse_qs(urlparse(self.path).query).get("statuses[]")
                return self._send(200, {"incidents": [i for i in INCIDENTS.values() if not wanted or i["status"] in wanted]})
            inc = INCIDENTS.get(rest.removeprefix("incidents/"))
            if inc:
                return self._send(200, {"incident": inc})
            return self._send(404, {"error": {"message": "Not Found", "code": 2100}})
        self._send(404, {"error": "unknown path"})

    def do_POST(self):
        p, body = self.path.split("?")[0], self._body()
        if p == "/slack/webhook":
            SLACK.append(json.loads(body or "{}"))
            return self._send(200, "ok", "text/plain")
        if p == "/sink":
            SINK.append(json.loads(body or "{}"))
            return self._send(200, {})
        if p == "/" and "GetCostAndUsage" in (self.headers.get("X-Amz-Target") or ""):
            end = date.today()
            return self._send(200, {"ResultsByTime": [{
                "TimePeriod": {"Start": (end - timedelta(days=30)).isoformat(), "End": end.isoformat()},
                "Total": {}, "Estimated": False,
                "Groups": [{"Keys": [s], "Metrics": {"UnblendedCost": {"Amount": a, "Unit": "USD"}}} for s, a in COSTS],
            }]}, "application/x-amz-json-1.1")
        self._send(404, {"error": "unknown path"})

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("0.0.0.0", 8080), H).serve_forever()
