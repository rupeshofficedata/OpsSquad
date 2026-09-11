# 🛠️ OpsSquad — Agentic DevOps Automation Platform

> A multi-agent system that reviews code, builds, deploys, responds to incidents, and controls cloud cost — driven by natural-language prompts.

---

## 📖 What This Is

OpsSquad is a web dashboard backed by a registry of **DevOps agents** — code review, testing, security scanning, deployment, incident remediation, cost analysis, and more. You either talk to a single agent directly through a chat interface, or chain several of them into a **Flightplan**: a declarative, approval-gated pipeline (e.g. review → test → build → scan → human approval → deploy → verify → auto-rollback). Every action is RBAC-checked, every step is persisted with its reasoning and tool calls, and mutating actions against production always stop for a human approval before they run.

It's a working scaffold, not a finished product: the orchestration, auth, RBAC, and Flightplan engine are all real and tested end-to-end, but the agents currently act on **deterministic simulated tool output** rather than a real Kubernetes cluster or cloud account — see [Current Functionality](#-current-functionality) for exactly what's real versus simulated.

---

## 🧱 Tech Stack

| Layer | Tech | Responsibility |
|---|---|---|
| **Frontend** | React + Vite + TailwindCSS | Login, dashboard, chat UI, Flightplan launcher, live run detail, admin panel |
| **API Gateway / BFF** | Node.js (Express) | Auth, JWT issue/verify, RBAC middleware, WebSocket run streaming, request proxy |
| **Agent Runtime** | FastAPI (Python) | Agent execution, LLM calls, tool invocation, Flightplan orchestration, admin CRUD, webhooks |
| **Database** | PostgreSQL 16 | Users, roles, agents, flightplans, runs, run_steps, audit trail |
| **Cache / Sessions** | Redis | Refresh-token storage today; reserved for run-queue and tool-output caching later |

All Python lives in one FastAPI service — an earlier version of this repo split it into a second Flask "control plane" for admin/webhooks, but that boundary added an extra service, an extra dependency tree, and an extra HTTP hop for no real gain, so it was folded back into FastAPI.

---

## 🏗️ Architecture Diagram

```
┌──────────────────────────────────────────────────────────────┐
│                    React Frontend (:5173)                     │
│  Login │ Dashboard │ Chat │ Flightplans │ Runs │ Admin Panel  │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTPS + WebSocket (JWT in header)
                            ▼
┌──────────────────────────────────────────────────────────────┐
│              Node.js API Gateway / BFF (:4000)                │
│  • Issues & verifies JWT        • RBAC middleware              │
│  • WebSocket log streaming      • Rate limiting                │
│  • Proxies to FastAPI           • Never talks to LLM directly  │
└───────────────────────────┬────────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                  FastAPI Runtime (:8000)                       │
│  • /chat, /agents/*/run, /flightplans/*      (agent execution) │
│  • /admin/*                                  (user/role/audit) │
│  • /webhooks/*                               (GitHub, Alertmgr)│
│  • Orchestrator + per-agent modules (app/agents/)               │
└──────────┬─────────────────────────────────────────────────────┘
           ▼
┌────────────────────────┐
│   Tool Layer (Python)  │   kubectl/terraform/trivy real, rest simulated
│ kubectl │ terraform    │
│ docker  │ trivy        │
│ git     │ prometheus   │
└──────────┬─────────────┘
           ▼
┌──────────────────────────────────────────────────────────────┐
│         PostgreSQL (:5432)          │      Redis (:6379)      │
│  users, roles, agents, flightplans, │  refresh-token store     │
│  runs, run_steps, audit_logs        │                          │
└──────────────────────────────────────────────────────────────┘
```

---

## 🖥️ The Dashboard, Page by Page

