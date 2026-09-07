# Cost-Sweep Real Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 5 simulated tool calls the `cost-sweep` flightplan actually uses (`cloud.cost_explorer`, `kubectl.get`, `prometheus.query`, `git.diff`, `terraform.plan`) with real implementations, without touching the other 23 tools in `runtime/app/tools/stubs.py` or either agent module's own logic.

**Architecture:** Add a `TOOLS_REAL_ENABLED` env-var switch to `runtime/app/tools/registry.py` that overlays real `Tool` subclasses on top of the existing `SimulatedTool` map, one per real tool, added incrementally task by task. `cost_analyzer.py` and `terraform_plan.py` need **zero changes** — they already call tools by name through `AgentContext.call`, so swapping the registry is transparent to them. Real tools target infrastructure that already exists locally: the live `kind-opssquad` cluster for `kubectl.get`, a new minimal Prometheus deployment in that same cluster for `prometheus.query`, the `floci` AWS emulator (container `floci`, currently stopped) for `cloud.cost_explorer`, and the already-applied Terraform root in the sibling `VisitorCounter` repo (`terraform/environments/dev`, floci-backed) for `git.diff` and `terraform.plan`.

**Tech Stack:** Python 3.14, FastAPI/asyncio (existing), `boto3` (new, AWS SDK against floci), `kubernetes` (new, official k8s Python client), `httpx` (existing dep, used for Prometheus HTTP API), `subprocess`/`asyncio.create_subprocess_exec` for `git` and `terraform` CLIs (no new dep — both binaries already installed).

**Spec:** This document — no separate spec file; scope and decisions were captured through a `grillme` interview in this session (see conversation history).

## Global Constraints

- Do not modify `runtime/app/tools/stubs.py`, `runtime/app/tools/simulate.py`, `runtime/app/agents/cost_analyzer.py`, or `runtime/app/agents/terraform_plan.py`. Real tools slot into the registry only.
- Default (`TOOLS_REAL_ENABLED` unset or `false`) must keep 100% simulated behavior — nothing about existing runs changes unless the flag is explicitly set.
- Real tools accept the same generic `**kwargs` shape the agents already pass (e.g. `{"min_idle_days": 30}` for cost-analyzer's calls, `{"mode": "rightsizing", "open_pr": true}` for terraform-plan's calls) — none of these flightplan params name a resource/repo/namespace, so every real tool gets its actual target (AWS endpoint, k8s namespace, repo path, terraform dir) from `Settings`, not from kwargs.
- `open_pr: true` (terraform-plan step) stays unimplemented this round — no PR-creation tool exists in the registry. Every real tool must accept and ignore unknown kwargs like `open_pr` rather than erroring on them.
- floci's storage mode is `memory` (confirmed from its own startup log) even though a Docker volume is mounted at `/app/data` — every floci restart wipes all AWS state, including the Terraform state S3 bucket. Any task that (re)starts floci must also re-bootstrap Terraform state.
- All sync SDK calls (`boto3`, `kubernetes` client) run inside `asyncio.to_thread` — the tool layer is async, these clients are not.

---

## File Structure

