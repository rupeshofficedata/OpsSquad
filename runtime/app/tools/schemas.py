"""JSON-schema `parameters` block per tool name — what executor.py sends to
Claude/the local LLM as each tool's `input_schema`/`parameters`. Previously
this was a blank `{"properties": {}, "additionalProperties": True}` for
every tool, so the model had no idea what a param was actually called and
guessed (e.g. "resource" instead of "target") — silently no-op'd or fell
back to defaults. Name-keyed dict, same pattern as SIMULATED_TOOLS/
PAYLOAD_BUILDERS, so real and simulated tools share one declaration instead
of each Tool subclass owning its own (most names are covered by the one
generic SimulatedTool class anyway, so a class attribute wouldn't fit them).
"""

from typing import Any

_TARGET = {"type": "string", "description": "Kubernetes resource, e.g. 'deployment/runtime' or 'pod/redis-abc123'"}


def _schema(properties: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties or {}, "required": required or []}


TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    # No params.
    "git.diff": _schema(),
    "git.log": _schema(),
    "secrets.scan": _schema(),
    "lint.run": _schema(),
    "test.select": _schema(),
    "test.run": _schema(),
    "iac.scan": _schema(),
    "helm.rollback": _schema(),
    "argocd.sync": _schema(),
    "argocd.rollback": _schema(),
    "http.smoke_test": _schema(),
    "alertmanager.read": _schema(),
    "terraform.plan": _schema(),
    "terraform.apply": _schema(),
    "cloud.cost_explorer": _schema(),
    "runs.read": _schema(),

    "docker.build": _schema({"tag": {"type": "string", "description": "Image tag to build, e.g. 'myapp:v1'"}}),
    "docker.tag": _schema({"tag": {"type": "string", "description": "Base image tag just built, to also tag ':latest' on"}}),
    "registry.push": _schema({"tag": {"type": "string", "description": "Image tag to push to the registry"}}),
    "registry.pull": _schema({"tag": {"type": "string", "description": "Image tag to pull from the registry"}}),
    "helm.upgrade": _schema({"tag": {"type": "string", "description": "Image tag to deploy via the canary release"}}),
    "trivy.scan": _schema({"scan_path": {"type": "string", "description": "Filesystem path to scan for CVEs"}}),
    "prometheus.query": _schema({"query": {"type": "string", "description": "PromQL query, e.g. 'up{job=\"x\"}'"}}),
    "slack.post": _schema(
        {"channel": {"type": "string", "description": "Slack channel, e.g. '#general'"},
         "message": {"type": "string", "description": "Message text to post"}},
        required=["message"],
    ),
    "pagerduty.read": _schema(
        {"incident_id": {"type": "string", "description": "PagerDuty incident ID to read"}},
        required=["incident_id"],
    ),

    "kubectl.get": _schema({"target": _TARGET}, required=["target"]),
    "kubectl.restart": _schema({"target": _TARGET}, required=["target"]),
    "kubectl.logs": _schema(
        {"target": _TARGET, "tail": {"type": "integer", "description": "Trailing log lines to return", "default": 200}},
        required=["target"],
    ),
    "kubectl.scale": _schema(
        {"target": _TARGET, "replicas": {"type": "integer", "description": "Desired replica count"}},
        required=["target", "replicas"],
    ),
}
