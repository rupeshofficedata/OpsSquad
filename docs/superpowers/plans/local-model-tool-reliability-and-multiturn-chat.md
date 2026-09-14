# Real tool schemas, strict/lenient switch, multi-turn chat, per-step verification

Source: opssquad-build-plan.md / opssquad-prompts.md (user-supplied), refined via /grillme.

## Decisions from grilling

- **Scope**: build all 4 parts in one pass (not staged).
- **`_TARGET_ALIASES`**: delete once schemas force the right field name — no
  fallback-guessing safety net. Strict mode exists precisely to catch a
  model that still won't comply.
- **`ask_user` pseudo-tool**: only included in `tool_defs` when the run
  originates from `POST /chat`. Cron/webhook/Flightplan runs never see it —
  an unattended run asking a question and hanging forever with nobody to
  answer is the failure mode being avoided.
- **Per-step input/output verification**: already fully wired
  backend-to-DB — `run_steps.tool_calls` JSONB already stores
  `{tool, input, result}` per call, `list_run_steps`/`get_run` already
  `SELECT *`. This is a **frontend-only** gap:
  - `RunDetail.jsx` — render `s.tool_calls` as an expandable per-step list
    (tool name, input args, raw result), not just `s.output`.
  - `Chat.jsx` — same, per assistant message, not just the
    `ResourceCards`/summary extraction that currently discards tool
    name/input.
  No backend or DB change needed for this part.

## Part 1 — Real tool schemas

- `runtime/app/tools/base.py` — tool base class gains a `parameters`
  JSON-schema block (name/type/description/required) instead of blank.
- `runtime/app/tools/real.py` — explicit schema per tool. `kubectl.get`/
  `kubectl.logs`: required `target` string. `kubectl.logs` also optional
  `tail` int (default 200). `kubectl.scale`: required `target` string +
  `replicas` int. Same pattern for trivy/terraform/docker/helm/argocd/etc.
  Delete `_TARGET_ALIASES`/`_resolve_target` once callers are forced onto
  the real field name.
- `runtime/app/orchestrator/executor.py` — `tool_defs` (both
  `_run_with_claude` and `_run_with_local_llm`) pull the real schema per
  tool instead of `properties: {}`.
- `runtime/app/tests/` — test asserting every registered tool has a
  non-empty schema.

## Part 2 — Strict / lenient switch (local model only)

- `db/migrations/00X_tool_call_mode.sql` — `users.tool_call_mode
  VARCHAR(20) NOT NULL DEFAULT 'lenient' CHECK (... IN ('lenient','strict'))`.
- `repo.py` — `get_user_model_preference` returns `tool_call_mode`.
- `executor.py` — `_run_with_local_llm(..., tool_call_mode)`:
  - `lenient` (default): unchanged — all fallback parsers, repeat-loop
    breaker, `final_looks_unexecuted` rewrite.
  - `strict`: only trust the real `tool_calls` field, same rule as Claude.
    No `tool_calls` emitted → stop, return text as-is.
- Thread `tool_call_mode` through `graph.py`, `routes/{agents,chat,
  flightplans}.py` the same way `model_provider`/`model_name` already flow.
- `bff/src/routes/me.js` — `PATCH` accepts/stores `tool_call_mode`.
- `frontend/src/pages/Chat.jsx` — second dropdown next to model_provider
  select, visible only when `model_provider === "local"`.

## Part 3 — Multi-turn conversational chat

- New run status `awaiting_user_input`.
- `db/migrations/00X_chat_threads.sql` — `chat_messages(run_id, role,
  content, created_at)`.
- `ask_user` pseudo-tool: one required string field `question`. Included
  in `tool_defs` only for chat-originated runs (see decision above).
- `executor.py`'s `run_agent` — model calling `ask_user` stops the loop,
  saves the question, sets run status `awaiting_user_input` instead of
  running to completion.
- `routes/chat.py` splits:
  - `POST /chat` — starts run as today; if model calls `ask_user`, returns
    the question + `awaiting_user_input` status instead of a final result.
  - `POST /chat/{run_id}/reply` — appends reply to `chat_messages`,
    resumes loop with full history, repeats until finished or asks again.
- `bff/src/routes/chat.js` — mirrors the split, same JWT/RBAC checks.
- `Chat.jsx` — `awaiting_user_input` renders as an agent question with an
  open reply box tied to that `run_id`, instead of ending the thread.

## Part 4 — Wiring

- `ask_user` included in `tool_defs` for strict AND lenient local-model
  modes, and Claude — real declared tool, not prose-parsing, works cleanly
  under strict mode. Chat-only gating from Part 3 still applies.
- Part 1's schema work must land before testing Part 3 — multi-turn with
  bad schemas is just more turns of wrong guesses.

## Build order

1. Part 1 (tool schemas) — self-contained, fixes the concrete wrong-param
   bug for both Claude and local model.
2. Part 2 (strict/lenient) — small, isolated, A/B-tests Part 1's impact.
3. Frontend per-step verification (RunDetail.jsx + Chat.jsx) — cheap,
   frontend-only, do alongside Part 1/2 so verification is available while
   testing them.
4. Part 3 (multi-turn chat) — biggest lift: DB, both backends, frontend.
5. Part 4 — glue (`ask_user` in every mode's `tool_defs`, chat-only gate).

## Verification

- `python -m py_compile` every touched runtime file.
- Rebuild `opssquad/runtime:local`, `opssquad/bff:local`,
  `opssquad/frontend:local` as needed; `kind load docker-image`; rollout
  restart.
- Run the new schema-completeness test.
- Live-test: ask chat to `kubectl get pods`/`kubectl scale` etc., confirm
  RunDetail/Chat now show the exact `target`/`replicas` args sent — proves
  the schema fix actually reached the model correctly (this was the
  original unverifiable gap).
- Toggle `tool_call_mode` to `strict`, confirm the local model's structured
  `tool_calls` path alone drives execution, no fallback parsing invoked.
- Live-test multi-turn: chat prompt that should trigger `ask_user` (e.g.
  ambiguous scale target), confirm run pauses at `awaiting_user_input`,
  reply resumes it.
- Confirm a cron-triggered (cost-sweep) or webhook-triggered (ship-to-prod)
  run never has `ask_user` in its `tool_defs`.
