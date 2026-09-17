# Per-command mutation gate + table-rendered summaries

Source: user request, refined via /grillme.

## Decisions

- **Not a rebuild** — layers onto the existing tool-use loop (plan-in-reasoning
  → execute → see result → continue → final summary), already built/tested.
- **Scope**: chat AND Flightplan runs. Flightplan runs are pre-approved by
  their own YAML (a step declaring a mutating tool IS the human's advance
  sign-off) — never pause. Chat runs pause for the requesting user.
- **Mutating tool set** (needs a gate): `kubectl.restart`, `kubectl.scale`,
  `terraform.apply`, `helm.upgrade`, `helm.rollback`, `argocd.sync`,
  `argocd.rollback`, `docker.build`, `docker.tag`, `registry.push`. Everything
  else (reads, scans) never gates.
- **Role bar**: viewer → denied outright, no pause, synthesized error result.
  dev/admin → paused, Approve/Deny.
- **Confirm UX**: dedicated Approve/Deny buttons showing the exact
  tool+input, not a free-text reply.
- **Stacks with the existing gate**: prod + mutating agent still needs admin
  sign-off before the run starts (unchanged). Once running, each individual
  mutating command additionally pauses for the requesting user.

## Design

API structural constraint: once a model turn proposes N tool calls, every
one of those N must get an answer before the next turn — so a gate on any
call in a round pauses the **whole round** (nothing in it executes yet),
not just the gated call. On resume, every call in that round executes (the
gated one per the decision, the rest normally, since they were never
denied — just held alongside).

Reuses the ask_user pause/resume architecture almost exactly (`runs.inputs`
JSONB stash, `set_run_pending_state`, resume-from-last-assistant-turn) —
same shape, new trigger (system-intercepted, not model-initiated) and new
status (`awaiting_command_approval` vs `awaiting_user_input`).

### Backend
- `executor.py`: `MUTATING_TOOLS` set. `run_agent`/`_run_with_claude`/
  `_run_with_local_llm` gain `user_role`, `pre_approved`. Before executing a
  round's tool calls (when not pre_approved): if any call's tool is in
  `MUTATING_TOOLS` — role insufficient → synthesize a denial result *inline*
  for just that call, execute the rest of the round normally, no pause. Role
  sufficient → pause the whole round, return `pending_command` (tool+input,
  for display).
- New `execute_paused_round(model_provider, messages, approved)` — derives
  the paused round's calls from `messages[-1]` (same pattern as
  `build_resume_messages`), executes every call (gated one per `approved`,
  rest normally), returns updated messages + the tool_calls executed.
- New run status `awaiting_command_approval` (enum + fresh-schema SQL).
- `routes/chat.py`: `POST /chat/{run_id}/approve-command` and
  `.../deny-command` — same ownership check as `/reply`, resume via
  `execute_paused_round` then continue the loop (`run_agent(history=...)`),
  same backgrounding pattern as `/reply`.
- `graph.py`: `_run_steps` passes `pre_approved=True` to `run_agent` — never
  gates (Flightplan's own trigger-role check at the execute endpoint already
  guarantees dev+).

### Frontend
- `FormattedText`: parse `| col | col |` markdown tables, render as real
  `<table>`.
- `Chat.jsx`: `awaiting_command_approval` renders the pending command +
  Approve/Deny buttons (new small component, sibling to `ReplyBox`).
- `client.js`: `approveCommand`/`denyCommand`.

## Known simplification

If a single round proposes two *different* mutating calls (rare), both are
approved/denied together as one decision — no per-call granularity within
a round. Documented as a `ponytail:` comment at the gate, not built further
unless it's actually hit.

## Verification

- Live test: dev user chat-triggers a scale/restart → sees Approve/Deny →
  approve → real kubectl.scale executes, confirmed via `kubectl get`.
- Deny path: same, confirms nothing executed, model's next round reacts to
  the denial text.
- Viewer role: mutating command auto-denied, no pause, clear error surfaces
  in the final summary.
- Flightplan run: mutating step executes without any pause (pre-approved).
- Table rendering: a prompt whose summary includes a markdown table renders
  as a real table, not literal `|` characters.
