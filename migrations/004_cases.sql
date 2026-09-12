-- Phase 6: the orchestrator's cases.
-- A case groups every piece of agent work on one purchase order story (an RFQ
-- and its replies, an ETA request, an inbound question). It is the unit the
-- Control Tower shows and the Langfuse session id. Payloads hold identifiers
-- and short summaries only: never an email body, subject or sender name.

CREATE TABLE IF NOT EXISTS cases (
    case_id         text PRIMARY KEY,
    kind            text NOT NULL,          -- rfq | eta | inbound | unlinked | receipt | planning | invoice
    po_name         text,
    partner_id      int,
    conversation_id text,                   -- Outlook thread the case lives in, once known
    status          text NOT NULL,          -- open | awaiting_approval | done | rejected | failed | escalated
    agent           text,
    trace_id        text,
    summary         text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    next_action_at  timestamptz
);
CREATE INDEX IF NOT EXISTS cases_po_idx ON cases (po_name, created_at DESC);
CREATE INDEX IF NOT EXISTS cases_status_idx ON cases (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS case_events (
    id       bigserial PRIMARY KEY,
    case_id  text NOT NULL REFERENCES cases (case_id),
    at       timestamptz NOT NULL DEFAULT now(),
    kind     text NOT NULL,                 -- event_received | task_sent | result | approval_requested
                                            -- | approval_resolved | rule_fired | escalated | note
    payload  jsonb NOT NULL                 -- ids and summaries only
);
CREATE INDEX IF NOT EXISTS case_events_case_idx ON case_events (case_id, id);
