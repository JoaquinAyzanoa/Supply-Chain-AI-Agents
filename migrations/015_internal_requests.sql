-- Phase 11 S6: internal purchase requests, linked to the RFQs they became.
-- Identifiers only: the message to reply to and the requester's address.

CREATE TABLE IF NOT EXISTS internal_requests (
    id                 bigserial PRIMARY KEY,
    case_id            text NOT NULL,
    graph_message_id   text NOT NULL UNIQUE,
    requester_address  text NOT NULL,
    po_names           text[] NOT NULL DEFAULT '{}',
    need_date          date,
    items              int NOT NULL DEFAULT 0,
    status             text NOT NULL DEFAULT 'open',   -- open | done | rejected
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS internal_requests_po_names_idx ON internal_requests USING gin (po_names);
