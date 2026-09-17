# AI-CONTEXT — read this first, skip the rest unless you need it

One page, built to be cheap to load. If you're an AI assistant picking up
this repo cold, read this file and you very likely don't need to open
anything else. Deeper detail lives in [`docs/INDEX.md`](INDEX.md) and its
`guide/*.md` pages — only follow those links if this page doesn't answer
your question.

## What this is

OpsSquad — a multi-agent DevOps automation platform. React frontend, Node
BFF, FastAPI runtime, Postgres, Redis. 15 agents (code-review, deploy,
rollback, remediate, etc.) invoked via chat or chained into YAML
"Flightplans." Full detail: [`guide/01-overview.md`](guide/01-overview.md).

## Stack & layout

```
frontend/   React + Vite + Tailwind        (React Router pages in src/pages/)
bff/        Node.js + Express              (auth, RBAC, proxy, WebSocket)
runtime/    FastAPI (Python)               (only Python service — agents, tools, orchestrator)
db/         Postgres migrations + seed
flightplans/  YAML Flightplan definitions (reference copies; seed.sql has the live JSON)
k8s/        kind-cluster manifests + bootstrap.sh
docs/       this documentation tree
docs/superpowers/plans/   DESIGN docs for features — the "desired state" (see STATE.md)
```

Full per-file breakdown: [`guide/03-frontend.md`](guide/03-frontend.md),
[`guide/04-bff.md`](guide/04-bff.md), [`guide/05-runtime.md`](guide/05-runtime.md).

## Current state vs. desired state

`docs/superpowers/plans/*.md` are design docs written before implementing a
feature — treat them as the **desired state**. This documentation tree
(`docs/guide/*.md`, `STATE.md`) reflects the **current, actual state** of
the code, verified against the running system, not just the diff.

As of the last update to this file, every plan in `docs/superpowers/plans/`
is **fully implemented and merged to `master`** — no open gap. Check
[`STATE.md`](STATE.md) for the live gap table; if a new plan file appears
there without a matching STATE.md row, treat it as not yet built.

## Recent work (most recent first)

