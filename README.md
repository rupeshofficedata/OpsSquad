# 🛠️ OpsSquad — Agentic DevOps Automation Platform

> A multi-agent system that reviews code, builds, deploys, responds to incidents, and controls cloud cost — driven by natural-language prompts.

---

## 📌 Naming Decision

| Concept | Name Used | Alternatives Considered |
|---|---|---|
| Product | **OpsSquad** | FlowForge, Orkyn, DevSwarm, PipeCrew |
| A saved multi-agent workflow (your "mission") | **Flightplan** | Runbook, Blueprint, Sortie, Campaign |
| A single agent execution | **Run** | Task, Job |
| Chat-driven single agent call | **Quick Run** | Ad-hoc, Prompt Run |

**Why "Flightplan"** — it implies a *pre-approved route with checkpoints*, which is exactly what a multi-agent DevOps workflow is. It also pairs naturally with words you'll need later: `takeoff`, `abort`, `land`, `co-pilot`.

---

## 🧱 Tech Stack & Why Each Piece Exists

| Layer | Tech | Responsibility |
|---|---|---|
| **Frontend** | React + Vite + TailwindCSS | Login page, dashboard, chat UI, Flightplan builder, live run logs |
| **API Gateway / BFF** | Node.js (Express) | Auth, JWT issue/verify, RBAC middleware, WebSocket streaming, request proxy |
| **Agent Runtime** | FastAPI (Python) | Agent execution, LLM calls, tool invocation, Flightplan orchestration |
| **Control Plane** | Flask (Python) | User admin, role management, audit log, integration webhooks, cron jobs |
| **Database** | PostgreSQL 16 | Users, roles, agents, flightplans, runs, logs, audit trail |
| **Cache / Queue** | Redis | Session store, run queue, agent shared context |

### ⚠️ Honest Note on the Stack
Running **Flask and FastAPI together** is unusual — they overlap heavily. Two workable options:

- **Option A (recommended):** Drop Flask. Use FastAPI for everything Python. Simpler ops, one dependency tree.
- **Option B (as implemented here):** Keep both, but with a **hard boundary** — FastAPI does *async agent work only*, Flask does *synchronous CRUD/admin only*. Never let them share business logic.

This repo follows **Option B** since that was the requirement, but the split is enforced strictly.

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
│  • Routes to FastAPI or Flask   • Never talks to LLM directly  │
└──────────┬──────────────────────────────┬────────────────────┘
           │                              │
           ▼                              ▼
┌────────────────────────┐   ┌────────────────────────────────┐
│ FastAPI Agent Runtime  │   │  Flask Control Plane (:6001)   │
│        (:8000)         │   │  • User / role CRUD            │
│  • /agents/run         │   │  • Audit log queries           │
│  • /flightplans/exec   │   │  • Git & Slack webhooks        │
│  • /chat (streaming)   │   │  • Scheduled jobs              │
│  • Orchestrator + LLM  │   │  (no LLM calls here)           │
└──────────┬─────────────┘   └───────────────┬────────────────┘
           │                                 │
           ▼                                 │
┌────────────────────────┐                   │
│   Tool Layer (Python)  │                   │
│ kubectl │ terraform    │                   │
│ docker  │ trivy        │                   │
│ git     │ prometheus   │                   │
└──────────┬─────────────┘                   │
           │                                 │
           ▼                                 ▼
┌──────────────────────────────────────────────────────────────┐
│         PostgreSQL (:5432)          │      Redis (:6379)      │
│  users, roles, agents, flightplans, │  sessions, run queue,   │
│  runs, run_steps, audit_logs        │  agent shared context   │
└──────────────────────────────────────────────────────────────┘
```

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
| Execute Flightplan — **non-prod** | ✅ | ✅ | ❌ |
| Execute Flightplan — **prod** | ✅ | ⚠️ needs approval | ❌ |
| Approve a gated step | ✅ | ❌ | ❌ |
| Manage users & roles | ✅ | ❌ | ❌ |
| Manage cloud credentials | ✅ | ❌ | ❌ |
| View audit log | ✅ | ❌ | ❌ |

**Best practice:** enforce RBAC in the **BFF middleware** *and* re-check in FastAPI. Never rely on the frontend hiding a button.

---

## 🗄️ Database Schema

See [`db/migrations/001_init.sql`](db/migrations/001_init.sql) for the full schema (users, agents, flightplans, runs, run_steps, audit_logs) and [`db/seed.sql`](db/seed.sql) for default users + the agent registry.

---

## 🤖 The Agent Registry

Seeded on first boot. Each agent has a **narrow tool scope** — least privilege per agent.

| Slug | Purpose | Mutating? | Min Role |
|---|---|:---:|---|
| `code-review` | Static analysis, secret scan, style feedback on a diff | ❌ | dev |
| `test-runner` | Selects impacted tests, runs suite, reports failures | ❌ | dev |
| `build` | Docker build, tag, push to registry | ✅ | dev |
| `security-scan` | Trivy / Snyk on image + IaC, blocks on CVE severity | ❌ | viewer |
| `deploy` | Helm upgrade or ArgoCD sync | ✅ | dev |
| `verify` | Smoke tests + Prometheus metric check post-deploy | ❌ | dev |
| `rollback` | Reverts to previous stable release | ✅ | admin |
| `incident-triage` | Reads alert, assigns severity, gathers context | ❌ | viewer |
| `diagnose` | Pulls pod logs, recent deploys, resource metrics | ❌ | viewer |
| `remediate` | Restart pod, scale HPA, or trigger rollback | ✅ | admin |
| `terraform-plan` | Generates HCL, runs `plan`, never applies | ❌ | dev |
| `terraform-apply` | Applies an approved plan | ✅ | admin |
| `cost-analyzer` | Finds idle resources, right-sizing proposals | ❌ | viewer |
| `postmortem` | Drafts RCA document from run history | ❌ | viewer |

---

## 💬 Chat Agent — Prompt to Execution

### Routing Logic

```
Prompt
  │
  ├─> 1. Intent Router (LLM, cheap model)
  │      Classifies prompt → agent_slug + extracted parameters
  │
  ├─> 2. RBAC Gate
  │      user.role >= agent.min_role ?  else 403
  │
  ├─> 3. Mutation Gate
  │      agent.is_mutating && env == 'prod' ?  → require approval
  │
  ├─> 4. Execute agent in FastAPI runtime
  │      Stream tokens + tool calls over WebSocket
  │
  └─> 5. Persist to runs + run_steps, write audit_log
