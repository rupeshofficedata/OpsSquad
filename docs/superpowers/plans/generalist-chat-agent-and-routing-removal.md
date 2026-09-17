# Generalist chat agent — drop narrow per-prompt routing

Source: user request, refined via /grillme.

## Root cause (found during grilling, not assumed)

`orchestrator/router.py::_classify_by_keyword` routes every chat prompt to
exactly one of 15 specialist agents by keyword-overlap score against that
agent's slug/name/description. A prompt with zero overlap against every
agent (e.g. "give me cluster name" — no agent mentions "cluster"/"name")
scores 0 everywhere; the loop's `if score > best_score` never fires (`0 >
0` is false), so `best` stays `None` and falls through to `best =
agents[0]` — whichever agent happens to be first in DB order, silently,
with no error. The model then gets that unrelated agent's narrow 2-4-tool
subset and one-sentence system prompt for a completely different question.

Confirmed side effect: `routes/chat.py` RBAC-checks the *misrouted* agent's
`min_role` — a viewer/dev asking an unrelated question can get a
confusing 403 if the fallback agent happens to need `admin` (e.g.
`remediate`, `rollback`).

Separately, even correctly-routed agents give weak answers: each
`system_prompt` (`db/seed.sql`) is one sentence with no explicit
plan → execute → verify → summarize instruction — the tool-use loop
mechanics already do this (`executor.py`, identical for Claude and local
providers), the model just isn't told to trust and use that loop
deliberately.

`classify()` has exactly one call site (`routes/chat.py`) — nothing else
(Flightplans, webhooks, direct `/agents/{slug}/run`) depends on it.

## Decisions from grilling

- **Chat gets one new generalist agent** (full tool catalog — all 29
  registered tools) instead of picking one narrow specialist per prompt.
  The 15 specialists are unchanged and keep serving Flightplans and direct
  `/agents/{slug}/run`.
- **`min_role='viewer'`** on the new agent — chat stays open to viewers for
  read-only use, same as today. Mutation safety is enforced per actual
  tool call (the existing per-command mutation gate — viewer auto-denied,
  dev/admin pause-to-approve), not by which agent got selected.
- **Drop the old whole-run gate for chat** (`agent["is_mutating"] and
  env == "prod"` in `routes/chat.py`, which blocked the entire run before
  it started). Redundant now that every mutating tool call is individually
  gated, and wrong for one agent that does both mutating and read-only
  things — a pure read-only prod question shouldn't need upfront approval
  just because the agent *could* mutate. `routes/agents.py`'s direct-invoke
  path keeps this gate unchanged (out of scope — decision was chat-specific).
- **Both providers, one code path** — Claude and local-model chat both use
  the generalist agent. No provider-specific branching.
- **`router.py` is deleted**, not deprecated — single call site removed,
  nothing else references it, no tests reference it.

## Design

### Database (`db/seed.sql`)
New row in the `agents` INSERT:
```sql
('assistant', 'Assistant', 'General-purpose chat agent with the full tool catalog',
 '<system prompt below>',
 '["kubectl.get","kubectl.logs","kubectl.restart","kubectl.scale","terraform.plan","terraform.apply",
   "trivy.scan","docker.build","docker.tag","helm.upgrade","helm.rollback","argocd.sync","argocd.rollback",
   "cloud.cost_explorer","slack.post","pagerduty.read","git.diff","git.log","secrets.scan","lint.run",
   "test.run","test.select","registry.push","registry.pull","iac.scan","http.smoke_test",
   "prometheus.query","alertmanager.read","runs.read"]',
 TRUE, 'viewer', 300)
ON CONFLICT (slug) DO NOTHING;
```
(All 29 tool names — cross-checked 1:1 against `tools/schemas.py` and
`tools/stubs.py`'s `SIMULATED_TOOLS`, no `KeyError` risk building
`tool_defs`.) `is_mutating=TRUE` stays accurate (it genuinely can call
mutating tools) — irrelevant to chat once the gate below is dropped, still
correct if ever hit via `/agents/assistant/run` directly.

Proposed system prompt:
> You are OpsSquad's general-purpose DevOps assistant, running with real
> tools against a real Kubernetes cluster. Given a question: (1) plan
> which tool call(s) will actually get you the answer — don't guess or
> assume state you haven't checked; (2) call them — you never execute
> anything yourself, the platform runs exactly what you call and returns
> the real result; (3) review each result, and call again if it didn't
> answer the question or revealed you need more; (4) once you have what
> you need, give a clear, direct answer to exactly what was asked — not a
> dump of raw tool output. If no available tool can answer the question,
> say so plainly instead of guessing. Mutating tools (deploy/scale/
> restart/apply/etc.) pause for the user's explicit approval before they
> run — propose the call, you don't need to ask permission yourself.

**Already-bootstrapped clusters:** `k8s/20-migration-job.yaml` skips
`seed.sql` entirely once the `agents` table exists (checks
`to_regclass('public.agents')`) — re-running `bootstrap.sh` will NOT pick
up this new row on the current dev cluster. One-off manual `INSERT` needed
during verification; not fixing that skip-logic here (separate, unrelated
concern).

### Backend (`runtime/app/routes/chat.py`)
- Drop `from app.orchestrator.router import classify`.
- `POST /chat`: replace `agents = await repo.list_agents(); match =
  await classify(...); agent = await repo.get_agent(match.agent_slug)`
  with a fixed lookup: `agent = await repo.get_agent("assistant")`.
  `params = {"prompt": req.prompt}` directly.
- Delete the `if agent["is_mutating"] and req.env == "prod":`
  awaiting-approval block entirely.
- RBAC check (`role_at_least(user.role, agent["min_role"])`) stays as-is —
  harmless no-op now since `min_role='viewer'` always passes, kept for
  consistency/defense-in-depth rather than special-cased away.

### Delete
- `runtime/app/orchestrator/router.py` — entire file.

### Not touched
- `runtime/app/orchestrator/executor.py` — tool-use loop already does
  exactly the plan→execute→verify pattern; zero mechanical changes needed.
- `runtime/app/routes/agents.py` — direct single-agent invocation keeps
  its existing `is_mutating && prod` gate.
- `runtime/app/orchestrator/graph.py` — Flightplans untouched.
- The 15 specialist agents — untouched, still used by Flightplans.
- Frontend — `Chat.jsx` already just displays whatever `agent` slug comes
  back; will now always show `"assistant"` instead of varying per prompt.
  Accepted, not a bug.

## Verification

- `python -m py_compile` every touched/deleted-from file.
- `grep -rn "router\|classify"` across `runtime/app` confirms no dangling
  references after deletion.
- Manually `INSERT` the new agent row into the live dev DB (migration Job
  won't do it — see above).
- Rebuild `opssquad/runtime:local`, `kind load docker-image`, rollout
  restart.
- Live test as `viewer`: a vague prompt ("give me cluster name" / "what
  pods are running") gets a real answer via a real tool call, no 403, no
  misroute.
- Live test as `dev`/`admin` in `env=prod`: a read-only question answers
  immediately (no upfront approval block); a mutating request (e.g.
  "scale redis") still pauses via the per-command gate, not the old
  whole-run gate.
- Live test as `viewer`: a mutating request gets the inline denial (per
  the existing per-command gate), not a 403 at agent-selection time.
