[← Back to Index](../INDEX.md) · [← Runtime](05-runtime.md)

# 08 — Tools: Real vs. Simulated

Every tool call an agent makes goes through `runtime/app/tools/registry.py`.
With `REAL_TOOLS_ENABLED=true` (**the default**), 28 of the 29 registered
tools are swapped for real implementations in
[`runtime/app/tools/real.py`](../../runtime/app/tools/real.py) — genuinely
shelling out or calling real APIs, not fake data. Only `runs.read` (reads
this app's own run history) has no separate "real" version — it's an
introspective query, not an external system call.

There's no real cloud account or "payments-api" deployment to point these
at, so each real tool is grounded against a **safe, self-contained target**
— see the "Real target" column.

## Full tool catalog

| Tool | Real? | Real target | Used by (agent) |
|---|:---:|---|---|
| `git.diff` / `git.log` | ✅ | A real git repo (`runtime/git-demo/`), rebuilt fresh each image build, with a genuine gitleaks-detectable fake key in its history | code-review, test-runner, terraform-plan, diagnose |
| `secrets.scan` | ✅ | Same git-demo repo | code-review |
| `lint.run` | ✅ | Real `ruff` against this app's own source | code-review |
| `test.select` / `test.run` | ✅ | A real, small `pytest` suite (`app/tests/`) | test-runner |
| `kubectl.get` / `kubectl.logs` | ✅ | This pod's own namespace (`opssquad`) | cost-analyzer, deploy, rollback, diagnose |
| `kubectl.restart` / `kubectl.scale` | ✅ **mutating** | This pod's own namespace — read targets default to the `runtime` deployment, mutating targets default to `redis` (stateless, safe to restart/scale, never the pod handling the request) | remediate |
| `docker.build` / `docker.tag` | ✅ **mutating** | `buildah` (rootless-ish, one narrow `CAP_SYS_ADMIN` grant, not `privileged: true`) building `runtime/docker-demo/` | build |
| `registry.push` / `registry.pull` | ✅ **mutating (push)** | A real, self-hosted in-cluster registry (`k8s/12-registry.yaml`) | build, security-scan |
| `trivy.scan` | ✅ | This container's own filesystem | security-scan |
| `iac.scan` | ✅ | Trivy's `config` scanner, already installed | security-scan |
| `terraform.plan` / `terraform.apply` | ✅ **mutating (apply)** | A real `local`-provider config (`runtime/terraform-demo/`) — genuine plan→apply lifecycle, no cloud credentials needed | terraform-plan, terraform-apply |
| `helm.upgrade` / `helm.rollback` | ✅ **mutating** | A real, trivial Helm release (`runtime/charts/canary`) in this pod's own namespace | deploy, rollback |
| `argocd.sync` / `argocd.rollback` | ✅ **mutating** | A real core-install ArgoCD (application-controller + repo-server + redis, no UI/API server) — synced by `kubectl patch`-ing the Application CRD directly | deploy, rollback |
| `http.smoke_test` | ✅ | Real requests against runtime's/bff's own `/health` | verify |
| `prometheus.query` | ✅ | A real Prometheus (`k8s/13-observability.yaml`) | verify, cost-analyzer, diagnose |
| `alertmanager.read` | ✅ | A real Alertmanager — Prometheus rules can fire into it, which POSTs to `/webhooks/alertmanager` | incident-triage |
| `cloud.cost_explorer` | ✅ (real API), gated | Real AWS Cost Explorer call — needs `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`; fails closed with a clear message if unset | cost-analyzer |
| `slack.post` | ✅ (real API), gated | Real webhook POST — needs `SLACK_WEBHOOK_URL`; fails closed if unset | notify |
| `pagerduty.read` | ✅ (real API), gated | Real API call — needs `PAGERDUTY_API_TOKEN`; fails closed if unset | incident-triage |
| `runs.read` | ❌ always simulated | — introspective query over this app's own DB | postmortem |

**"Gated" tools** are real but need an optional credential you supply — they
don't silently pretend to succeed when unset, they return a clear error.

## Simulated mode

Without `REAL_TOOLS_ENABLED=true`, or for `runs.read`, every tool returns
deterministic fake data from
[`runtime/app/tools/simulate.py`](../../runtime/app/tools/simulate.py) —
**seeded by the tool's own input**, so the same commit/image tag/prompt
always produces the same simulated result, while different input produces
different (sometimes failing) results. This is what lets agent policy
logic (e.g. "block on a CRITICAL CVE") actually branch differently across
runs instead of every simulated run being an identical no-op success.

## Mutating tools — the ones that need an approval gate

```
kubectl.restart   kubectl.scale
terraform.apply
helm.upgrade      helm.rollback
argocd.sync       argocd.rollback
docker.build      docker.tag
registry.push
```

This exact set is `MUTATING_TOOLS` in `runtime/app/orchestrator/executor.py`
— see [Security](09-security.md) for how the two independent approval gates
around them work.

Next: [Security →](09-security.md)
