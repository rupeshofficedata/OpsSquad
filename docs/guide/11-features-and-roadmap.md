[← Back to Index](../INDEX.md)

# 11 — Features, Trade-offs & Roadmap

## What's real and tested end-to-end right now

- **Auth & RBAC** — full login/refresh/logout cycle, JWT verified
  independently at both the BFF and FastAPI, per-agent and
  per-Flightplan-step role checks. Details: [Security](09-security.md).
- **Chat routing without an LLM** — a tokenized keyword+stemming matcher
  correctly routes natural-language prompts to the right agent with zero
  API key.
- **Per-user model choice** — Claude or a self-hosted OpenAI-compatible
  server, picked per user, threaded through every execution path including
  Flightplan resume-after-approval. Tested end-to-end against a real local
  `llama-server` (Qwen2.5-Coder-7B-Instruct, GPU-accelerated) — including
  working around a GTX 1080/Pascal CUDA-kernel gap by self-building CUDA
  for `sm_61` (~37 tok/s generation, 6-10x faster prompt processing than
  the Vulkan fallback). **Caveat found by testing:** a quantized 7B
  model's tool-call compliance is inconsistent — reliable for some
  agents, silent (describes intent in prose instead of calling the tool)
  for others. Not an OpsSquad bug — a known small-model characteristic;
  Claude doesn't have this problem.
- **28 of 29 tools are real**, not simulated, by default. Full table:
  [Tools](08-tools.md).
- **Local-model status + start** — a live indicator and "Start model"
  button on the Chat page, backed by a tiny host-run control agent
  (`local-model-control/agent.py`) since pods inside kind can't touch the
  host's process table.
- **GitHub webhook → real trigger** — a push to `main`/`master` maps into a
  real `ship-to-prod` run.
- **In-process cron scheduler** — no separate CronJob container; reads
  every Flightplan's `trigger.cron` fresh from the DB each tick.
- **15 agent modules** with actual decision logic (not tool-call echoes) —
  see [Agents & Flightplans](07-agents-and-flightplans.md).
- **Flightplan engine** — dependency-ordered steps, conditions, approval
  gates, policy/guardrail enforcement, correct final-status computation,
  cooperative abort (checked between steps). Live-tested: aborted a
  `ship-to-prod` run mid-flight, confirmed it never reached the approval
  gate.
- **Two independent mutation gates** (agent-level prod gate + per-command
  gate) — see [Security](09-security.md).
- **Admin panel** — user CRUD, role changes, full audit trail.
- **Real self-hosted Vault** (k8s path), with startup retry through its
  known dev-mode restart window — see [Security](09-security.md).

## What's still simulated by default

`cloud.cost_explorer`, `slack.post`, `pagerduty.read` are real API calls
but need a credential you supply — fail closed with a clear message if
unset, not a silent fake success. `runs.read` is always simulated (it's an
introspective query over this app's own data, not an external system). No
real cloud provider, container registry beyond the self-hosted one, Slack
workspace, or PagerDuty account is touched unless you configure one.

## Advantages (why this design)

- **Approval gates are structural, not bolted on** — two independent
  layers (agent-level, per-command), each checked server-side regardless
  of what the UI shows.
- **Least privilege is enforced by the platform itself**, not just
  documented — Vault policies, Kubernetes RBAC, and per-tool credential
  scoping all independently narrow what each piece can touch.
- **Nothing is a black box** — every tool call's exact input/output,
  every agent's reasoning, every approval decision is persisted and
  visible, not summarized away.
- **Works offline** — the simulated fallback isn't a stub that always
  succeeds; it's deterministic, input-seeded fake data realistic enough to
  exercise real pass/fail branching in every agent.

## Disadvantages / honest limitations

- **No visual Flightplan builder** — `POST /flightplans` exists, but new
  pipelines are hand-written YAML; only the 3 seeded ones launch from the UI.
- **Vault has no HA** in the k8s path — dev mode, in-memory, self-heals via
  a CronJob rather than being genuinely durable. See
  [Security](09-security.md) for the mitigation already in place.
- **No SSO.**
- **No per-user/per-day token or cost caps.**
- **Redis is underused** — only refresh tokens today; tool-output caching
  (cluster state, cost data) is a natural, cheap follow-up.
- **WebSocket updates are DB-polling underneath** (`bff/src/ws/streamRuns.js`,
  every 2s) — works, but a Redis pub/sub push would be more efficient at
  scale.

## Roadmap (roughly in dependency order toward production use)

1. **Flightplan builder UI** — the API already exists.
2. **HCP Vault / a real storage backend** — the local dev-mode Vault
   proves the integration genuinely works; production needs durable
   storage + real auto-unseal.
3. **SSO** and **per-user/per-day cost caps.**
4. **Redis-backed tool-output caching** once more real tools accumulate
   meaningful read latency worth caching.

## How this page relates to `docs/superpowers/plans/`

This page describes what's **already built**. Features still being
designed live as plan docs in
[`docs/superpowers/plans/`](../superpowers/plans/) — check
[`STATE.md`](../STATE.md) before assuming a plan file describes something
not yet built; it might already be done and just not archived out of that
folder yet.

Back to: [Overview →](01-overview.md) · [Index →](../INDEX.md)