- `runtime/app/config.py` — modified across tasks to add: `tools_real_enabled`, `aws_endpoint_url`/`aws_region`/`aws_access_key_id`/`aws_secret_access_key`, `k8s_context`/`k8s_namespace`, `prometheus_url`, `git_target_repo`, `terraform_target_dir`.
- `runtime/app/tools/registry.py` — modified across tasks: adds the `TOOLS_REAL_ENABLED` overlay mechanism, then one registration line per real tool.
- `runtime/app/tools/aws_cost_explorer.py` — new, `AwsCostExplorerTool`.
- `runtime/app/tools/k8s_get.py` — new, `K8sGetTool`.
- `runtime/app/tools/prometheus_query.py` — new, `PrometheusQueryTool`.
- `runtime/app/tools/git_diff.py` — new, `GitDiffTool`.
- `runtime/app/tools/terraform_plan.py` — new, `TerraformPlanTool` (lives in `app/tools/`, distinct from the existing `app/agents/terraform_plan.py` — different package, same basename, matches this repo's existing flat per-tool-concern file layout).
- `runtime/requirements.txt` — adds `boto3`, `kubernetes`.
- `k8s/40-prometheus.yaml` — new, minimal Prometheus (ServiceAccount + ClusterRole + ClusterRoleBinding + ConfigMap + Deployment + Service) scraping node cAdvisor in the `opssquad` namespace.
- `scripts/bootstrap-floci.sh` — new, restarts floci and re-applies the two VisitorCounter Terraform roots that depend on it (needed because of the memory-storage constraint above).
- `.env.example` — documents every new env var.
- Self-checks (no pytest in this repo today — matching existing convention, plain `assert`-based scripts run directly): `runtime/app/tools/check_registry.py`, `runtime/app/tools/check_aws_cost_explorer.py`, `runtime/app/tools/check_k8s_get.py`, `runtime/app/tools/check_prometheus_query.py`, `runtime/app/tools/check_git_diff.py`, `runtime/app/tools/check_terraform_plan.py`.

---

### Task 1: Real-tool registry switch

**Files:**
- Modify: `runtime/app/config.py`
- Modify: `runtime/app/tools/registry.py`
- Test: `runtime/app/tools/check_registry.py`

**Interfaces:**
- Produces: `settings.tools_real_enabled: bool` (env `TOOLS_REAL_ENABLED`, default `False`). `registry._real_tools() -> dict[str, Tool]` — every later task adds one entry here.

- [ ] **Step 1: Write the failing check**

Create `runtime/app/tools/check_registry.py`:

```python
"""Self-check: TOOL_REGISTRY honors TOOLS_REAL_ENABLED.
Run: python app/tools/check_registry.py   (from the runtime/ directory)
"""
import os
import subprocess
import sys
from pathlib import Path

RUNTIME_DIR = Path(__file__).resolve().parents[2]  # runtime/

CHECK_SNIPPET = (
    "from app.tools.registry import get_tool, TOOL_REGISTRY\n"
    "t = get_tool('cloud.cost_explorer')\n"
    "print(type(t).__name__, len(TOOL_REGISTRY))\n"
)


def run_with(tools_real_enabled: str) -> str:
    env = {**os.environ, "TOOLS_REAL_ENABLED": tools_real_enabled}
    result = subprocess.run(
        [sys.executable, "-c", CHECK_SNIPPET],
        cwd=str(RUNTIME_DIR),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


if __name__ == "__main__":
    out_off = run_with("false")
    assert out_off.startswith("SimulatedTool"), out_off
    print(f"ok: TOOLS_REAL_ENABLED=false -> {out_off}")

    out_on = run_with("true")
    assert out_on.startswith("SimulatedTool"), out_on  # no real tools registered yet at this task
    print(f"ok: TOOLS_REAL_ENABLED=true, no real tools yet -> {out_on}")
```

- [ ] **Step 2: Run it to confirm it fails (registry.py doesn't read the flag yet)**

Run: `cd runtime && python app/tools/check_registry.py`
Expected: `AssertionError` — `TOOLS_REAL_ENABLED` doesn't exist on `Settings` yet, `result.returncode != 0`.

- [ ] **Step 3: Add the setting**

Edit `runtime/app/config.py`, add one field to `Settings`:

```python
class Settings(BaseSettings):
    database_url: str = "postgresql://opssquad:change-me@localhost:5432/opssquad"
    redis_url: str = "redis://localhost:6379"
    jwt_secret: str = "change-me-to-a-long-random-string"
    jwt_algorithm: str = "HS256"
    anthropic_api_key: str = ""
    intent_router_model: str = "claude-haiku-4-5-20251001"
    agent_model: str = "claude-sonnet-5"
    mutating_agents_enabled: bool = True
    tools_real_enabled: bool = False
```

- [ ] **Step 4: Add the overlay mechanism**

Replace `runtime/app/tools/registry.py` with:

```python
from app.config import settings
from app.tools.base import Tool
from app.tools.stubs import SIMULATED_TOOLS, SimulatedTool

TOOL_REGISTRY: dict[str, Tool] = {
    name: SimulatedTool(name, desc) for name, desc in SIMULATED_TOOLS.items()
}


def _real_tools() -> dict[str, Tool]:
    """Real Tool instances, keyed by tool name. Each is added by the task
    that implements it — imports stay local so a tool's SDK (boto3,
    kubernetes) is only required once that tool is actually enabled."""
    return {}


if settings.tools_real_enabled:
    TOOL_REGISTRY.update(_real_tools())


def get_tool(name: str) -> Tool:
    tool = TOOL_REGISTRY.get(name)
    if tool is None:
        raise KeyError(f"Unknown tool '{name}'")
    return tool
```

- [ ] **Step 5: Run the check again to confirm it passes**

Run: `cd runtime && python app/tools/check_registry.py`
Expected:
```
ok: TOOLS_REAL_ENABLED=false -> SimulatedTool 28
ok: TOOLS_REAL_ENABLED=true, no real tools yet -> SimulatedTool 28
```

- [ ] **Step 6: Commit**

```bash
git add runtime/app/config.py runtime/app/tools/registry.py runtime/app/tools/check_registry.py
git commit -m "feat: add TOOLS_REAL_ENABLED switch to the tool registry"
```

---

### Task 2: floci bootstrap script + real `cloud.cost_explorer`

**Files:**
- Create: `scripts/bootstrap-floci.sh`
- Create: `runtime/app/tools/aws_cost_explorer.py`
- Modify: `runtime/app/config.py`
- Modify: `runtime/app/tools/registry.py`
- Modify: `runtime/requirements.txt`
- Modify: `.env.example`
- Test: `runtime/app/tools/check_aws_cost_explorer.py`

**Interfaces:**
- Consumes: `settings.tools_real_enabled` (Task 1).
- Produces: `AwsCostExplorerTool` class in `app/tools/aws_cost_explorer.py`, registered under `"cloud.cost_explorer"`. Output shape matches the simulated one: `{"idle_resources": [{"type", "id", "monthly_cost_usd"}], "monthly_savings_usd": float}`.

- [ ] **Step 1: Write the bootstrap script**

Create `scripts/bootstrap-floci.sh`:

```bash
#!/usr/bin/env bash
# Restarts floci (the local AWS emulator) and re-applies the two Terraform
# roots in the sibling VisitorCounter repo that live inside it.
#
# floci runs in memory-only storage mode (confirmed via `docker logs floci`),
# so every restart wipes all AWS state, including the S3 bucket Terraform
# uses as its backend. Re-running this script after any floci restart is
# what makes `terraform plan`/`cloud.cost_explorer` see a consistent world
# again instead of drift against state that no longer exists.
set -euo pipefail

VC_TF="${VC_TF:-$HOME/Projects/VisitorCounter/terraform}"

docker start floci >/dev/null
echo "waiting for floci..."
for _ in $(seq 1 30); do
  if curl -sf http://localhost:4566/_floci/health >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
curl -sf http://localhost:4566/_floci/health >/dev/null || { echo "floci never became healthy" >&2; exit 1; }

echo "bootstrapping terraform state bucket..."
(cd "$VC_TF/01-bootstrap-state" && terraform init -reconfigure -input=false && terraform apply -auto-approve)

echo "applying environments/dev..."
(cd "$VC_TF/environments/dev" && terraform init -reconfigure -input=false && terraform apply -auto-approve)

echo "floci bootstrap complete."
```

```bash
chmod +x scripts/bootstrap-floci.sh
```

- [ ] **Step 2: Run it and confirm floci + Terraform state come up clean**

Run: `./scripts/bootstrap-floci.sh`
Expected: ends with `floci bootstrap complete.`, no Terraform errors. (If `floci` was already running with live state, this is idempotent — `apply` reports `No changes.` for resources that still exist.)

- [ ] **Step 3: Write the failing check for the real tool**

Create `runtime/app/tools/check_aws_cost_explorer.py`:

```python
"""Self-check: AwsCostExplorerTool makes real boto3 calls against floci.
Requires floci running (scripts/bootstrap-floci.sh).
Run: python app/tools/check_aws_cost_explorer.py   (from runtime/)
"""
import asyncio

from app.tools.aws_cost_explorer import AwsCostExplorerTool


async def main():
    tool = AwsCostExplorerTool()
    result = await tool.run(min_idle_days=30)
    assert result.ok, result.error
    data = result.data
    assert data["simulated"] is False
    assert isinstance(data["idle_resources"], list)
    assert isinstance(data["monthly_savings_usd"], (int, float))
    assert data["monthly_savings_usd"] == sum(r["monthly_cost_usd"] for r in data["idle_resources"])
    print(f"ok: {len(data['idle_resources'])} idle resource(s), ${data['monthly_savings_usd']}/mo")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run it to confirm it fails (module doesn't exist yet)**

Run: `cd runtime && python app/tools/check_aws_cost_explorer.py`
Expected: `ModuleNotFoundError: No module named 'app.tools.aws_cost_explorer'`

- [ ] **Step 5: Add AWS settings**

Edit `runtime/app/config.py`, add to `Settings`:

```python
    aws_endpoint_url: str = "http://localhost:4566"
    aws_region: str = "us-east-1"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
```

- [ ] **Step 6: Add `boto3` to requirements**

Edit `runtime/requirements.txt`, add:

```
boto3==1.35.36
```

Run: `cd runtime && pip install -r requirements.txt`

- [ ] **Step 7: Implement the real tool**

Create `runtime/app/tools/aws_cost_explorer.py`:

```python
"""Real cloud.cost_explorer — queries floci (local AWS emulator) for idle
resources. floci's account starts empty; VisitorCounter's applied Terraform
only creates VPC/subnet/S3/IAM resources (no EC2/EBS/EIP), so an empty
idle_resources list is the correct, honest answer today — not a bug.

monthly_cost_usd uses small static per-resource-type rate approximations
(not a live pricing lookup) — good enough for a rightsizing demo, not a
real bill.
"""
import asyncio
from typing import Any

from app.config import settings
from app.tools.base import Tool, ToolResult

_EBS_GP3_USD_PER_GB_MONTH = 0.08
_UNASSOCIATED_EIP_USD_PER_MONTH = 3.6
_STOPPED_INSTANCE_USD_PER_MONTH = 30.0


def _client(service: str):
    import boto3

    return boto3.client(
        service,
        endpoint_url=settings.aws_endpoint_url,
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
    )


def _idle_ebs_volumes(ec2) -> list[dict[str, Any]]:
    resp = ec2.describe_volumes(Filters=[{"Name": "status", "Values": ["available"]}])
    return [
        {
            "type": "EBS volume",
            "id": v["VolumeId"],
            "monthly_cost_usd": round(v["Size"] * _EBS_GP3_USD_PER_GB_MONTH, 2),
        }
        for v in resp["Volumes"]
    ]


def _unattached_eips(ec2) -> list[dict[str, Any]]:
    resp = ec2.describe_addresses()
    return [
        {"type": "unattached Elastic IP", "id": a["AllocationId"], "monthly_cost_usd": _UNASSOCIATED_EIP_USD_PER_MONTH}
        for a in resp["Addresses"]
        if "InstanceId" not in a
    ]


def _stopped_instances(ec2) -> list[dict[str, Any]]:
    resp = ec2.describe_instances(Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}])
    return [
        {"type": "idle EC2 instance", "id": i["InstanceId"], "monthly_cost_usd": _STOPPED_INSTANCE_USD_PER_MONTH}
        for r in resp["Reservations"]
        for i in r["Instances"]
    ]