| Page | Route | Who | What it does |
|---|---|---|---|
| **Login** | `/login` | anyone | Email + password. Seeded credentials are printed on the form itself. |
| **Dashboard** | `/` | anyone | Run history table — kind, agent or flightplan, status, start time. Click through to a run's detail. |
| **Chat** | `/chat` | dev+ for mutating agents, viewer for read-only | Free-text prompt box with a staging/prod environment toggle. The intent router matches your prompt to one agent, runs it, and shows the matched agent, its output, and its reasoning inline. A mutating agent targeting `prod` doesn't run — it shows an "awaiting approval" banner instead. |
| **Flightplans** | `/flightplans` | dev+ | One card per saved Flightplan (name, description, env badge). **Execute** kicks off a run and takes you to its detail page. |
| **Run Detail** | `/runs/:id` | anyone (own visibility) | Live step-by-step timeline over a WebSocket — each step's agent, status, reasoning, and full output JSON. **Approve** appears for admins when a run is paused at a gate; **Abort** appears for dev+ while a run is still queued or awaiting approval. |
| **Admin** | `/admin` | admin only | User table with an inline role dropdown per user, plus a live audit log of every mutating action taken anywhere in the system. |

**Typical first session:** log in as `admin@opssquad.dev`, open **Chat** and ask something read-only ("find idle cost resources"), then open **Flightplans** and execute `ship-to-prod` with a repo/commit — watch it run through review/test/build/scan on the **Run Detail** page, approve the gate, and see deploy/verify/rollback play out. Then check **Admin** to see the audit trail it left behind.

---

## 🔐 Authentication & Authorization

### Auth Flow

```
1. User submits email + password  →  Node BFF /api/auth/login
2. BFF fetches user from Postgres, verifies bcrypt hash
3. On success → issue Access Token (15 min) + Refresh Token (7 days)
4. Access token stored in memory; Refresh token in httpOnly cookie
5. Every protected request → BFF verifies JWT, attaches req.user
6. RBAC middleware checks role against route permission map
```

### JWT Payload

```json
{
  "sub": "u_8f21c9",
  "email": "admin@opssquad.dev",
  "role": "admin",
  "iat": 1757000000,
  "exp": 1757000900
}
```

### 👥 Default Seed Users

> **Change these before any non-local deployment.** They exist only so the app is usable on first `docker compose up`.

| Email | Password | Role |
|---|---|---|
| `admin@opssquad.dev` | `Admin@123` | **admin** |
| `dev@opssquad.dev` | `Dev@123` | **dev** |
| `viewer@opssquad.dev` | `Viewer@123` | **viewer** |

### 🛡️ RBAC Permission Matrix

| Action | admin | dev | viewer |
|---|:---:|:---:|:---:|
| View dashboard & run history | ✅ | ✅ | ✅ |
| View logs & agent output | ✅ | ✅ | ✅ |
| Use chat agent (read-only agents) | ✅ | ✅ | ✅ |
| Use chat agent (write/deploy agents) | ✅ | ✅ | ❌ |
| Create / edit Flightplans | ✅ | ✅ | ❌ |
| Execute Flightplan — any env | ✅ | ✅ | ❌ |
| Approve a gated step | ✅ | ❌ | ❌ |
| Manage users & roles | ✅ | ❌ | ❌ |
| View audit log | ✅ | ❌ | ❌ |

A dev **can** trigger a `prod` Flightplan — it just stops at that Flightplan's own approval step (admin-only) before any mutating agent runs. Within a Flightplan, each individual agent's `min_role` is also re-checked against whoever triggered the run, so a dev-authored Flightplan can't sneak in a call to an admin-only agent (e.g. `rollback`) without a gate in front of it.

**Defense in depth:** the BFF's RBAC middleware is a convenience gate for the UI; FastAPI re-verifies every check independently and never trusts the BFF's decision.

---

## 🗄️ Database Schema

See [`db/migrations/001_init.sql`](db/migrations/001_init.sql) for the full schema (users, agents, flightplans, runs, run_steps, audit_logs) and [`db/seed.sql`](db/seed.sql) for default users + the agent registry.

---

## 🤖 The Agent Registry

Seeded on first boot. Each agent has a **narrow tool scope** — least privilege per agent — and its own Python module under [`runtime/app/agents/`](runtime/app/agents/) with real pass/fail logic, not just a tool-call echo.

