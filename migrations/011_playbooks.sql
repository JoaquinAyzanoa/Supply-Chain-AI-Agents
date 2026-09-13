-- Phase 11 S3: playbooks, the director's multi-step plans that wait, check and escalate.

CREATE TABLE IF NOT EXISTS playbook_runs (
    id           bigserial PRIMARY KEY,
    playbook     text NOT NULL,
    case_id      text NOT NULL,
    po_name      text,
    partner_id   int,
    status       text NOT NULL DEFAULT 'running',  -- running | waiting | waiting_approval | done | failed | cancelled
    step_index   int NOT NULL DEFAULT 0,          -- the step the run is on
    due_at       timestamptz,                     -- a wait step: when it may continue
    waiting_for  text,                            -- a wait step: the condition that continues it early
    started_by   text,
    started_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    summary      text
);
CREATE INDEX IF NOT EXISTS playbook_runs_po_idx ON playbook_runs (po_name, status);
CREATE INDEX IF NOT EXISTS playbook_runs_status_idx ON playbook_runs (status, due_at);

-- What happened at each step, in order: ids and short outcomes only.
CREATE TABLE IF NOT EXISTS playbook_steps (
    id         bigserial PRIMARY KEY,
    run_id     bigint NOT NULL REFERENCES playbook_runs (id) ON DELETE CASCADE,
    step_id    text NOT NULL,
    status     text NOT NULL,                     -- done | skipped | waiting | failed | escalated
    at         timestamptz NOT NULL DEFAULT now(),
    detail     jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS playbook_steps_run_idx ON playbook_steps (run_id, id);