1. **Generalist chat agent replaces per-prompt routing** (`runtime/app/routes/chat.py`,
   `db/seed.sql`, `orchestrator/router.py` deleted) — chat always uses one
   `assistant` agent (full 29-tool catalog, `min_role='viewer'`) instead of
   a keyword router picking one of 15 narrow specialists per prompt (which
   silently misrouted any zero-keyword-overlap prompt to `agents[0]`).
   **Shipping this surfaced a real incident**: `_run_simulated`'s generic
   fallback (used when no LLM is reachable) had zero mutation gating and
   genuinely executed a real `kubectl` rollout restart against `redis`
   from one chat message — confirmed via `kubectl get events`. Fixed same
   session: that fallback now refuses any `MUTATING_TOOLS` call outright.
   See [`STATE.md`](STATE.md) and
   [`guide/09-security.md`](guide/09-security.md#simulated-mode-has-a-narrower-gate-than-the-live-paths).
2. **Vault login retry** (`runtime/app/vault.py`, `bff/src/vault.js`) — dev-mode
   Vault is in-memory and wipes its auth config on every restart; a CronJob
   (`k8s/07-vault-init-job.yaml`) self-heals it every 2 minutes. Both
   services now retry a failed login every 5s for ~3.3min instead of
   crashing if they boot inside that window. Root-caused from real
   `CrashLoopBackoff` pods in the live kind cluster, not theoretical.
3. **Per-command mutation gate** (chat) — any mutating tool call
   (`kubectl.scale`, `helm.upgrade`, `terraform.apply`, …) an agent proposes
   mid-chat-run pauses that round for the requesting dev/admin's own
   Approve/Deny, independent of the older agent-level prod-only gate.
   Viewer role gets an inline denial, no pause. Flightplans are never
   gated this way (a step declaring a mutating tool is the advance
   sign-off). See [`guide/09-security.md`](guide/09-security.md).
4. **Live round-by-round chat streaming** — chat runs stream over the same
   `/ws/runs/:id` WebSocket the Run Detail page uses, instead of blocking
   on one final response.
5. **Real tool schemas + strict/lenient local-model mode + multi-turn
   chat (`ask_user`)** — see
   `docs/superpowers/plans/local-model-tool-reliability-and-multiturn-chat.md`,
   fully implemented.

## Known non-obvious facts (save yourself a wrong assumption)

- **The per-command mutation gate does not cover every code path.** It
  only lives inside `_run_with_claude`/`_run_with_local_llm`
  (`executor.py`). The third path, `_run_simulated` (hit whenever
  `model_provider` resolves to no working LLM), has its own separate,
  narrower rule: refuse any `MUTATING_TOOLS` call outright, no gate/pause
  at all — because there's no LLM to ask and no in-progress run to
  resume. This was a real incident (see "Recent work" above), not a
  hypothetical — verify against this fact before assuming "the mutation
  gate covers X" for any new code path that can reach a tool call.
- **Only one Python service.** An earlier version split admin/webhooks into
  a second Flask service — folded back into FastAPI. Don't look for a
  second Python service.
- **Most tools are REAL, not simulated**, when `REAL_TOOLS_ENABLED=true`
  (the default) — kubectl/terraform/trivy/docker/helm/argocd genuinely
  shell out or call real self-hosted targets (this pod's own namespace, a
  local Terraform provider, this container's own filesystem, a real
  in-cluster registry). See [`guide/08-tools.md`](guide/08-tools.md) for
  the full real-vs-simulated table before assuming a tool is fake.
  `simulate.py`/`stubs.py` are the fallback only.
- **RBAC is checked twice, independently** — BFF (Node) is a UI
  convenience gate; FastAPI never trusts it and re-checks every time. Don't
  "fix" an RBAC bug in only one of the two.
- **`safe_eval.py`** is a real restricted AST evaluator for Flightplan
  `when`/`policy` expressions — replaced a real `eval()` sandbox-escape
  vulnerability. Never swap it back for `eval()`.
- **Vault dev-mode is in-memory** — every Vault pod restart wipes its auth
  config; self-heals via the CronJob above. This is a deliberate scope
  choice, not a bug to "fix" by itself — the retry fix above is the right
  layer to patch.
- **No pytest-asyncio** — async tests use plain `def test_*()` +
  `asyncio.run(...)`, see any file in `runtime/app/tests/`.
- **Local Python has no venv** — `pytest` isn't on the host `python3`; run
  tests inside the runtime pod/container (`kubectl exec ... pytest` or
  `docker compose exec runtime pytest`), not on the bare host.
- **`k8s/bootstrap.sh` is fully idempotent** — safe to re-run any time to
  pick up new code; rebuilds all 3 images, reuses the existing kind
  cluster, reruns the (idempotent) migration Job.

## Where to look for X

| Need | File(s) |
|---|---|
| Auth / JWT / RBAC | `runtime/app/security.py`, `bff/src/jwt.js`, `bff/src/middleware/require*.js` |
| Agent behavior (pass/fail logic) | `runtime/app/agents/*.py` |
| Tool catalog (real vs simulated) | `runtime/app/tools/{real,stubs,schemas}.py` |
| Chat tool-use loop | `runtime/app/orchestrator/executor.py` |
| Flightplan graph execution | `runtime/app/orchestrator/graph.py` |
| DB schema | `db/migrations/001_init.sql` |
| Flightplan YAML examples | `flightplans/*.yaml` |
| k8s manifests | `k8s/*.yaml`, entrypoint `k8s/bootstrap.sh` |
| Design docs for a feature in progress | `docs/superpowers/plans/*.md` |

## Maintenance rule for this doc tree

**Any session that changes code under `frontend/`, `bff/`, `runtime/`,
`db/`, `k8s/`, or `flightplans/` should update the matching `guide/*.md`
page and, if a `docs/superpowers/plans/*.md` gets implemented, mark it
done in `STATE.md`.** Keep this file (`AI-CONTEXT.md`) as the single
place a fresh session reads first — if you add a fact a fresh session
would need to avoid a wrong assumption, add it to "Known non-obvious
facts" above, not buried in a `guide/*.md` page nobody will open.