| Slug | Purpose | Mutating? | Min Role |
|---|---|:---:|---|
| `code-review` | Diff review — blocks on a committed secret | ❌ | dev |
| `test-runner` | Runs the impacted test suite, reports failures | ❌ | dev |
| `build` | Docker build, tag, push to registry | ✅ | dev |
| `security-scan` | CVE scan — blocks on a policy severity breach | ❌ | viewer |
| `deploy` | Helm upgrade / ArgoCD sync | ✅ | dev |
| `verify` | Smoke test + error-rate threshold check | ❌ | dev |
| `rollback` | Reverts to the last known-good release | ✅ | admin |
| `incident-triage` | Reads an alert, assigns severity | ❌ | viewer |
| `diagnose` | Pod logs + resource state → a recommended remediation | ❌ | viewer |
| `remediate` | Restart / scale / rollback, enforced against guardrails | ✅ | admin |
| `terraform-plan` | Generates a plan, never applies | ❌ | dev |
| `terraform-apply` | Applies an approved plan | ✅ | admin |
| `cost-analyzer` | Finds idle/oversized resources, estimates savings | ❌ | viewer |
| `postmortem` | Drafts a markdown RCA from the run's own trace | ❌ | viewer |
| `notify` | Posts a status update to a chat channel | ❌ | viewer |

---

## 💬 Chat Agent — Prompt to Execution

```
Prompt
  │
  ├─> 1. Intent Router
  │      Claude (if ANTHROPIC_API_KEY set), else a tokenized keyword
  │      matcher with stemming and stopword filtering
  │      → agent_slug + extracted parameters
  │
  ├─> 2. RBAC Gate
  │      user.role >= agent.min_role ?  else 403
  │
  ├─> 3. Mutation Gate
  │      agent.is_mutating && env == 'prod' ?  → require approval
  │
  ├─> 4. Execute the agent's module, invoking its declared tools
  │
  └─> 5. Persist to runs + run_steps, write audit_log
```

---

## 🚀 Flightplans — Multi-Agent Workflows

A **Flightplan** is a declarative YAML graph — steps run in dependency order, a step can carry a `when` condition, a `policy.block_on` (fails the whole run on a matching severity), or `guardrails` (fails a `remediate` action outside its allowed set or clamps it to a max). A `type: approval` step pauses the run until an admin approves it; the run's final status reflects whether *any* step failed, not just whether one triggered an early stop.

- [`ship-to-prod.yaml`](flightplans/ship-to-prod.yaml) — review → test → build → scan → **human approval** → deploy → verify → auto-rollback on failure
- [`incident-response.yaml`](flightplans/incident-response.yaml) — triggered by the Alertmanager webhook: triage → diagnose → remediate (guardrailed) → notify → postmortem
- [`cost-sweep.yaml`](flightplans/cost-sweep.yaml) — meant to run nightly: find idle resources → open a right-sizing PR (plan only, never applies)

---

## 🔌 API Endpoints

### Node BFF (`:4000`) — what the frontend talks to

| Method | Route | Role | Purpose |
|---|---|---|---|
| POST | `/api/auth/login` | public | Email + password → JWT pair |
| POST | `/api/auth/refresh` | public | Rotate access token |
| POST | `/api/auth/logout` | any | Revoke refresh token |
| GET | `/api/me` | any | Current user + permissions |
| GET | `/api/agents` | any | List agents visible to role |
| POST | `/api/chat` | any | Proxy to FastAPI's intent router |
| GET | `/api/flightplans` | any | List |
| POST | `/api/flightplans` | dev+ | Create |
| POST | `/api/flightplans/:id/execute` | dev+ | Trigger a run |
| GET | `/api/runs` | any | Run history |
| GET | `/api/runs/:id` | any | Run detail + steps |
| POST | `/api/runs/:id/approve` | admin | Release a gated step |
| POST | `/api/runs/:id/abort` | dev+ | Stop a queued/awaiting-approval run |
| GET/POST | `/api/admin/users` | admin | User list / create |
| PATCH | `/api/admin/users/:id/role` | admin | Change role |
| GET | `/api/admin/audit` | admin | Audit log, filterable |
| WS | `/ws/runs/:id` | any | Live run-step stream (polls the DB every 2s — no message broker yet) |

### FastAPI Runtime (`:8000`) — internal, plus two public webhook endpoints

