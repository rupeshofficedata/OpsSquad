[← Back to Index](../INDEX.md) · [← Architecture](02-architecture.md)

# 06 — Database

PostgreSQL 16. One migration file, [`db/migrations/001_init.sql`](../../db/migrations/001_init.sql).
Default users + the agent registry are seeded by [`db/seed.sql`](../../db/seed.sql).

## Entity relationship diagram

```mermaid
erDiagram
    users ||--o{ flightplans : "created_by"
    users ||--o{ runs : "triggered_by"
    users ||--o{ audit_logs : "user_id"
    flightplans ||--o{ runs : "flightplan_id"
    agents ||--o{ runs : "agent_id (chat runs)"
    runs ||--o{ run_steps : "run_id"
    runs ||--o{ chat_messages : "run_id"

    users {
        uuid id PK
        varchar email UK
        varchar password_hash "bcrypt cost 12"
        user_role role "admin | dev | viewer"
        varchar model_provider "anthropic | local"
        varchar tool_call_mode "lenient | strict"
    }
    agents {
        uuid id PK
        varchar slug UK "code-review, deploy, ..."
        jsonb tools "declared tool list"
        boolean is_mutating
        user_role min_role
    }
    flightplans {
        uuid id PK
        varchar slug UK
        jsonb definition "step graph"
        varchar env "staging | prod"
    }
    runs {
        uuid id PK
        varchar kind "chat | flightplan"
        run_status status
        jsonb inputs "resume state"
        jsonb result
    }
    run_steps {
        uuid id PK
        int step_order
        varchar agent_slug
        jsonb tool_calls
        run_status status
    }
    chat_messages {
        uuid id PK
        varchar role "user | agent | tool"
        text content
    }
    audit_logs {
        bigint id PK
        varchar action
        varchar target
        jsonb metadata
    }
```

## `run_status` — the state machine every run moves through

```mermaid
stateDiagram-v2
    [*] --> queued
    queued --> running
    running --> awaiting_approval: agent-level prod gate
    running --> awaiting_user_input: agent called ask_user
    running --> awaiting_command_approval: agent proposed a mutating tool call
    awaiting_approval --> running: admin approves
    awaiting_user_input --> running: user replies
    awaiting_command_approval --> running: user approves/denies
    running --> success
    running --> failed
    running --> aborted
    running --> skipped
    success --> [*]
    failed --> [*]
    aborted --> [*]
    skipped --> [*]
```

## Table notes (the non-obvious parts)

- **`runs.kind`** is `'chat'` or `'flightplan'` — one table for both, so
  Dashboard/audit history doesn't need a union query.
- **`runs.inputs`** doubles as resume state — a paused run's
  still-unanswered assistant turn (for `ask_user` or the per-command gate)
  is stashed here, not in a separate table, then read back by
  `execute_paused_round`/`build_resume_messages` on resume.
- **`run_steps.step_id`** is the Flightplan step's own YAML id (`"review"`,
  `"deploy"`) — `NULL` for chat runs, which have no step graph.
- **`run_steps.tool_calls`** is the full per-call `{tool, input, result}`
  trace — this is what `ToolCallDetail.jsx` renders, not `output` alone.
- **`chat_messages`** only exists to let a chat run resume with full
  history after an `ask_user` pause — it's not a general chat log.
- **`audit_logs`** is append-only, admin-viewable only, and is written for
  every mutating action anywhere in the system (Flightplan approval,
  per-command approve/deny, admin role change, etc.).

Next: [Runtime →](05-runtime.md) · [Agents & Flightplans →](07-agents-and-flightplans.md)
