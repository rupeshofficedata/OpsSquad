# Test cluster + workflow improvements

Written after running the 24-question eval (`evals/`) against a local Bonsai 27B model and reading every
tool failure recorded in the live cluster's run history (77 chat runs). Everything below is backed by a
command or query result, not by assumption; the evidence is cited inline.

## 1. What blocked testing (inventory)

### Permissions / RBAC
| # | Blocker | Evidence | Effect |
|---|---|---|---|
| P1 | Runtime Role is over-broad *and* incomplete: full read/write/delete on **all secrets and services**, but no `events`, `replicasets`, `configmaps`, `jobs`, `nodes`, `pods/exec`, no other namespaces, pods get/list only | `k8s/05-rbac.yaml`; `kubectl auth can-i --as=system:serviceaccount:opssquad:runtime` | "why is it crashing" and "cluster name" are unanswerable; a model-driven runtime can rewrite every secret |
| P2 | Mutation role bar `("dev","admin")`; viewer path never exercised | `executor.py:60` | role matrix untested |

### Approval gate
| # | Blocker | Evidence | Effect |
|---|---|---|---|
| G1 | The eval runner denies every gated call by design, so **10 mutating tools have never run in an eval** (`kubectl.restart/scale`, `helm.upgrade/rollback`, `argocd.sync/rollback`, `terraform.apply`, `docker.build/tag`, `registry.push`) | run history: `docker.build` Denied x9, `kubectl.scale` Denied x7 | only "pauses + denied" is proven, never "executes correctly after approval" |
| G2 | Paused runs never expire: 4 runs stuck since 09-17 (3 `awaiting_user_input`, 1 `awaiting_command_approval`) | `SELECT ... FROM runs WHERE status LIKE 'awaiting%'` | Dashboard clutter, leaked state |
| G3 | After a denial the model re-proposes the same command: Q20 made 8 approval requests over 33 rounds and ended with an empty answer | eval Q20 | approval spam; no denial memory or per-run approval budget |
| G4 | `env` (staging/prod) selector is a no-op in chat | `grep req.env runtime/app/routes/chat.py` = 0 matches | UI implies prod-safety that doesn't exist |
| G5 | No approve-in-test mode (`pre_approved` exists only for Flightplans) | `executor.py run_agent` | can't run mutating scenarios unattended, even in a throwaway cluster |

### Missing credentials / external services
| # | Blocker | Evidence |
|---|---|---|
| C1 | `cloud.cost_explorer`: AWS creds absent | fails x6 |
| C2 | `slack.post`: webhook absent | fails x5 |
| C3 | `pagerduty.read`: token absent; reads **one incident by id, cannot list**; base URL hard-coded (`real.py:525`) so it can't be pointed at a sandbox | fails x1; Q08 |
| C4 | No Anthropic key, and Vault reads are (correctly) blocked for the agent | Q: no frontier baseline |

