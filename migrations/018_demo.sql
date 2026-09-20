-- Phase 11 S9: demo mode. One row: where the scripted scenario stands, the records it
-- owns (the late order and its original date, the receipt order, the round's RFQs) and the
-- outcome of every step. No email text is ever stored.

CREATE TABLE IF NOT EXISTS demo_state (
    id            smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    position      integer NOT NULL DEFAULT 0,
    started_at    timestamptz,
    records       jsonb NOT NULL DEFAULT '{}'::jsonb,
    outcomes      jsonb NOT NULL DEFAULT '[]'::jsonb,
    approval_ids  integer[] NOT NULL DEFAULT '{}',
    updated_at    timestamptz NOT NULL DEFAULT now()
);
