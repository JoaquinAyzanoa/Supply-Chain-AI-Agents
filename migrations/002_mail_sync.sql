-- Phase 4: mail sync state, signed-event delivery and scheduler runs.
-- No table here stores a subject, a body, a sender name or an attachment:
-- only Graph identifiers, order names and run bookkeeping.

-- One row per mailbox: the Graph delta link and the outcome of the last run.
CREATE TABLE IF NOT EXISTS mail_sync_state (
    mailbox      text PRIMARY KEY,
    delta_link   text,
    last_run_at  timestamptz,
    last_status  text
);

-- Every inbox message the sync has handled, so re-delivered delta entries
-- and full resyncs after an expired delta are harmless.
CREATE TABLE IF NOT EXISTS mail_processed (
    graph_message_id text PRIMARY KEY,
    processed_at     timestamptz NOT NULL DEFAULT now(),
    outcome          text NOT NULL,          -- linked | unlinked | skipped
    po_name          text,
    case_id          text
);

-- Messages the agents sent, keyed for In-Reply-To / References matching.
CREATE TABLE IF NOT EXISTS mail_outbound (
    graph_message_id    text PRIMARY KEY,
    internet_message_id text UNIQUE,
    conversation_id     text,
    po_name             text NOT NULL,
    case_id             text NOT NULL,
    sent_at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS mail_outbound_conversation_idx ON mail_outbound (conversation_id);

-- Events the director could not accept at publish time; retried in order.
CREATE TABLE IF NOT EXISTS event_outbox (
    event_id        text PRIMARY KEY,
    event_type      text NOT NULL,
    target          text NOT NULL,
    body            text NOT NULL,            -- the signed JSON payload (identifiers only)
    attempts        int  NOT NULL DEFAULT 0,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_attempt_at timestamptz
);

-- Events the director accepted; the primary key makes redelivery idempotent.
CREATE TABLE IF NOT EXISTS event_inbox (
    event_id    text PRIMARY KEY,
    event_type  text NOT NULL,
    source      text NOT NULL,
    case_id     text NOT NULL,
    payload     jsonb NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    handled_at  timestamptz                  -- set by the orchestrator (phase 6)
);
CREATE INDEX IF NOT EXISTS event_inbox_case_idx ON event_inbox (case_id);
CREATE INDEX IF NOT EXISTS event_inbox_unhandled_idx ON event_inbox (received_at)
    WHERE handled_at IS NULL;

-- One row per scheduler firing (cron or manual).
CREATE TABLE IF NOT EXISTS scheduler_runs (
    run_id       text PRIMARY KEY,
    job_id       text NOT NULL,
    trigger      text NOT NULL,               -- cron | manual
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    status       text NOT NULL,               -- running | ok | failed | skipped_overlap
    http_status  int,
    summary      text
);
CREATE INDEX IF NOT EXISTS scheduler_runs_job_idx ON scheduler_runs (job_id, started_at DESC);
