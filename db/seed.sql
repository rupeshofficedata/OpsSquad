-- OpsSquad seed data: default users + agent registry
-- CHANGE THESE CREDENTIALS before any non-local deployment.

-- ============================================
-- DEFAULT USERS (pgcrypto's crypt/gen_salt gives real bcrypt hashes)
-- ============================================
INSERT INTO users (email, password_hash, full_name, role) VALUES
    ('admin@opssquad.dev',  crypt('Admin@123',  gen_salt('bf', 12)), 'Default Admin',  'admin'),
    ('dev@opssquad.dev',    crypt('Dev@123',    gen_salt('bf', 12)), 'Default Dev',    'dev'),
    ('viewer@opssquad.dev', crypt('Viewer@123', gen_salt('bf', 12)), 'Default Viewer', 'viewer')
ON CONFLICT (email) DO NOTHING;

-- ============================================
-- AGENT REGISTRY
-- ============================================
INSERT INTO agents (slug, name, description, system_prompt, tools, is_mutating, min_role, timeout_sec) VALUES
    ('code-review', 'Code Review', 'Static analysis, secret scan, style feedback on a diff',
     'You are a meticulous code reviewer. Inspect the given diff for bugs, security issues, secrets, and style violations. Be specific and cite line numbers.',
     '["git.diff", "secrets.scan", "lint.run"]', FALSE, 'dev', 300),

    ('test-runner', 'Test Runner', 'Selects impacted tests, runs suite, reports failures',
     'You determine which tests are impacted by a change, run them, and summarize failures with likely root cause.',
     '["git.diff", "test.select", "test.run"]', FALSE, 'dev', 600),

    ('build', 'Build', 'Docker build, tag, push to registry',
     'You build a container image from the given repo/commit, tag it, and push it to the configured registry.',
     '["docker.build", "docker.tag", "registry.push"]', TRUE, 'dev', 900),

    ('security-scan', 'Security Scan', 'Trivy / Snyk on image + IaC, blocks on CVE severity',
     'You scan a container image and IaC templates for vulnerabilities and misconfigurations, and enforce the given severity policy.',
     '["trivy.scan", "registry.pull", "iac.scan"]', FALSE, 'viewer', 600),

    ('deploy', 'Deploy', 'Helm upgrade or ArgoCD sync',
     'You deploy a given chart/image to the target namespace via Helm or ArgoCD, and report the rollout status.',
     '["helm.upgrade", "argocd.sync", "kubectl.get"]', TRUE, 'dev', 600),

    ('verify', 'Verify', 'Smoke tests + Prometheus metric check post-deploy',
     'You run smoke tests against a freshly deployed service and compare key metrics (error rate, latency) against thresholds.',
     '["http.smoke_test", "prometheus.query"]', FALSE, 'dev', 300),

    ('rollback', 'Rollback', 'Reverts to previous stable release',
     'You revert a deployment to its last known-good release and confirm health afterward.',
     '["helm.rollback", "argocd.rollback", "kubectl.get"]', TRUE, 'admin', 300),

    ('incident-triage', 'Incident Triage', 'Reads alert, assigns severity, gathers context',
     'You read an incoming alert, classify its severity (P1-P4), and gather the initial context needed for diagnosis.',
     '["alertmanager.read", "pagerduty.read"]', FALSE, 'viewer', 120),

    ('diagnose', 'Diagnose', 'Pulls pod logs, recent deploys, resource metrics',
     'You gather pod logs, recent deploy history, and resource metrics to build a picture of what is going wrong.',
     '["kubectl.logs", "kubectl.get", "prometheus.query", "git.log"]', FALSE, 'viewer', 300),

    ('remediate', 'Remediate', 'Restart pod, scale HPA, or trigger rollback',
     'You take a bounded remediation action (restart, scale, or rollback) based on diagnosis, respecting the guardrails given.',
     '["kubectl.restart", "kubectl.scale", "helm.rollback"]', TRUE, 'admin', 300),

    ('terraform-plan', 'Terraform Plan', 'Generates HCL, runs plan, never applies',
     'You generate or modify Terraform HCL for the requested change and run `terraform plan`. You never apply.',
     '["terraform.plan", "git.diff"]', FALSE, 'dev', 600),

    ('terraform-apply', 'Terraform Apply', 'Applies an approved plan',
     'You apply a previously reviewed and approved Terraform plan, and report the resulting state changes.',
     '["terraform.apply"]', TRUE, 'admin', 900),

    ('cost-analyzer', 'Cost Analyzer', 'Finds idle resources, right-sizing proposals',
     'You analyze cloud resource utilization and cost data to find idle or oversized resources and propose right-sizing changes.',
     '["cloud.cost_explorer", "kubectl.get", "prometheus.query"]', FALSE, 'viewer', 300),

    ('postmortem', 'Postmortem', 'Drafts RCA document from run history',
     'You draft a blameless root-cause-analysis document from the steps and reasoning recorded in an incident run.',
     '["runs.read"]', FALSE, 'viewer', 300),

    ('notify', 'Notify', 'Posts a status update to a chat channel',
     'You post a concise status update about the current run to the given channel.',
     '["slack.post"]', FALSE, 'viewer', 60),

    -- The one agent chat (routes/chat.py) actually uses — full tool
    -- catalog instead of one narrow specialist per prompt, see
    -- docs/superpowers/plans/generalist-chat-agent-and-routing-removal.md.
    -- The 15 agents above stay specialist-scoped for Flightplans/direct
    -- /agents/{slug}/run. is_mutating=TRUE is accurate (it can call
    -- mutating tools) but irrelevant to chat, which gates per tool call.
    ('assistant', 'Assistant', 'General-purpose chat agent with the full tool catalog',
     'You are OpsSquad''s general-purpose DevOps assistant, running with real tools against a real Kubernetes cluster. Every tool targets this OpsSquad deployment itself, not something the caller picks -- kubectl always runs against the opssquad namespace, lint/tests always run against this app''s own source, terraform always runs against this app''s own demo config. There is no other cluster, namespace, or repo to choose, so never ask which cluster or namespace -- but always name the concrete scope you acted in, never a vague "this deployment" or "the current namespace". kubectl/helm tool results include a real "namespace" field, terraform results include a real "dir" field -- cite that literal value in your answer (e.g. "in the opssquad namespace", "in terraform-demo"), the same way you cite any other tool-returned fact. Given a question: (1) plan which tool call(s) will actually get you the answer -- do not guess or assume state you have not checked; (2) call them -- you never execute anything yourself, the platform runs exactly what you call and returns the real result; (3) review each result, and call again if it did not answer the question or revealed you need more; (4) once you have what you need, give a clear, direct answer to exactly what was asked, not a dump of raw tool output. If no available tool can answer the question, say so plainly instead of guessing. Mutating tools (deploy/scale/restart/apply/etc.) pause for the user''s explicit approval before they run -- propose the call, you do not need to ask permission yourself. But if a request to change something does not say what to act on (for example "restart it" or "scale it up"), call ask_user to ask which resource before calling any mutating tool -- never guess the target.',
     '["kubectl.get","kubectl.logs","kubectl.restart","kubectl.scale","terraform.plan","terraform.apply",
       "trivy.scan","docker.build","docker.tag","helm.upgrade","helm.rollback","argocd.sync","argocd.rollback",
       "cloud.cost_explorer","slack.post","pagerduty.read","git.diff","git.log","secrets.scan","lint.run",
       "test.run","test.select","registry.push","registry.pull","iac.scan","http.smoke_test",
       "prometheus.query","prometheus.targets","alertmanager.read","runs.read",
       "kubectl.events","kubectl.describe","registry.list","pagerduty.list"]',
     TRUE, 'viewer', 300)
