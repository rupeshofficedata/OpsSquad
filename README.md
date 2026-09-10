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
│   Tool Layer (Python)  │   simulated by default — see below
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
- **Chat routing** — a tokenized keyword+stemming matcher (no LLM required) correctly routes a broad set of natural-language prompts to the right agent; swaps to a real Claude tool-use loop when `ANTHROPIC_API_KEY` is set.
- **15 agent modules** with actual decision logic: `security-scan` really blocks on a CVE severity policy, `test-runner` really fails the run on failing tests, `remediate` really clamps an over-limit scale request against its guardrail, etc. — see [`runtime/app/agents/`](runtime/app/agents/).
- **Flightplan engine** — dependency-ordered steps, `when` conditions, approval gates, policy/guardrail enforcement, correct final-status computation (a run that failed and auto-rolled-back is reported as failed, not success).
- **Alertmanager webhook** — fires `incident-response` in-process on receipt, once `ALERTMANAGER_WEBHOOK_TOKEN` is set (it fails closed with no default-allow if unset).
- **Admin panel** — user CRUD, role changes, and a full audit trail of every mutating action.
- **Two deploy paths** — `docker compose` for local dev, `k8s/bootstrap.sh` for a local Kubernetes cluster via `kind`.

What's **simulated**, not real, by default (no `ANTHROPIC_API_KEY` needed to try any of this):

- Every tool call (`kubectl.*`, `terraform.*`, `trivy.scan`, `docker.*`, `cloud.cost_explorer`, `prometheus.query`, `slack.post`, …) returns deterministic fake data generated from [`runtime/app/tools/simulate.py`](runtime/app/tools/simulate.py) — seeded by its own input, so the same commit or alert always produces the same result, and different input produces different (sometimes failing) results.
- No real Kubernetes, cloud provider, registry, or Slack workspace is touched.

---

## 🔮 In Development / Future Integrations

Roughly in the order they'd need to happen to move this from a scaffold toward production use:

- **Real tool integrations** — swap each simulated tool in `app/tools/` for an actual `kubectl`/`helm`/`terraform`/`trivy`/cloud-SDK/Slack call. This is the biggest remaining chunk of work; the agent logic that consumes tool output is already written and doesn't need to change shape.
- **GitHub webhook → real trigger** — `/webhooks/github` currently just acknowledges push/PR events; it doesn't yet map them into a `ship-to-prod` run.
- **A real job queue** — Flightplan execution runs synchronously inside the HTTP request handler today, so `abort` can only stop a run that hasn't started executing yet (`queued` / `awaiting_approval`), not one mid-flight. A worker queue (Redis is already provisioned for this) would let long-running or scheduled Flightplans run in the background and be genuinely cancellable.
- **Scheduler for cron-triggered Flightplans** — `cost-sweep.yaml` declares a `schedule.cron`, but nothing currently reads it; it has to be triggered manually or by an external cron job hitting the API.
- **Flightplan builder UI** — the API to create/edit a Flightplan already exists (`POST /api/flightplans`), but the dashboard has no visual builder for it yet — only the three seeded Flightplans can be launched from the UI.
- **Secrets management** — credentials currently live in a `.env` file or a plain Kubernetes `Secret`; a Vault/AWS Secrets Manager integration would replace that.
- **SSO** and **per-user/per-day token or cost caps** — not implemented.
- **Redis-backed tool-output caching** — Redis is only used for refresh tokens today; caching read-only tool output (cluster state, cost data) for a few minutes is a cheap follow-up once real tools exist.