class AwsCostExplorerTool(Tool):
    name = "cloud.cost_explorer"

    async def run(self, **kwargs: Any) -> ToolResult:
        return await asyncio.to_thread(self._run_sync)

    def _run_sync(self) -> ToolResult:
        ec2 = _client("ec2")
        idle = _idle_ebs_volumes(ec2) + _unattached_eips(ec2) + _stopped_instances(ec2)
        savings = round(sum(r["monthly_cost_usd"] for r in idle), 2)
        return ToolResult(
            ok=True,
            data={
                "simulated": False,
                "tool": self.name,
                "idle_resources": idle,
                "monthly_savings_usd": savings,
                "note": "Real query against floci. monthly_cost_usd values are static rate approximations, not live pricing.",
            },
        )
```

- [ ] **Step 8: Run the check to confirm it passes**

Run: `cd runtime && python app/tools/check_aws_cost_explorer.py`
Expected: `ok: 0 idle resource(s), $0/mo` (VisitorCounter's Terraform doesn't create any EC2/EBS/EIP resources — this is the correct current answer).

- [ ] **Step 9: Register it**

Edit `runtime/app/tools/registry.py`, update `_real_tools`:

```python
def _real_tools() -> dict[str, Tool]:
    """Real Tool instances, keyed by tool name. Each is added by the task
    that implements it — imports stay local so a tool's SDK (boto3,
    kubernetes) is only required once that tool is actually enabled."""
    from app.tools.aws_cost_explorer import AwsCostExplorerTool

    return {
        "cloud.cost_explorer": AwsCostExplorerTool(),
    }