```

---

## 🚀 Flightplans — Multi-Agent Workflows

A **Flightplan** is a declarative YAML graph. See [`flightplans/`](flightplans/) for the seeded examples:

- [`ship-to-prod.yaml`](flightplans/ship-to-prod.yaml) — review → test → build → scan → **human approval** → deploy → verify → auto-rollback on failure
- [`incident-response.yaml`](flightplans/incident-response.yaml) — auto-triggered by Alertmanager webhook: triage → diagnose → remediate (guardrailed) → notify → postmortem
- [`cost-sweep.yaml`](flightplans/cost-sweep.yaml) — nightly cron: find idle resources → open a right-sizing PR (plan only, never applies)

---

## 🔌 API Endpoints

### Node BFF (`:4000`)

| Method | Route | Role | Purpose |
|---|---|---|---|
| POST | `/api/auth/login` | public | Email + password → JWT pair |
| POST | `/api/auth/refresh` | public | Rotate access token |
| POST | `/api/auth/logout` | any | Revoke refresh token |
| GET | `/api/me` | any | Current user + permissions |
| GET | `/api/agents` | any | List agents visible to role |
| POST | `/api/chat` | dev+ | Proxy to FastAPI, streams back |
| GET | `/api/flightplans` | any | List |
| POST | `/api/flightplans` | dev+ | Create |
| POST | `/api/flightplans/:id/execute` | dev+ | Trigger a run |
| GET | `/api/runs` | any | Run history |
| GET | `/api/runs/:id` | any | Run detail + steps |
| POST | `/api/runs/:id/approve` | admin | Release a gated step |
| POST | `/api/runs/:id/abort` | dev+ | Stop a running plan |
| WS | `/ws/runs/:id` | any | Live log stream |

### FastAPI Runtime (`:8000`) — internal only

| Method | Route | Purpose |
|---|---|---|
| POST | `/chat` | Route prompt → agent → stream |
| POST | `/agents/{slug}/run` | Direct agent execution |
| POST | `/flightplans/execute` | Orchestrate the step graph |
| GET | `/health` | Liveness |

### Flask Control Plane (`:6001`) — admin only

| Method | Route | Purpose |
|---|---|---|
| GET/POST | `/admin/users` | User CRUD |
| PATCH | `/admin/users/<id>/role` | Change role |
| GET | `/admin/audit` | Audit log with filters |
| POST | `/webhooks/github` | PR / push events |
| POST | `/webhooks/alertmanager` | Fire incident Flightplan |

---

## 📁 Project Structure

```
opssquad/
├── frontend/                    # React + Vite
├── bff/                         # Node.js + Express
├── runtime/                     # FastAPI — agents live here
├── control/                     # Flask — admin & webhooks
├── db/                          # migrations + seed.sql
├── flightplans/                 # YAML definitions
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
| Flask | 6001 |
| PostgreSQL | 5432 |
| Redis | 6379 |

---

## ✅ Best Practices Built In From Day One

**Security**
- Every agent gets its **own IAM role / kubeconfig** — never one god credential
- Mutating agents in prod **always** pass through an approval gate
- Store cloud creds in **Vault or AWS Secrets Manager**, never in Postgres
- Re-verify RBAC in FastAPI — the BFF check is convenience, not security

**Reliability**
- Hard timeout on every agent (`timeout_sec`) — LLMs hang
- Every tool call **idempotent** where possible, so retries are safe
- `reasoning` persisted on each step — you cannot debug or trust an agent without it
- A **kill switch**: one flag that blocks all mutating agents instantly

**Cost & Performance**
- Small model for the intent router, a large one only for reasoning agents
- Read-only tool output cached in Redis (cluster state, cost data) for 5–15 min
- Token caps per run and per user per day

**Rollout Order**
1. Auth + RBAC + dashboard shell
2. Chat agent with **read-only agents only** (`diagnose`, `cost-analyzer`)
3. Run history and log streaming
4. Flightplans with approval gates
5. Mutating agents, staging first — prod last

---

## 🗺️ Roadmap

| Phase | Deliverable |
|---|---|
| **v0.1** | Auth, RBAC, dashboard, 3 read-only agents via chat |
| **v0.2** | Run history, live WebSocket logs, audit trail |
| **v0.3** | Flightplan engine + YAML definitions + approval gates |
| **v0.4** | Mutating agents (build, deploy, rollback) on staging |
| **v0.5** | Incident response auto-trigger from Alertmanager |
| **v1.0** | Prod-ready: Vault integration, kill switch, cost caps, SSO |
