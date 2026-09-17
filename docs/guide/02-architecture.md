[← Back to Index](../INDEX.md)

# 02 — Architecture

## System diagram

```mermaid
flowchart TB
    subgraph Browser
        FE["React Frontend :5173<br/>Login · Dashboard · Chat · Flightplans · Runs · Admin"]
    end

    FE -->|"HTTPS + WebSocket, JWT in header"| BFF

    subgraph BFF["Node.js API Gateway / BFF :4000"]
        direction TB
        B1["Issue & verify JWT"]
        B2["RBAC middleware (convenience gate)"]
        B3["WebSocket log streaming"]
        B4["Proxy to FastAPI"]
    end

    BFF -->|"proxied requests"| RT

    subgraph RT["FastAPI Runtime :8000"]
        direction TB
        R1["/chat, /agents/*/run, /flightplans/*"]
        R2["/admin/* (users, roles, audit)"]
        R3["/webhooks/* (GitHub, Alertmanager)"]
        R4["Orchestrator (tool-use loop, Flightplan graph)"]
    end

    RT --> TOOLS["Tool Layer<br/>kubectl · terraform · trivy · docker · helm · argocd · git · prometheus · ..."]

    RT --> PG[("PostgreSQL :5432<br/>users, agents, flightplans,<br/>runs, run_steps, audit_logs")]
    BFF --> RD[("Redis :6379<br/>refresh tokens")]

    RT -.->|"secrets at startup"| V[("Vault<br/>(k8s deployment only)")]
    BFF -.->|"secrets at startup"| V
```

**Why this shape:** the BFF is the only thing the browser ever talks to —
it never proxies raw LLM/tool calls, only issues/verifies JWTs and forwards
already-authenticated requests to FastAPI. FastAPI is the *only* Python
service — an earlier version split it into a second Flask "control plane"
for admin/webhooks, but that added an extra service, an extra dependency
tree, and an extra HTTP hop for no real gain, so it was folded back in.

## Request flow: login

```mermaid
sequenceDiagram
    participant U as Browser
    participant B as BFF (Node)
    participant P as Postgres

    U->>B: POST /api/auth/login {email, password}
    B->>P: fetch user, verify bcrypt hash
    P-->>B: user row
    B-->>U: Access Token (15min) + Refresh Token (7d, httpOnly cookie)
    Note over U: Access token kept in memory only
    U->>B: every request: Authorization: Bearer <access token>
    B->>B: verify JWT, attach req.user, check RBAC route map
```

Full RBAC matrix and JWT payload shape: [Security](09-security.md).

## Request flow: chat

```mermaid
sequenceDiagram
    participant U as User
    participant C as Chat page
    participant B as BFF
    participant R as FastAPI Runtime
    participant T as Tools

    U->>C: free-text prompt
    C->>B: POST /api/chat
    B->>R: POST /chat
    R->>R: Always uses one agent: 'assistant' (full tool catalog, no per-prompt routing)
    R->>R: RBAC gate (role >= agent.min_role — always passes, min_role='viewer')
    loop each round, streamed live over WebSocket
        R->>T: call proposed tool(s)
        alt any call targets a mutating tool
            R-->>C: pause round, awaiting_command_approval
            U->>C: Approve / Deny
            C->>B: POST /api/chat/:runId/approve-command (or deny)
            B->>R: same, proxied
            R->>T: execute approved call, deny the rest inline if denied
        end
        T-->>R: tool result
    end
    R->>R: persist run + run_steps, write audit_log
    R-->>C: final summary (streamed as it completes)
```

Chat used to route each prompt to one of 15 specialist agents by keyword
match; that's gone (`orchestrator/router.py` deleted) — every chat prompt
now runs against one generalist `assistant` agent with the full tool
catalog, so the model itself decides what's relevant instead of a
keyword-score picking one narrow tool subset in advance. See [Agents &
Flightplans](07-agents-and-flightplans.md).

The **per-command mutation gate** shown above (any single mutating tool
call, any env) is the only mutation gate chat has — the old whole-run
"agent-level, `prod`-only" gate was dropped for chat as redundant.
Flightplans still have their own separate agent-level gate. Both are
covered in depth in [Security](09-security.md), including a real incident
found while shipping this: the per-command gate only lives in the live
LLM paths, and a third path (`_run_simulated`, used when no LLM is
reachable) had no gating at all until it was fixed.

## Request flow: a Flightplan run

```mermaid
flowchart LR
    T["Trigger:<br/>manual / cron / GitHub webhook / Alertmanager webhook"] --> S1
    subgraph "ship-to-prod.yaml"
        S1[review] --> S2[test] --> S3[build] --> S4[scan] --> S5{{"approval<br/>(admin gate)"}}
        S5 -->|approved| S6[deploy] --> S7[verify]
        S7 -->|failed| S8[rollback]
        S7 -->|passed| DONE1([done])
        S8 --> DONE2([done, reported failed])
    end
```

Steps run in dependency order (`needs`), can carry a `when` condition, a
`policy.block_on` (fail the whole run on a matching severity), or
`guardrails` (clamp/refuse an out-of-bounds mutating action). A Flightplan
run never hits the per-command chat gate above — a step declaring a
mutating tool **is** the human's advance sign-off. Full anatomy: [Agents &
Flightplans](07-agents-and-flightplans.md).

## Directory map

```
frontend/   React + Vite            → 03-frontend.md
bff/        Node.js + Express       → 04-bff.md
runtime/    FastAPI (Python)        → 05-runtime.md
db/         Postgres schema + seed  → 06-database.md
flightplans/ YAML pipeline defs     → 07-agents-and-flightplans.md
k8s/        kind cluster manifests  → 10-kubernetes-deploy.md
```

Next: [Database →](06-database.md) · [Agents & Flightplans →](07-agents-and-flightplans.md)