| Method | Route | Purpose |
|---|---|---|
| POST | `/chat` | Route prompt → agent → run |
| POST | `/agents/{slug}/run` | Direct agent execution |
| POST | `/flightplans/{slug}/execute` | Start a Flightplan run |
| POST | `/flightplans/runs/{id}/approve` | Resume a run past its approval gate |
| POST | `/runs/{id}/abort` | Abort a run |
| GET/POST | `/admin/users` | User CRUD |
| PATCH | `/admin/users/{id}/role` | Change role |
| GET | `/admin/audit` | Audit log with filters |
| POST | `/webhooks/github` | HMAC-verified push/PR events |
| POST | `/webhooks/alertmanager` | Bearer-token verified, fires `incident-response` in-process |
| GET | `/health` | Liveness |

---

## 📁 Project Structure

```
opssquad/
├── frontend/                    # React + Vite
├── bff/                         # Node.js + Express
├── runtime/                     # FastAPI — the only Python service
│   └── app/
│       ├── agents/              # one module per agent — real pass/fail logic
│       ├── tools/                # simulated tool layer (simulate.py = fake data generators)
│       ├── orchestrator/        # intent router, single-agent executor, Flightplan graph
│       └── routes/              # chat, agents, flightplans, runs, admin, webhooks
├── db/                          # migrations + seed.sql
├── flightplans/                 # YAML definitions (reference copy; seed.sql has the live JSON)
├── k8s/                         # kind cluster manifests + bootstrap.sh
├── docker-compose.yml
└── .env.example
```

---

## 🐳 Running Locally

```bash
# 1. Clone and configure
cp .env.example .env
#    set OPENAI_API_KEY / ANTHROPIC_API_KEY, JWT_SECRET, DB creds

# 2. Bring everything up
docker compose up -d

# 3. Apply schema + seed default users and agents
docker compose exec postgres psql -U opssquad -d opssquad -f /db/seed.sql

# 4. Open the app
open http://localhost:5173
#    login: admin@opssquad.dev / Admin@123
```

**Service ports**

| Service | Port |
|---|---|
| React | 5173 |
| Node BFF | 4000 |
| FastAPI | 8000 |
| PostgreSQL | 5432 |
| Redis | 6379 |

## ☸️ Running on Kubernetes (kind)

