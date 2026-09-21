"""Stub tool implementations.

These return deterministic, clearly-labeled simulated data so the orchestrator,
RBAC, and persistence layers can be exercised end-to-end without wiring real
kubectl/terraform/trivy/cloud credentials. Swap `SimulatedTool.run` for a real
CLI/API call per tool when connecting to actual infrastructure — keep each
tool scoped to the narrowest credential it needs.

Tools listed in `simulate.PAYLOAD_BUILDERS` return realistic structured data
(seeded deterministically by their own input) that the matching module in
app/agents/ actually reasons over — e.g. trivy.scan's CVE list is what
security_scan.py checks against its severity policy. Tools without a builder
just echo a generic "simulated: true" confirmation, because no agent needs to
branch on their output (e.g. docker.build only needs to have happened).
"""

from typing import Any

from app.tools.base import Tool, ToolResult
from app.tools.simulate import PAYLOAD_BUILDERS, seeded_random

# name -> a short description of what the real tool would do
SIMULATED_TOOLS: dict[str, str] = {
    "git.diff": "Fetch the diff for a repo/commit",
    "git.log": "Fetch recent commit history",
    "secrets.scan": "Scan a diff for committed secrets",
    "lint.run": "Run the project's linter",
    "test.select": "Select tests impacted by a diff",
    "test.run": "Run a test suite",
    "docker.build": "Build a container image",
    "docker.tag": "Tag a container image",
    "registry.push": "Push an image to the registry",
    "registry.pull": "Pull an image from the registry",
    "trivy.scan": "Scan an image for CVEs",
    "iac.scan": "Scan IaC templates for misconfiguration",
    "helm.upgrade": "Run `helm upgrade`",
    "helm.rollback": "Run `helm rollback`",
    "argocd.sync": "Trigger an ArgoCD sync",
    "argocd.rollback": "Roll back an ArgoCD application",
    "kubectl.get": "Read cluster resource state",
    "kubectl.logs": "Read the tail of a pod's logs plus its error lines (previous=true for the last crashed container)",
    "kubectl.events": "List recent Kubernetes events (scheduling, image pull, crash-loop, probe failures)",
    "kubectl.describe": "Describe a resource: status, conditions, restart reasons, events",
    "prometheus.targets": "List Prometheus scrape targets and whether each is up",
    "registry.list": "List the images and tags in the container registry",
    "pagerduty.list": "List PagerDuty incidents (default: triggered and acknowledged)",
    "kubectl.restart": "Restart a pod/deployment",
    "kubectl.scale": "Scale a deployment/HPA",
    "http.smoke_test": "Run smoke-test HTTP requests against a service",
    "prometheus.query": "Query a Prometheus metric",
    "alertmanager.read": "Read the firing alert payload",
    "pagerduty.read": "Read the linked PagerDuty incident",
    "terraform.plan": "Run `terraform plan`",
    "terraform.apply": "Run `terraform apply`",
    "cloud.cost_explorer": "Query cloud cost/usage data",
    "runs.read": "Read prior run history",
    "slack.post": "Post a message to a Slack channel",
}


class SimulatedTool(Tool):
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.payload_fn = PAYLOAD_BUILDERS.get(name)

    async def run(self, **kwargs: Any) -> ToolResult:
        extra: dict[str, Any] = {}
        note = "Stub result — wire a real integration in app/tools to replace this."
        if self.payload_fn:
            extra = self.payload_fn(kwargs, seeded_random(kwargs))
            note = "Simulated data, deterministic per input — not a real scan/query."

        return ToolResult(
            ok=True,
            data={
                "simulated": True,
                "tool": self.name,
                "description": self.description,
                "args": kwargs,
                **extra,
                "note": note,
            },
        )