```

- [ ] **Step 10: Update `runtime/app/tools/check_registry.py`'s CHECK_SNIPPET expectation for the "on" case**

Edit `runtime/app/tools/check_registry.py`, change the last assertion block to:

```python
    out_on = run_with("true")
    assert out_on.startswith("AwsCostExplorerTool"), out_on
    print(f"ok: TOOLS_REAL_ENABLED=true -> {out_on}")
```

Run: `cd runtime && python app/tools/check_registry.py` — expect both lines to print `ok:`.

- [ ] **Step 11: Document the new env vars**

Append to `.env.example`:

```
# ── Real tools (cost-sweep) ────────────────────────────
TOOLS_REAL_ENABLED=false
AWS_ENDPOINT_URL=http://localhost:4566
AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=test
AWS_SECRET_ACCESS_KEY=test
```

- [ ] **Step 12: Commit**

```bash
git add scripts/bootstrap-floci.sh runtime/app/tools/aws_cost_explorer.py runtime/app/tools/check_aws_cost_explorer.py \
        runtime/app/config.py runtime/app/tools/registry.py runtime/app/tools/check_registry.py \
        runtime/requirements.txt .env.example
git commit -m "feat: real cloud.cost_explorer against floci + floci bootstrap script"
```

---

### Task 3: Real `kubectl.get`

**Files:**
- Create: `runtime/app/tools/k8s_get.py`
- Modify: `runtime/app/config.py`
- Modify: `runtime/app/tools/registry.py`
- Modify: `runtime/requirements.txt`
- Modify: `.env.example`
- Test: `runtime/app/tools/check_k8s_get.py`

**Interfaces:**
- Consumes: `settings.tools_real_enabled` (Task 1).
- Produces: `K8sGetTool` in `app/tools/k8s_get.py`, registered under `"kubectl.get"`. Output shape matches simulated: `{"replicas_desired": int, "replicas_ready": int}`, plus extra real fields (`namespace`, `deployments`).

- [ ] **Step 1: Write the failing check**

Create `runtime/app/tools/check_k8s_get.py`:

```python
"""Self-check: K8sGetTool reads real Deployments from kind-opssquad.
Requires the kind-opssquad cluster running with its usual 6 deployments
(bff, control, frontend, postgres, redis, runtime) each at 1/1.
Run: python app/tools/check_k8s_get.py   (from runtime/)
"""
import asyncio

from app.tools.k8s_get import K8sGetTool