### Missing tools / thin data / absent infra
| # | Blocker | Evidence |
|---|---|---|
| T1 | Models invent tools that don't exist: `registry.list`, `docker.images`, `kubectl.config` | "tool not registered" x4 = demand signal |
| T2 | `kubectl.logs` returns only line/error **counts**; `kubectl.get` returns summaries; no events/describe | Q01, Q16, Q18 |
| T3 | `prometheus.query` returns `series_returned` + `error_rate`, not values; Prometheus scrapes exactly **1 target** (the kind node's cadvisor, job `kubernetes-cadvisor`; verified with `/api/v1/targets`) and no app, kube-state-metrics or node-exporter targets, so there is nothing app-level to query | Q02; 4x HTTP 400 from invented metrics |
| T4 | Alertmanager has never had an alert | Q06 always 0 |
| T5 | No incident data anywhere | Q08 |
| T6 | Tool args not validated: `trivy.scan` accepted `/path/to/runtime/container/filesystem` | run-history FATAL |
| T7 | Name friction: pod name used as deployment name (`deployments.apps "postgres-c9d67bf79" not found`) ~8 NotFound errors | run history |
| T8 | Registry has one image but nothing can list it; `registry.pull` on a guessed tag = "manifest unknown" | run history, Q17 |

### Access / environment
| # | Blocker | Evidence |
|---|---|---|
| E1 | **Version skew on the live cluster**: runtime is new, BFF/frontend are old (no `/threads` route, no "Show steps"); no deploy pipeline | `grep threads` in live bff = 0; `grep "Show steps"` in live frontend = 0 |
| E2 | **Schema drift** hit twice (compose volume; kind DB missing `thread_id`) because `001_init.sql` is run-once and non-idempotent, with no migration runner | chat 500s |
| E3 | Local-model plumbing: host llama-server on a fixed port, ufw allows only the kind subnet, one model at a time on the GPU, runtime 120 s read timeout, 8K context | ufw change needed approval; 3 cancelled requests |
| E4 | UI never driven with the local model: Chrome extension not connected, Playwright's Chrome missing | browser phase ran via API |
| E5 | Fragile local access: stale `kubectl port-forward`s squatted ports 4000/5173 twice | session log |
| E6 | Thin test infra: no pytest on host, 12 test files, no e2e until `evals/`; thread summarization (>10 runs) never exercised live; `ask_user` flow and Q21-Q24 not run | repo scan |

## 2. Agent behaviour seen in results (Bonsai PTQ1_0, 16/20)
- Over-exploration: Q02 nine PromQL variants, Q01 nine calls, Q18 twelve calls / 514 s.
- **Step cap ends silently**: at `MAX_TOOL_ITERATIONS=8` the loop returns the last text (often empty or cut off) with no "I ran out of steps" summary.
- Context exhaustion: two requests reached 8,191 tokens (`truncated = 1`), giving a cut-off answer (Q18) and an empty one (Q20).
- Retry-after-denial (Q20), false all-clear headline (Q08), full secret echoed in an answer (Q11).
- Timeouts hide their cause: a request cancelled at 120 s becomes "Simulated run completed" with no recorded reason.

## 3. Root-cause themes
1. The harness can't tell "cannot do this" from "did not try": thin tool data, guessed names, no capability description.
2. Approval is binary and stateless: no denial memory, no expiry, no scoped approval, no test mode.
3. There is no sandbox: every credentialed or mutating path is either untested or risky.
4. Deploy and migrations are manual, so drift is the normal state.
5. The agent loop has no guardrails: step budget, duplicate-call detection, forced conclusion, context budget.

## 4. Improvement plan (ordered by value / cost)

**P0: agent-loop guardrails** (`executor.py`, small, model-independent). **Status: items 1-6 built, unit-tested (`test_loop_guardrails.py`), not yet measured against a model.** Differences from the plan text: item 2's cap is 4 calls of one tool per run (`MAX_CALLS_PER_TOOL`); item 3 has denial memory but not the "max 2 approvals" cap or a persisted reason; item 4 covers chat pauses only (Flightplan approvals excluded); item 5's fallback reason is already in the run's reasoning text, no UI badge change; item 6 trims old tool output above `LOCAL_LLM_MAX_HISTORY_CHARS` (16000 chars), no summarization. The forced conclusion is untested against Claude (no API key here).
1. Forced conclusion: on step cap, repeated call, or context near-full, make one final tool-less call ("summarize what you found and what blocked you").
2. Duplicate-call detector: same tool+args twice, or same tool 3x with no new information, injects "you already have this result" and stops the loop after N.
3. Denial memory: a denied tool is not re-proposed in the same run; cap approvals per run at 2; persist the reason.
4. Paused-run TTL (30 min) that auto-aborts with a note; close the 4 stuck runs.
5. Configurable `LOCAL_LLM_READ_TIMEOUT`; persist the fallback reason on the step and show it in the UI badge.
6. Context budget: trim/summarize old tool output before overflow; `LOCAL_LLM_CTX` config.

**P1: tools and permissions**
7. Richer read tools: `kubectl.logs` returns a bounded, redacted tail; `kubectl.events`/`describe`; `prometheus.query` returns top-N values + `prometheus.targets`; `registry.list`; `pagerduty.list`; `kubectl.contexts` (cluster name).
8. Argument validation + "did you mean": resolve names across pods/deployments; allow-list paths for trivy/iac.
9. One result envelope `{ok, data, error, hint, truncated}`; generate the tool list in the system prompt from the registry so prose can't drift.
10. Redact secrets in tool output and final answers.
11. Least-privilege RBAC: drop secret/service write+delete from the runtime Role, add events/replicasets/jobs get, allow-list deployment names for scale/restart; separate `test` (wide) and `prod` (narrow) Roles; add an `auth can-i` self-check tool.
12. Give `env` real meaning (different gates/targets) or remove it.

**P2: workflow**
13. `make deploy` (build, `kind load`, rollout, migrate) + `/version` (git sha) shown in the UI footer to expose skew.
14. Versioned idempotent migrations (`schema_migrations` table) replacing ad-hoc ALTERs; CI check that fresh init equals migrated.
15. Eval as CI: run a smoke subset + gate scenarios against the test cluster per change; keep a results leaderboard per model.
16. Approval UX: approve-once vs approve-for-run, `APPROVAL_MODE=manual|auto|deny` (auto only in the test cluster), audit rows.
17. Model profiles: store measured ctx / tok/s / recommended reasoning effort+budget per model; warm the prompt cache on start.

**P3:** multi-cluster scope selection (`ask_user` + cluster registry); real-browser UI verification.

## 5. Test environment: `opssquad-test` (built)

Decision: a namespace in the existing `kind-opssquad` cluster, not a second cluster (lighter; ~1 GB RAM on a machine with ~4 GB available). Isolation comes from RBAC, not from a cluster boundary.

`k8s/test/test-env.sh up | down | status`. Files: `k8s/test/test-env.yaml`, `argocd.yaml`, `mocks.py`.

| Blocker | How it is removed |
|---|---|
| P1/P2 permissions | Role `runtime-agent` = all verbs on all resources in `opssquad-test` only. No ClusterRole. Verified: `can-i delete deployments` is yes in `opssquad-test`, no in `opssquad`. |
| G1-G5 approval gate | `APPROVAL_MODE=auto` skips only the pause (`executor.py`, both provider paths). Role check (`dev`/`admin`) stays. `check_approval_mode()` runs at startup and refuses `auto` unless `ENVIRONMENT=test` **and** `KUBE_NAMESPACE=opssquad-test`. Unit-tested (`test_approval_mode.py`). |
| C1 PagerDuty | Mock serves `PTEST001` (triggered) and `PTEST002` (resolved); needs `Token token=test-token`; unknown id gives a real-shaped 404. `PAGERDUTY_API_URL` is now a setting. |
| C2 Slack | Mock webhook accepts the post and keeps it (`/mock/slack`) so a run can assert it landed. |
| C3 AWS Cost Explorer | Mock answers `GetCostAndUsage` (JSON 1.1) via `AWS_ENDPOINT_URL_COST_EXPLORER` (no code change). |
| T3/T4 Prometheus + alerts | Own Prometheus scrapes the mock's `/metrics`; rule `CheckoutHighErrorRate` fires into Alertmanager (annotation links `PTEST001`); Alertmanager posts to the mock sink. |
| T2 unhealthy workload | `crashy` Deployment really crash-loops with a real log line. |
| Argo CD | App `argocd-test` on the public `argocd-example-apps/guestbook` (no hard-coded namespace), synced at an older then latest revision so `argocd.rollback` has history. Runtime may get/patch that one Application only (`resourceNames`). `ARGOCD_APP` / `ARGOCD_NAMESPACE` are now settings. |
| Helm | `canary` installed twice so `helm.rollback` has a prior revision. |
| E-series | Own Postgres (emptyDir), Redis, BFF, runtime, registry. `:test` image tags; the dev namespace's `:local` images and DB are untouched. No Vault. |

Eval: `run_eval.py opssquad --env test` (BFF on `localhost:4100`). Per-question `test` overrides in the JSON: Q07 expects the mock's figures, Q08 looks up `PTEST001`, Q09 asserts the message reached the mock, gated questions become `expect_mutation` (no pause, change really happens, `post_check`, `reset_cmd` undoes it).

Verified live (tool calls inside the test runtime pod): pagerduty.read (hit + 404), cloud.cost_explorer, slack.post, alertmanager.read (1 firing), prometheus.query, helm.rollback, argocd.rollback (rolled back to the older SHA).

**Defect found while building it (T3, fixed):** `prometheus.query` returned only `series_returned` and a down-target ratio, never the sample values, so "what is X right now" could not be answered from any Prometheus. Now also returns `samples` (up to 20 `{metric, value}`).

**Second defect found by the first test-env eval run (fixed):** Q21 ("restart the runtime deployment...") made the agent restart *its own* Deployment, which killed the process mid-run; the run stayed `running` forever (no startup recovery). `main.py` now calls `fail_orphaned_runs()` at startup (marks `running` rows failed; `awaiting_*` pauses are kept). Verified: the orphaned Q21 run flipped to `failed`. Still open: the agent can restart itself at all (`kubectl.restart` accepts any target); consider refusing `deployment/runtime` or asking first.

First test-env eval (Bonsai PTQ1_0, 9 questions): 8 pass, Q21 killed by the self-restart above. Q09 first failed on my own answer check (fixed), the answer was correct. Q02's answer said Prometheus was "in the `opssquad` namespace" while it was `opssquad-test`: `prometheus.query` results carry no scope, same gap as T-namespace earlier.

Not done / known gaps: no frontend in the test namespace (BFF only); `--approve` (exercise the real approval path with a real approval) not built, since auto mode skips it; trivy still needs network for its DB; the namespace shares the cluster's nodes; a ResourceQuota caps it at 30 pods, but there are no CPU/memory limits.

## 6. Sequence
1. **Phase 0 (0.5 day):** P0 items 1-5, close stuck runs, `make deploy` (fixes skew on the dev cluster).
2. **Phase 1 (done):** test namespace, mocks, `PAGERDUTY_API_URL`, `APPROVAL_MODE`, fixtures, `--env test`. Every later item can now be verified for real.
3. **Phase 2 (1-2 days):** tool improvements (items 7-10) verified in the test namespace.
4. **Phase 3:** least-privilege RBAC, migrations, `env` semantics, eval-in-CI, model profiles.

## 7. Decisions
1. Namespace in the existing cluster: chosen by the user.
2. Mocks over real PagerDuty/Slack/AWS accounts: implemented.
3. Code changes made: `PAGERDUTY_API_URL`, `ARGOCD_APP`/`ARGOCD_NAMESPACE`, `APPROVAL_MODE` + startup guard, `prometheus.query` samples. Still open: push to GitHub and commit.