Requires `docker`, [`kind`](https://kind.sigs.k8s.io/), and `kubectl` on PATH.

```bash
# 1. Bootstrap everything: build images, create/reuse the "opssquad" kind
#    cluster, apply manifests, run the migration Job, restart deployments so
#    they pick up the freshly built images, and port-forward frontend
#    (:5173) and BFF (:4000) to localhost.
./k8s/bootstrap.sh

# 2. Open the app
open http://localhost:5173
#    login: admin@opssquad.dev / Admin@123
```

Idempotent — re-run anytime to pick up code changes. Uses its own `kubectl --context kind-opssquad`, so it won't touch your current kube context.

```bash
# Stop the port-forwards
kill $(cat /tmp/opssquad-k8s-port-forward.pids)

# Tear down the whole cluster
kind delete cluster --name opssquad
```

Details and manifest layout in [`k8s/`](k8s/).

---

## ✅ Current Functionality

What's real and tested end-to-end right now:

- **Auth & RBAC** — full login/refresh/logout cycle, JWT verified independently at both the BFF and FastAPI, per-agent and per-Flightplan-step role checks.
- **Chat routing** — a tokenized keyword+stemming matcher (no LLM required) correctly routes a broad set of natural-language prompts to the right agent.
- **Per-user model choice** — Claude (Anthropic API) or a self-hosted OpenAI-compatible server (`llama-server`, Ollama, vLLM, …), picked per user from the Chat page and threaded through every agent-execution path, including Flightplan resume-after-approval (which runs under the *triggering* user's preference). Tested end-to-end against a real local `llama-server` running Qwen2.5-Coder-7B-Instruct (Q4, GPU-accelerated). The distro's prebuilt CUDA backend has no compiled kernels for this machine's GTX 1080/Pascal (`sm_61`) — neither does the CUDA-12.8 AUR prebuilt tried as an alternative, both target newer archs only. Two working fixes, real trade-off between them: **Vulkan** (`-dev Vulkan0 -ngl 999`, zero extra installs, the packaged backend already supports it) gets ~45 tok/s generation; a **self-built CUDA** (`cmake -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=61`, needs a CUDA ≤12.x toolkit since 13.x dropped Pascal — ~45-90 min build) gets ~37 tok/s generation but 6-10x faster prompt/context processing (185-346 tok/s vs ~29), which wins for this codebase's tool-schema-heavy agent prompts — that's what's running by default (`/opt/llama-cpp-cuda-pascal`, self-contained build archived separately). **Caveat, found by testing, not assumed:** a quantized 7B model's tool-call compliance is inconsistent — reliable for some agents/prompts (`security-scan` → real `trivy.scan`, consistently), silent (describes intent in prose instead of calling a tool) for others (`cost-analyzer`, `terraform-plan` on a bare-params prompt) even after strengthening the tool-use instruction. Claude doesn't have this problem; it's a known characteristic of small local models, not a bug in the adapter — verified by watching real tool_calls arrive (or not) per agent.
- **Real kubectl/terraform/trivy/docker/helm/argocd** — `REAL_TOOLS_ENABLED=true` (the default) swaps these tool implementations for the real CLIs/APIs, genuinely shelling out (see [`runtime/app/tools/real.py`](runtime/app/tools/real.py)). Since there's no real cloud account or "payments-api" deployment to target, they're grounded against safe, self-contained real targets: **kubectl** manages the runtime pod's own least-privilege ServiceAccount (`k8s/05-rbac.yaml`, scoped to the `opssquad` namespace), **terraform** runs a real `local`-provider config (`runtime/terraform-demo/`) through a genuine plan→apply lifecycle, **trivy** scans the runtime container's own filesystem, **docker** (`buildah`, rootless-ish with one narrow `CAP_SYS_ADMIN` grant — found by testing, not `privileged: true`) builds a real image from `runtime/docker-demo/`, **helm** installs/rolls back a real, trivial release (`runtime/charts/canary`) in this pod's own namespace, **argocd** is a real core-install (application-controller + repo-server + redis, deliberately no UI/API server) whose Application CR is synced/rolled back by `kubectl patch`-ing the CRD directly rather than via the `argocd` CLI. **cloud.cost_explorer/slack.post/pagerduty.read** are real API calls too, gated on a real credential (`AWS_ACCESS_KEY_ID`/`SLACK_WEBHOOK_URL`/`PAGERDUTY_API_TOKEN`, all optional) — fail closed with a clear message when unset, same shape as `ALERTMANAGER_WEBHOOK_TOKEN`. A production deployment would point all of these at real infrastructure instead of at OpsSquad itself.
- **Local-model status + start** — the Chat page shows a live running/loading/stopped indicator and a "Start model" button for the local `llama-server` provider, backed by `local-model-control/agent.py` — a tiny host-run control agent (bff/runtime run inside kind pods and can't touch the host's process table otherwise), reachable the same way `LOCAL_LLM_BASE_URL` already is.
- **Every remaining tool is real** — `git.diff/log`/`secrets.scan` (a real `runtime/git-demo/` repo, rebuilt fresh each image build, with a genuine gitleaks-detectable fake key in its history), `lint.run` (real `ruff` against this app's own source), `test.select/run` (a real, small `pytest` suite — `app/tests/`), `registry.push/pull` (a real, self-hosted in-cluster registry — `k8s/12-registry.yaml`), `iac.scan` (trivy's `config` scanner, already installed), `http.smoke_test` (real requests against runtime's/bff's own `/health`), `prometheus.query`/`alertmanager.read` (a real Prometheus + a real Alertmanager, `k8s/13-observability.yaml` — Prometheus was already running by hand this session; reconstructed into a committed manifest here). The whole loop is real end to end: Prometheus's rule can fire into Alertmanager, which POSTs to `/webhooks/alertmanager` (already real), and `alertmanager.read` can separately query Alertmanager directly within that same run.
- **GitHub webhook → real trigger** — a `push` to `main`/`master` maps into a real `ship-to-prod` run (`repo`/`commit` taken from the payload), same backgrounded pattern as the Alertmanager webhook. Non-push events still just acknowledge — there's no PR-time Flightplan to map them to.
- **Cron scheduler** — an in-process `asyncio` loop (`runtime/app/scheduler.py`, no separate CronJob) reads every Flightplan's `trigger.cron` fresh from the DB each tick and fires a real run when due — `cost-sweep.yaml`'s `schedule.cron` is no longer read by nothing. In-memory only (no durable job store, a restart near a fire time just recomputes the next slot) — an honest, documented limitation, not a silent gap.
- **15 agent modules** with actual decision logic: `security-scan` really blocks on a CVE severity policy, `test-runner` really fails the run on failing tests, `remediate` really clamps an over-limit scale request against its guardrail, etc. — see [`runtime/app/agents/`](runtime/app/agents/).
- **Flightplan engine** — dependency-ordered steps, `when` conditions, approval gates, policy/guardrail enforcement, correct final-status computation (a run that failed and auto-rolled-back is reported as failed, not success). Execution runs as a background `asyncio` task (`orchestrator/graph.run_in_background`), not inline in the HTTP handler — `POST .../execute` returns as soon as the run is created, and `abort` works against a `running` run, not just `queued`/`awaiting_approval` (checked cooperatively between steps, so it stops before the *next* step rather than pre-empting one mid-flight). Live-tested: aborted a `ship-to-prod` run after step 4 of 8, confirmed it never reached the approval gate.
- **Alertmanager webhook** — fires `incident-response` in-process on receipt, once `ALERTMANAGER_WEBHOOK_TOKEN` is set (it fails closed with no default-allow if unset).
- **Admin panel** — user CRUD, role changes, and a full audit trail of every mutating action.
- **Two deploy paths** — `docker compose` for local dev, `k8s/bootstrap.sh` for a local Kubernetes cluster via `kind`.
- **Real self-hosted Vault** (k8s path) — `JWT_SECRET`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GITHUB_WEBHOOK_SECRET`, and `ALERTMANAGER_WEBHOOK_TOKEN` are fetched from Vault at startup, not read from a k8s `Secret`. Runtime and bff each authenticate with their own ServiceAccount token via Vault's Kubernetes auth method (`k8s/07-vault-init-job.yaml`) — no static Vault credential stored anywhere, and each service's Vault policy only grants it the paths it actually needs (bff can't read the Anthropic key, for instance). Both services also **refuse to start** if `JWT_SECRET` ends up empty or still the old public placeholder, whether that came from Vault or a plain env var. Vault itself runs in dev mode (in-memory, no HA) — a deliberate scope choice for this local `kind` cluster, same as terraform-demo's local-only provider; a real deployment needs a real storage backend + auto-unseal, or HCP Vault. `POSTGRES_PASSWORD`/`DATABASE_URL` stay on the plain k8s `Secret` — Postgres is an unmodified image with no way to speak to Vault without a sidecar injector. docker-compose is untouched — no Vault there, plain env vars, same fail-closed check.

What's still **simulated**, not real, by default:

- `docker.*`, `cloud.cost_explorer`, `prometheus.query`, `helm.*`, `argocd.*`, `slack.post`, `pagerduty.read`, `iac.scan`, and a few others return deterministic fake data from [`runtime/app/tools/simulate.py`](runtime/app/tools/simulate.py) — seeded by their own input, so the same commit or alert always produces the same result, and different input produces different (sometimes failing) results.
- No real cloud provider, container registry, Slack workspace, or PagerDuty account is touched by anything.

---

## 🔮 In Development / Future Integrations

Roughly in the order they'd need to happen to move this from a scaffold toward production use:

- **Flightplan builder UI** — the API to create/edit a Flightplan already exists (`POST /api/flightplans`), but the dashboard has no visual builder for it yet — only the three seeded Flightplans can be launched from the UI.
- **HCP Vault / a real storage backend** — the local dev-mode Vault above proves the integration (Kubernetes auth, policies, fetch-at-startup) genuinely works, but has no HA and loses its data on restart (the init Job just re-seeds it). A real deployment wants HCP Vault or a self-hosted cluster with a durable storage backend and real auto-unseal.
- **SSO** and **per-user/per-day token or cost caps** — not implemented.
- **Redis-backed tool-output caching** — Redis is only used for refresh tokens today; caching read-only tool output (cluster state, cost data) for a few minutes is a cheap follow-up once real tools exist.
