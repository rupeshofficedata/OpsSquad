-- OpsSquad initial schema

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ============================================
-- USERS & ROLES
-- ============================================
CREATE TYPE user_role AS ENUM ('admin', 'dev', 'viewer');

CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,        -- bcrypt, cost 12
    full_name     VARCHAR(120),
    role          user_role NOT NULL DEFAULT 'viewer',
    is_active     BOOLEAN DEFAULT TRUE,
    last_login_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    -- Per-user agent-execution model preference. 'anthropic' uses the paid
    -- API (ANTHROPIC_API_KEY); 'local' targets a self-hosted OpenAI-compatible
    -- server (e.g. llama-server). model_name is a free-text label — for
    -- 'anthropic' it overrides AGENT_MODEL, for 'local' it's informational
    -- only, since a llama.cpp server serves whichever single GGUF it loaded.
    model_provider VARCHAR(20) NOT NULL DEFAULT 'anthropic'
        CHECK (model_provider IN ('anthropic', 'local')),
    model_name     VARCHAR(120)
);

-- ============================================
-- AGENTS — registry of what the system can do
-- ============================================
CREATE TABLE agents (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug          VARCHAR(80) UNIQUE NOT NULL,   -- 'code-review', 'deploy'
    name          VARCHAR(120) NOT NULL,
    description   TEXT,
    system_prompt TEXT NOT NULL,
    tools         JSONB NOT NULL DEFAULT '[]',   -- ["git.diff","trivy.scan"]
    is_mutating   BOOLEAN DEFAULT FALSE,         -- TRUE = changes real infra
    min_role      user_role NOT NULL DEFAULT 'dev',
    timeout_sec   INT DEFAULT 300,
    enabled       BOOLEAN DEFAULT TRUE
);

-- ============================================
-- FLIGHTPLANS — saved multi-agent workflows
-- ============================================
CREATE TABLE flightplans (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug        VARCHAR(80) UNIQUE NOT NULL,
    name        VARCHAR(150) NOT NULL,
    description TEXT,
    definition  JSONB NOT NULL,      -- the step graph (see flightplans/*.yaml)
    env         VARCHAR(30) DEFAULT 'staging',
    created_by  UUID REFERENCES users(id),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================
-- RUNS — one execution (chat run OR flightplan run)
-- ============================================
CREATE TYPE run_status AS ENUM
    ('queued','running','awaiting_approval','success','failed','aborted','skipped');

CREATE TABLE runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind          VARCHAR(20) NOT NULL,          -- 'chat' | 'flightplan'
    flightplan_id UUID REFERENCES flightplans(id),
    agent_id      UUID REFERENCES agents(id),    -- set for chat runs
    prompt        TEXT,
    inputs        JSONB,                         -- flightplan inputs, needed to resume after approval
    status        run_status NOT NULL DEFAULT 'queued',
    triggered_by  UUID REFERENCES users(id),
    started_at    TIMESTAMPTZ DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    result        JSONB
);

-- ============================================
-- RUN STEPS — per-agent trace inside a run
-- ============================================
CREATE TABLE run_steps (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id      UUID REFERENCES runs(id) ON DELETE CASCADE,
    step_order  INT NOT NULL,
    step_id     VARCHAR(80),          -- the Flightplan step's own id (e.g. 'review'), null for chat runs
    agent_slug  VARCHAR(80) NOT NULL,
    input       JSONB,
    output      JSONB,
    reasoning   TEXT,                 -- why the agent chose this action
    tool_calls  JSONB,
    status      run_status NOT NULL,
    duration_ms INT
);

-- ============================================
-- AUDIT LOG — admin-only, append-only
-- ============================================
CREATE TABLE audit_logs (
    id         BIGSERIAL PRIMARY KEY,
    user_id    UUID REFERENCES users(id),
    action     VARCHAR(100) NOT NULL,   -- 'flightplan.execute'
    target     VARCHAR(200),
    metadata   JSONB,
    ip_address INET,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_runs_status  ON runs(status);
CREATE INDEX idx_steps_run    ON run_steps(run_id, step_order);
CREATE INDEX idx_audit_user   ON audit_logs(user_id, created_at DESC);
