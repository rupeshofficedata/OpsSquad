"""Deterministic fake data for simulated tool calls.

Seeded by the tool's own kwargs, so the same input (same commit, same image
tag, same prompt) always produces the same simulated result — reproducible
for demos and tests — while different input produces different, still
plausible, outcomes. This is what lets agent-level policy logic (e.g.
"block on CRITICAL CVE") actually branch differently across runs instead of
every simulated run being an identical no-op success.
"""

import hashlib
import random
from typing import Any


def seeded_random(kwargs: dict[str, Any]) -> random.Random:
    key = "|".join(f"{k}={v}" for k, v in sorted(kwargs.items()) if isinstance(v, (str, int, float)))
    seed = int(hashlib.sha256(key.encode()).hexdigest()[:16], 16)
    return random.Random(seed)


_PACKAGES = ["openssl", "libxml2", "curl", "zlib", "glibc", "busybox", "libssh2", "pcre2"]
_SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
_SEVERITY_WEIGHTS = [0.12, 0.23, 0.3, 0.35]


def trivy_scan(kwargs: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    n = rng.randint(1, 5)
    cves = [
        {
            "id": f"CVE-2024-{rng.randint(1000, 9999)}",
            "severity": rng.choices(_SEVERITIES, weights=_SEVERITY_WEIGHTS)[0],
            "package": rng.choice(_PACKAGES),
            "fixed_in": f"{rng.randint(1, 9)}.{rng.randint(0, 9)}.{rng.randint(0, 9)}",
        }
        for _ in range(n)
    ]
    counts = {s: sum(1 for c in cves if c["severity"] == s) for s in _SEVERITIES}
    return {"cves": cves, "counts": counts}


def secrets_scan(kwargs: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    # Secrets are rare in this simulation — most diffs are clean.
    if rng.random() < 0.12:
        kinds = ["AWS access key", "private key", "API token", "database password"]
        return {"secrets_found": [{"kind": rng.choice(kinds), "file": "config/settings.py", "line": rng.randint(1, 200)}]}
    return {"secrets_found": []}


def test_run(kwargs: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    total = rng.randint(20, 120)
    failed = 0 if rng.random() < 0.8 else rng.randint(1, 3)
    failures = [f"test_{rng.choice(['auth', 'billing', 'checkout', 'search'])}_{i}" for i in range(failed)]
    return {"total": total, "passed": total - failed, "failed": failed, "failures": failures}

def lint_run(kwargs: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    return {"warnings": rng.randint(0, 6)}


def prometheus_query(kwargs: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    # error_rate is the one metric agent logic actually thresholds against;
    # everything else is descriptive.
    # Cubed so most draws land well under the default 0.02 verify threshold
    # (a healthy deploy is the common case) while an occasional release
    # still lands above it, exercising the rollback path.
    error_rate = (rng.random() ** 3) * 0.06
    return {
        "error_rate": round(error_rate, 4),
        "p99_latency_ms": rng.randint(80, 900),
        "cpu_utilization": round(rng.uniform(0.1, 0.9), 2),
    }


def http_smoke_test(kwargs: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    ok = rng.random() > 0.05
    return {"ok": ok, "checks_run": rng.randint(3, 10), "failures": [] if ok else ["GET /healthz -> 503"]}


def cloud_cost_explorer(kwargs: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    n = rng.randint(2, 6)
    resource_types = ["EBS volume", "idle EC2 instance", "unattached Elastic IP", "oversized RDS instance"]
    resources = [
        {
            "type": rng.choice(resource_types),
            "id": f"res-{rng.randint(10000, 99999)}",
            "monthly_cost_usd": rng.randint(15, 400),
        }
        for _ in range(n)
    ]
    return {"idle_resources": resources, "monthly_savings_usd": sum(r["monthly_cost_usd"] for r in resources)}


def alertmanager_read(kwargs: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    severity = rng.choices(["P1", "P2", "P3", "P4"], weights=[0.15, 0.3, 0.3, 0.25])[0]
    alertname = rng.choice(["HighErrorRate", "PodCrashLooping", "HighMemoryUsage", "SlowResponseTime"])
    return {"severity": severity, "alertname": alertname, "summary": f"{alertname} firing on payments-api"}


def kubectl_logs(kwargs: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    error_count = rng.randint(0, 15)
    return {"lines_returned": rng.randint(50, 500), "error_count": error_count}


def kubectl_get(kwargs: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    desired = rng.randint(2, 6)
    ready = desired if rng.random() > 0.15 else max(0, desired - rng.randint(1, 2))
    return {"replicas_desired": desired, "replicas_ready": ready}


def terraform_plan(kwargs: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    return {"to_add": rng.randint(0, 3), "to_change": rng.randint(0, 5), "to_destroy": rng.randint(0, 2)}


def iac_scan(kwargs: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    if rng.random() < 0.2:
        return {"misconfigurations": [{"rule": "S3 bucket public read", "resource": "aws_s3_bucket.assets"}]}
    return {"misconfigurations": []}


# Maps tool name -> payload builder. Tools not listed here keep the plain
# "simulated: true" echo from stubs.py — they don't need bespoke logic
# because no agent branches on their output (e.g. docker.build just needs
# to have happened, not be reasoned about).
PAYLOAD_BUILDERS = {
    "trivy.scan": trivy_scan,
    "secrets.scan": secrets_scan,
    "test.run": test_run,
    "lint.run": lint_run,
    "prometheus.query": prometheus_query,
    "http.smoke_test": http_smoke_test,
    "cloud.cost_explorer": cloud_cost_explorer,
    "alertmanager.read": alertmanager_read,
    "kubectl.logs": kubectl_logs,
    "kubectl.get": kubectl_get,
    "terraform.plan": terraform_plan,
    "iac.scan": iac_scan,
}
