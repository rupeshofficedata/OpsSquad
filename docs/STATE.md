# STATE — current state vs. desired state

**Current state** = what's actually implemented in the code on `master`
right now, verified against the running system (tests + a live `kind`
cluster), not just "the diff looks right."

**Desired state** = the design docs in
[`docs/superpowers/plans/`](superpowers/plans/) — written *before*
implementing a feature, as the plan to build against. A plan file staying
in that directory does not mean the feature is unbuilt; check this table.

Last verified: 2026-09-17.

## Gap table

| Plan | Status | Notes |
|---|---|---|
| [`per-command-mutation-gate-and-table-rendering.md`](superpowers/plans/per-command-mutation-gate-and-table-rendering.md) | ✅ Done | Merged `1683691`. Every design item confirmed present in code: `MUTATING_TOOLS` set + gate logic (`executor.py`), `execute_paused_round`, `awaiting_command_approval` status (DB enum + repo.py + routes), `/approve-command`/`/deny-command` (BFF + FastAPI), Flightplan `pre_approved=True` bypass, frontend Approve/Deny UI + markdown table rendering. Live-tested: a chat-triggered `kubectl.scale` paused, was approved, executed for real (`kubectl get` confirmed replica count changed). |
| [`local-model-tool-reliability-and-multiturn-chat.md`](superpowers/plans/local-model-tool-reliability-and-multiturn-chat.md) | ✅ Done | Real per-tool JSON schemas (`tools/schemas.py`), `tool_call_mode` strict/lenient switch, multi-turn chat via `ask_user` + `awaiting_user_input` + `POST /chat/{run_id}/reply`, frontend per-step tool-call rendering (`ToolCallDetail.jsx`). |

**No open gap as of this writing** — both plans in `docs/superpowers/plans/`
are fully implemented and merged. A newly-added plan file with no row here
should be treated as **not yet built**.

## Unplanned fixes shipped since the last plan (not in any plan doc)

These were root-caused and fixed live during a session, not pre-planned —
listed here so "current state" stays accurate even for work that skipped
the plan-first flow:

- **Vault login retry** (`runtime/app/vault.py`, `bff/src/vault.js`, commits
  `e01fc1d` + `24ed4aa`) — both services retry a failed Vault
  Kubernetes-auth login instead of crashing on the first transient 403/DNS
  failure. Root cause: dev-mode Vault (`k8s/06-vault.yaml`) is in-memory
  and wipes its auth config on every restart; a CronJob
  (`k8s/07-vault-init-job.yaml`) self-heals it every 2 minutes, but neither
  service tolerated that window before this fix. Verified against real
  `CrashLoopBackoff` pods in the live cluster, both before (crash logs
  captured) and after (0 restarts across a full `bootstrap.sh` re-run).

## How to use this file

- Before trusting a `docs/superpowers/plans/*.md` file as "what the code
  currently does," check this table first.
- When a plan is implemented, verify it against the running system (not
  just `git diff`), then add/update its row here with the merge commit and
  what was actually confirmed working.
- When you fix something that wasn't planned, add it to the "Unplanned
  fixes" list above so the current-state picture stays complete.