async def main():
    tool = K8sGetTool()
    result = await tool.run(min_idle_days=30)
    assert result.ok, result.error
    data = result.data
    assert data["simulated"] is False
    assert data["namespace"] == "opssquad"
    assert data["replicas_desired"] >= 6, data
    assert data["replicas_ready"] == data["replicas_desired"], data
    assert "runtime" in data["deployments"], data
    print(f"ok: {data['replicas_ready']}/{data['replicas_desired']} replicas ready across {data['deployments']}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd runtime && python app/tools/check_k8s_get.py`
Expected: `ModuleNotFoundError: No module named 'app.tools.k8s_get'`

- [ ] **Step 3: Add k8s settings**

Edit `runtime/app/config.py`, add to `Settings`:

```python
    k8s_context: str = "kind-opssquad"
    k8s_namespace: str = "opssquad"
```

- [ ] **Step 4: Add `kubernetes` to requirements**

Edit `runtime/requirements.txt`, add:

```
kubernetes==31.0.0
```

Run: `cd runtime && pip install -r requirements.txt`

- [ ] **Step 5: Implement the real tool**

Create `runtime/app/tools/k8s_get.py`:

```python
"""Real kubectl.get — reads Deployment state from the live kind-opssquad
cluster via the official Python k8s client (no shelling out to kubectl).
"""
import asyncio
from typing import Any

from app.config import settings
from app.tools.base import Tool, ToolResult


class K8sGetTool(Tool):
    name = "kubectl.get"

    async def run(self, **kwargs: Any) -> ToolResult:
        return await asyncio.to_thread(self._run_sync)

    def _run_sync(self) -> ToolResult:
        from kubernetes import client, config as k8s_config

        k8s_config.load_kube_config(context=settings.k8s_context)
        apps = client.AppsV1Api()
        deployments = apps.list_namespaced_deployment(settings.k8s_namespace).items

        desired = sum(d.spec.replicas or 0 for d in deployments)
        ready = sum(d.status.ready_replicas or 0 for d in deployments)

        return ToolResult(
            ok=True,
            data={
                "simulated": False,
                "tool": self.name,
                "namespace": settings.k8s_namespace,
                "replicas_desired": desired,
                "replicas_ready": ready,
                "deployments": [d.metadata.name for d in deployments],
            },
        )
```

- [ ] **Step 6: Run the check to confirm it passes**

Run: `cd runtime && python app/tools/check_k8s_get.py`
Expected: `ok: 6/6 replicas ready across [...]`

- [ ] **Step 7: Register it**

Edit `runtime/app/tools/registry.py`, update `_real_tools`:

```python
def _real_tools() -> dict[str, Tool]:
    """Real Tool instances, keyed by tool name. Each is added by the task
    that implements it — imports stay local so a tool's SDK (boto3,
    kubernetes) is only required once that tool is actually enabled."""
    from app.tools.aws_cost_explorer import AwsCostExplorerTool
    from app.tools.k8s_get import K8sGetTool

    return {
        "cloud.cost_explorer": AwsCostExplorerTool(),
        "kubectl.get": K8sGetTool(),
    }
```

- [ ] **Step 8: Document the new env vars**

Append to `.env.example` (under the "Real tools" section added in Task 2):

```
K8S_CONTEXT=kind-opssquad
K8S_NAMESPACE=opssquad
```

- [ ] **Step 9: Commit**

```bash
git add runtime/app/tools/k8s_get.py runtime/app/tools/check_k8s_get.py \
        runtime/app/config.py runtime/app/tools/registry.py runtime/requirements.txt .env.example
git commit -m "feat: real kubectl.get against kind-opssquad"
```

---

### Task 4: Minimal Prometheus + real `prometheus.query`

**Files:**
- Create: `k8s/40-prometheus.yaml`
- Create: `runtime/app/tools/prometheus_query.py`
- Modify: `runtime/app/config.py`
- Modify: `runtime/app/tools/registry.py`
- Modify: `.env.example`
- Test: `runtime/app/tools/check_prometheus_query.py`

**Interfaces:**
- Consumes: `settings.tools_real_enabled` (Task 1). Uses `httpx` (already a dependency).
- Produces: `PrometheusQueryTool` in `app/tools/prometheus_query.py`, registered under `"prometheus.query"`. Output includes `cpu_utilization` (matching the field cost-analyzer's simulated payload exposes) plus the raw Prometheus result.

- [ ] **Step 1: Write the Prometheus manifest**

Create `k8s/40-prometheus.yaml`:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: prometheus
  namespace: opssquad
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: opssquad-prometheus
rules:
  - apiGroups: [""]
    resources: ["nodes", "nodes/proxy", "nodes/metrics", "pods"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: opssquad-prometheus
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: opssquad-prometheus
subjects:
  - kind: ServiceAccount
    name: prometheus
    namespace: opssquad
---
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-config
  namespace: opssquad
data:
  prometheus.yml: |
    global:
      scrape_interval: 15s
    scrape_configs:
      - job_name: kubernetes-cadvisor
        scheme: https
        tls_config:
          ca_file: /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
          insecure_skip_verify: true
        bearer_token_file: /var/run/secrets/kubernetes.io/serviceaccount/token
        kubernetes_sd_configs:
          - role: node
        relabel_configs:
          - action: labelmap
            regex: __meta_kubernetes_node_label_(.+)
          - target_label: __address__
            replacement: kubernetes.default.svc:443
          - source_labels: [__meta_kubernetes_node_name]
            regex: (.+)
            target_label: __metrics_path__
            replacement: /api/v1/nodes/${1}/proxy/metrics/cadvisor
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: prometheus
  namespace: opssquad
spec:
  replicas: 1
  selector:
    matchLabels: { app: prometheus }
  template:
    metadata:
      labels: { app: prometheus }
    spec:
      serviceAccountName: prometheus
      containers:
        - name: prometheus
          image: prom/prometheus:v2.55.1
          args: ["--config.file=/etc/prometheus/prometheus.yml"]
          ports:
            - containerPort: 9090
          volumeMounts:
            - name: config
              mountPath: /etc/prometheus
      volumes:
        - name: config
          configMap: { name: prometheus-config }
---
apiVersion: v1
kind: Service
metadata:
  name: prometheus
  namespace: opssquad
spec:
  selector: { app: prometheus }
  ports:
    - port: 9090
      targetPort: 9090
```

- [ ] **Step 2: Apply it and confirm it comes up**

Run:
```bash
kubectl --context kind-opssquad apply -f k8s/40-prometheus.yaml
kubectl --context kind-opssquad -n opssquad rollout status deployment/prometheus --timeout=60s
```
Expected: `deployment "prometheus" successfully rolled out`

- [ ] **Step 3: Port-forward and sanity check it's scraping**

Run (background): `kubectl --context kind-opssquad -n opssquad port-forward svc/prometheus 9090:9090 &`
Then: `curl -s 'http://localhost:9090/api/v1/query?query=up' | head -c 300`
Expected: JSON with `"status":"success"` and at least one result (the cAdvisor target).

- [ ] **Step 4: Write the failing check for the real tool**

Create `runtime/app/tools/check_prometheus_query.py`:

```python
"""Self-check: PrometheusQueryTool makes a real HTTP query.
Requires k8s/40-prometheus.yaml applied and port-forwarded to localhost:9090
(kubectl --context kind-opssquad -n opssquad port-forward svc/prometheus 9090:9090).
Run: python app/tools/check_prometheus_query.py   (from runtime/)
"""
import asyncio

from app.tools.prometheus_query import PrometheusQueryTool


async def main():
    tool = PrometheusQueryTool()
    result = await tool.run(min_idle_days=30)
    assert result.ok, result.error
    data = result.data
    assert data["simulated"] is False
    assert isinstance(data["cpu_utilization"], (int, float))
    assert 0.0 <= data["cpu_utilization"] <= 1.0, data
    print(f"ok: cpu_utilization={data['cpu_utilization']}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: Run it to confirm it fails**

Run: `cd runtime && python app/tools/check_prometheus_query.py`
Expected: `ModuleNotFoundError: No module named 'app.tools.prometheus_query'`

- [ ] **Step 6: Add the Prometheus URL setting**

Edit `runtime/app/config.py`, add to `Settings`:

```python
    prometheus_url: str = "http://localhost:9090"
```

- [ ] **Step 7: Implement the real tool**

Create `runtime/app/tools/prometheus_query.py`:

```python
"""Real prometheus.query — queries the in-cluster Prometheus (k8s/40-prometheus.yaml)
over its HTTP API. cost-analyzer's call site doesn't pass a `query` kwarg (its
flightplan step only sends {"min_idle_days": 30}), so this tool defaults to an
average node CPU-utilization query when the caller doesn't supply one.
"""
from typing import Any

import httpx

from app.config import settings
from app.tools.base import Tool, ToolResult

_DEFAULT_QUERY = (
    "avg(rate(container_cpu_usage_seconds_total{id=\"/\"}[5m]))"
)


class PrometheusQueryTool(Tool):
    name = "prometheus.query"

    async def run(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query", _DEFAULT_QUERY)
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{settings.prometheus_url}/api/v1/query", params={"query": query})
        resp.raise_for_status()
        payload = resp.json()

        result = payload.get("data", {}).get("result", [])
        cpu_utilization = float(result[0]["value"][1]) if result else 0.0

        return ToolResult(
            ok=True,
            data={
                "simulated": False,
                "tool": self.name,
                "query": query,
                "cpu_utilization": round(cpu_utilization, 4),
                "raw_result": result,
            },
        )
```

- [ ] **Step 8: Run the check to confirm it passes**

Run: `cd runtime && python app/tools/check_prometheus_query.py`
Expected: `ok: cpu_utilization=<some float between 0 and 1>`

- [ ] **Step 9: Register it**

Edit `runtime/app/tools/registry.py`, update `_real_tools`:

```python
def _real_tools() -> dict[str, Tool]:
    """Real Tool instances, keyed by tool name. Each is added by the task
    that implements it — imports stay local so a tool's SDK (boto3,
    kubernetes) is only required once that tool is actually enabled."""
    from app.tools.aws_cost_explorer import AwsCostExplorerTool
    from app.tools.k8s_get import K8sGetTool
    from app.tools.prometheus_query import PrometheusQueryTool

    return {
        "cloud.cost_explorer": AwsCostExplorerTool(),
        "kubectl.get": K8sGetTool(),
        "prometheus.query": PrometheusQueryTool(),
    }
```

- [ ] **Step 10: Document the new env var**

Append to `.env.example`:

```
PROMETHEUS_URL=http://localhost:9090
```

- [ ] **Step 11: Commit**

```bash
git add k8s/40-prometheus.yaml runtime/app/tools/prometheus_query.py runtime/app/tools/check_prometheus_query.py \
        runtime/app/config.py runtime/app/tools/registry.py .env.example
git commit -m "feat: minimal in-cluster Prometheus + real prometheus.query"
```

---

### Task 5: Real `git.diff`

**Files:**
- Create: `runtime/app/tools/git_diff.py`
- Modify: `runtime/app/config.py`
- Modify: `runtime/app/tools/registry.py`
- Modify: `.env.example`
- Test: `runtime/app/tools/check_git_diff.py`

**Interfaces:**
- Consumes: `settings.tools_real_enabled` (Task 1).
- Produces: `GitDiffTool` in `app/tools/git_diff.py`, registered under `"git.diff"`.

- [ ] **Step 1: Write the failing check**

Create `runtime/app/tools/check_git_diff.py`:

```python
"""Self-check: GitDiffTool runs real `git diff` against the configured repo.
Run: python app/tools/check_git_diff.py   (from runtime/)
"""
import asyncio

from app.tools.git_diff import GitDiffTool


async def main():
    tool = GitDiffTool()
    result = await tool.run(mode="rightsizing", open_pr=True)
    assert result.ok, result.error
    data = result.data
    assert data["simulated"] is False
    assert data["repo"].endswith("VisitorCounter")
    assert isinstance(data["diff_stat"], str)
    assert isinstance(data["files_changed"], int)
    print(f"ok: {data['files_changed']} file(s) changed in {data['repo']}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd runtime && python app/tools/check_git_diff.py`
Expected: `ModuleNotFoundError: No module named 'app.tools.git_diff'`

- [ ] **Step 3: Add the repo path setting**

Edit `runtime/app/config.py`, add to `Settings`:

```python
    git_target_repo: str = "/home/rupeshkumar/Projects/VisitorCounter"
```

- [ ] **Step 4: Implement the real tool**

Create `runtime/app/tools/git_diff.py`:

```python
"""Real git.diff — terraform-plan's flightplan step doesn't pass a repo/commit
(its `with:` block only has {"mode": "rightsizing", "open_pr": true}), so the
target repo comes from settings, not kwargs: the VisitorCounter repo, since
that's where the Terraform this flightplan plans against actually lives.
"""
import asyncio
from typing import Any

from app.config import settings
from app.tools.base import Tool, ToolResult


class GitDiffTool(Tool):
    name = "git.diff"

    async def run(self, **kwargs: Any) -> ToolResult:
        proc = await asyncio.create_subprocess_exec(
            "git", "diff", "HEAD~1", "--stat",
            cwd=settings.git_target_repo,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return ToolResult(ok=False, error=stderr.decode())

        diff_stat = stdout.decode()
        return ToolResult(
            ok=True,
            data={
                "simulated": False,
                "tool": self.name,
                "repo": settings.git_target_repo,
                "diff_stat": diff_stat,
                "files_changed": sum(1 for line in diff_stat.splitlines() if "|" in line),
            },
        )
```

- [ ] **Step 5: Run the check to confirm it passes**

Run: `cd runtime && python app/tools/check_git_diff.py`
Expected: `ok: <N> file(s) changed in /home/rupeshkumar/Projects/VisitorCounter`

- [ ] **Step 6: Register it**

Edit `runtime/app/tools/registry.py`, update `_real_tools`:

```python
def _real_tools() -> dict[str, Tool]:
    """Real Tool instances, keyed by tool name. Each is added by the task
    that implements it — imports stay local so a tool's SDK (boto3,
    kubernetes) is only required once that tool is actually enabled."""
    from app.tools.aws_cost_explorer import AwsCostExplorerTool
    from app.tools.k8s_get import K8sGetTool
    from app.tools.prometheus_query import PrometheusQueryTool
    from app.tools.git_diff import GitDiffTool

    return {
        "cloud.cost_explorer": AwsCostExplorerTool(),
        "kubectl.get": K8sGetTool(),
        "prometheus.query": PrometheusQueryTool(),
        "git.diff": GitDiffTool(),
    }
```

- [ ] **Step 7: Document the new env var**

Append to `.env.example`:

```
GIT_TARGET_REPO=/home/rupeshkumar/Projects/VisitorCounter
```

- [ ] **Step 8: Commit**

```bash
git add runtime/app/tools/git_diff.py runtime/app/tools/check_git_diff.py \
        runtime/app/config.py runtime/app/tools/registry.py .env.example
git commit -m "feat: real git.diff against the VisitorCounter repo"
```

---

### Task 6: Real `terraform.plan`

**Files:**
- Create: `runtime/app/tools/terraform_plan.py`
- Modify: `runtime/app/config.py`
- Modify: `runtime/app/tools/registry.py`
- Modify: `.env.example`
- Test: `runtime/app/tools/check_terraform_plan.py`

**Interfaces:**
- Consumes: `settings.tools_real_enabled` (Task 1). Requires `scripts/bootstrap-floci.sh` (Task 2) to have been run so `VisitorCounter/terraform/environments/dev` has real, applied state to plan against.
- Produces: `TerraformPlanTool` in `app/tools/terraform_plan.py`, registered under `"terraform.plan"`. Output shape matches simulated: `{"to_add": int, "to_change": int, "to_destroy": int}`.

- [ ] **Step 1: Write the failing check**

Create `runtime/app/tools/check_terraform_plan.py`:

```python
"""Self-check: TerraformPlanTool runs real `terraform plan`.
Requires scripts/bootstrap-floci.sh to have been run (floci up, environments/dev applied).
Run: python app/tools/check_terraform_plan.py   (from runtime/)
"""
import asyncio

from app.tools.terraform_plan import TerraformPlanTool


async def main():
    tool = TerraformPlanTool()
    result = await tool.run(mode="rightsizing", open_pr=True)
    assert result.ok, result.error
    data = result.data
    assert data["simulated"] is False
    for key in ("to_add", "to_change", "to_destroy"):
        assert isinstance(data[key], int), data
    # environments/dev was just applied by bootstrap-floci.sh, so a plan
    # right after should show no drift.
    assert data["to_add"] == 0 and data["to_change"] == 0 and data["to_destroy"] == 0, data
    print(f"ok: +{data['to_add']} ~{data['to_change']} -{data['to_destroy']}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd runtime && python app/tools/check_terraform_plan.py`
Expected: `ModuleNotFoundError: No module named 'app.tools.terraform_plan'`

- [ ] **Step 3: Add the terraform dir setting**

Edit `runtime/app/config.py`, add to `Settings`:

```python
    terraform_target_dir: str = "/home/rupeshkumar/Projects/VisitorCounter/terraform/environments/dev"
```

- [ ] **Step 4: Implement the real tool**

Create `runtime/app/tools/terraform_plan.py`:

```python
"""Real terraform.plan — shells out to the `terraform` CLI against the
already-applied, floci-backed root in the sibling VisitorCounter repo
(terraform/environments/dev). Never applies — matches the simulated
behavior and the agent's own contract (app/agents/terraform_plan.py).
"""
import asyncio
import re
from typing import Any

from app.config import settings
from app.tools.base import Tool, ToolResult

_PLAN_SUMMARY_RE = re.compile(r"Plan: (\d+) to add, (\d+) to change, (\d+) to destroy")


def _parse_plan_output(output: str) -> dict[str, int]:
    if "No changes." in output:
        return {"to_add": 0, "to_change": 0, "to_destroy": 0}
    match = _PLAN_SUMMARY_RE.search(output)
    if not match:
        raise RuntimeError(f"Could not parse terraform plan output:\n{output}")
    to_add, to_change, to_destroy = (int(x) for x in match.groups())
    return {"to_add": to_add, "to_change": to_change, "to_destroy": to_destroy}


class TerraformPlanTool(Tool):
    name = "terraform.plan"

    async def run(self, **kwargs: Any) -> ToolResult:
        proc = await asyncio.create_subprocess_exec(
            "terraform", "plan", "-no-color", "-input=false",
            cwd=settings.terraform_target_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return ToolResult(ok=False, error=stderr.decode())

        output = stdout.decode()
        summary = _parse_plan_output(output)
        return ToolResult(
            ok=True,
            data={
                "simulated": False,
                "tool": self.name,
                **summary,
                "raw_output_tail": output[-2000:],
                "note": f"Real `terraform plan` against {settings.terraform_target_dir}. open_pr is not implemented — no PR-creation tool exists yet.",
            },
        )
```

- [ ] **Step 5: Run the check to confirm it passes**

Run: `cd runtime && python app/tools/check_terraform_plan.py`
Expected: `ok: +0 ~0 -0`

- [ ] **Step 6: Register it**

Edit `runtime/app/tools/registry.py`, update `_real_tools`:

```python
def _real_tools() -> dict[str, Tool]:
    """Real Tool instances, keyed by tool name. Each is added by the task
    that implements it — imports stay local so a tool's SDK (boto3,
    kubernetes) is only required once that tool is actually enabled."""
    from app.tools.aws_cost_explorer import AwsCostExplorerTool
    from app.tools.k8s_get import K8sGetTool
    from app.tools.prometheus_query import PrometheusQueryTool
    from app.tools.git_diff import GitDiffTool
    from app.tools.terraform_plan import TerraformPlanTool

    return {
        "cloud.cost_explorer": AwsCostExplorerTool(),
        "kubectl.get": K8sGetTool(),
        "prometheus.query": PrometheusQueryTool(),
        "git.diff": GitDiffTool(),
        "terraform.plan": TerraformPlanTool(),
    }
```

- [ ] **Step 7: Document the new env var**

Append to `.env.example`:

```
TERRAFORM_TARGET_DIR=/home/rupeshkumar/Projects/VisitorCounter/terraform/environments/dev
```

- [ ] **Step 8: Commit**

```bash
git add runtime/app/tools/terraform_plan.py runtime/app/tools/check_terraform_plan.py \
        runtime/app/config.py runtime/app/tools/registry.py .env.example
git commit -m "feat: real terraform.plan against VisitorCounter's floci-backed dev environment"
```

---

### Task 7: End-to-end verification of the real cost-sweep flightplan

**Files:**
- Test: `runtime/app/tools/check_registry.py` (final assertion added)
- No new source files — this task proves Tasks 1-6 compose correctly and closes out the plan.

**Interfaces:**
- Consumes: every real tool from Tasks 2-6, all wired through the same `TOOLS_REAL_ENABLED` switch from Task 1.

- [ ] **Step 1: Extend the registry check to cover all 5 tools**

Edit `runtime/app/tools/check_registry.py`, replace `CHECK_SNIPPET` and the final assertions:

```python
CHECK_SNIPPET = (
    "from app.tools.registry import get_tool, TOOL_REGISTRY\n"
    "names = ['cloud.cost_explorer', 'kubectl.get', 'prometheus.query', 'git.diff', 'terraform.plan']\n"
    "print(','.join(type(get_tool(n)).__name__ for n in names), len(TOOL_REGISTRY))\n"
)
```

```python
if __name__ == "__main__":
    out_off = run_with("false")
    assert out_off.startswith("SimulatedTool,SimulatedTool,SimulatedTool,SimulatedTool,SimulatedTool"), out_off
    print(f"ok: TOOLS_REAL_ENABLED=false -> all simulated ({out_off})")

    out_on = run_with("true")
    expected = "AwsCostExplorerTool,K8sGetTool,PrometheusQueryTool,GitDiffTool,TerraformPlanTool"
    assert out_on.startswith(expected), out_on
    print(f"ok: TOOLS_REAL_ENABLED=true -> all real ({out_on})")
```

Run: `cd runtime && python app/tools/check_registry.py`
Expected: both `ok:` lines print, second one confirms all 5 real classes are wired and the other 23 tool names still resolve to `SimulatedTool` (registry length unchanged at 28).

- [ ] **Step 2: Run every per-tool check once more, in order, with real infra up**

```bash
cd runtime
./../scripts/bootstrap-floci.sh   # or: cd .. && ./scripts/bootstrap-floci.sh
kubectl --context kind-opssquad apply -f ../k8s/40-prometheus.yaml
kubectl --context kind-opssquad -n opssquad port-forward svc/prometheus 9090:9090 &
python app/tools/check_aws_cost_explorer.py
python app/tools/check_k8s_get.py
python app/tools/check_prometheus_query.py
python app/tools/check_git_diff.py
python app/tools/check_terraform_plan.py
```
Expected: five `ok:` lines, no exceptions.

- [ ] **Step 3: Run the actual flightplan end-to-end with real tools on**

Set `TOOLS_REAL_ENABLED=true` in `.env`, restart the `runtime` service (`docker compose restart runtime` or, against the kind cluster, `kubectl --context kind-opssquad -n opssquad rollout restart deployment/runtime`), then trigger `cost-sweep` the same way it's normally triggered (its own cron, or manually via the BFF's run-trigger endpoint / UI Flightplans page) and inspect the resulting `run_steps` rows / UI run detail for the `scan` and `propose` steps.

Expected: `scan` step output has `"simulated": false` on each of its three tool calls; `propose` step's `terraform.plan` output shows `+0 ~0 -0` (matching Step 2's baseline) and its `note` field mentions `open_pr is not implemented`.

- [ ] **Step 4: Commit**

```bash
git add runtime/app/tools/check_registry.py
git commit -m "test: verify all 5 cost-sweep tools swap to real implementations together"
```

---

## Deferred (explicitly out of scope for this plan)

- `open_pr: true` on the `terraform-plan` step — no PR-creation tool exists in the registry. `TerraformPlanTool` accepts and ignores the kwarg; its `note` field says so. Adding real PR creation (e.g. a `github.open_pr` tool using a GitHub token) is a separate, later plan.
- The other 23 simulated tools (`docker.*`, `trivy.scan`, `helm.*`, `argocd.*`, `pagerduty.read`, etc.) — untouched, stay simulated. Real-wiring any of them is a separate plan per the "one flightplan at a time" scope decision made in this session.
- `cloud.cost_explorer`'s per-resource `monthly_cost_usd` values are static rate approximations, not a live AWS Price List API lookup (floci does expose a `pricing` service — using it for real per-resource rates would be a natural follow-up, not required for this plan's scope).