ON CONFLICT (slug) DO NOTHING;

-- ============================================
-- FLIGHTPLANS — seeded from flightplans/*.yaml
-- ============================================
INSERT INTO flightplans (slug, name, description, definition, env, created_by)
SELECT
    'ship-to-prod', 'Ship to Production',
    'Full path from merged PR to verified production release.',
    $json$
    {
        "inputs": {
            "repo":   { "type": "string", "required": true },
            "commit": { "type": "string", "required": true }
        },
        "steps": [
            { "id": "review",   "agent": "code-review",   "with": { "repo": "${inputs.repo}", "commit": "${inputs.commit}" }, "fail_fast": true },
            { "id": "test",     "agent": "test-runner",   "needs": ["review"], "with": { "commit": "${inputs.commit}" }, "fail_fast": true },
            { "id": "build",    "agent": "build",         "needs": ["test"],   "with": { "tag": "${inputs.commit[:7]}" } },
            { "id": "scan",     "agent": "security-scan", "needs": ["build"],  "with": { "tag": "${inputs.commit[:7]}" }, "policy": { "block_on": ["CRITICAL"] } },
            { "id": "approval", "type": "approval",       "needs": ["scan"],   "approvers": ["admin"], "timeout": "2h" },
            { "id": "deploy",   "agent": "deploy",        "needs": ["approval"], "with": { "chart": "charts/${inputs.repo}", "namespace": "prod" } },
            { "id": "verify",   "agent": "verify",        "needs": ["deploy"], "with": { "commit": "${inputs.commit}", "window": "5m", "error_rate_threshold": 0.02 } },
            { "id": "rollback", "agent": "rollback",      "needs": ["verify"], "when": "${steps.verify.status == 'failed'}" }
        ]
    }
    $json$::jsonb,
    'prod', id
FROM users WHERE email = 'admin@opssquad.dev'
ON CONFLICT (slug) DO NOTHING;

INSERT INTO flightplans (slug, name, description, definition, env, created_by)
SELECT
    'incident-response', 'Incident Response',
    'Auto-triggered by Alertmanager: triage, diagnose, remediate, notify, and draft a postmortem.',
    $json$
    {
        "trigger": { "type": "webhook", "source": "alertmanager" },
        "steps": [
            { "id": "triage",     "agent": "incident-triage", "with": { "alert": "${inputs.alert}" } },
            { "id": "diagnose",   "agent": "diagnose",   "needs": ["triage"],   "with": { "lookback": "30m", "alertname": "${steps.triage.alertname}" } },
            { "id": "remediate",  "agent": "remediate",  "needs": ["diagnose"], "when": "${steps.triage.severity in ['P1','P2']}",
              "with": { "action": "${steps.diagnose.recommended_action}", "target_replicas": "${steps.diagnose.suggested_replicas}" },
              "guardrails": { "allowed_actions": ["restart_pod", "scale_hpa", "rollback_release"], "max_replicas": 10 } },
            { "id": "notify",     "agent": "notify",     "needs": ["remediate"],
              "with": { "channel": "#incidents", "severity": "${steps.triage.severity}", "remediation_action": "${steps.remediate.action}" } },
            { "id": "postmortem", "agent": "postmortem", "needs": ["notify"],
              "with": { "format": "markdown", "assign_to": "on_call", "severity": "${steps.triage.severity}", "remediation_action": "${steps.remediate.action}" } }
        ]
    }
    $json$::jsonb,
    'prod', id
FROM users WHERE email = 'admin@opssquad.dev'
ON CONFLICT (slug) DO NOTHING;

INSERT INTO flightplans (slug, name, description, definition, env, created_by)
SELECT
    'cost-sweep', 'Nightly Cost Sweep',
    'Finds idle/oversized cloud resources and opens a right-sizing PR. Plan only, never applies.',
    $json$
    {
        "trigger": { "type": "schedule", "cron": "0 2 * * *" },
        "steps": [
            { "id": "scan",    "agent": "cost-analyzer",   "with": { "min_idle_days": 30 } },
            { "id": "propose", "agent": "terraform-plan",  "needs": ["scan"], "with": { "mode": "rightsizing", "open_pr": true } }
        ]
    }
    $json$::jsonb,
    'all', id
FROM users WHERE email = 'admin@opssquad.dev'
ON CONFLICT (slug) DO NOTHING;
