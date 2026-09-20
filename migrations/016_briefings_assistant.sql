-- Phase 11 S7: the morning briefing per day, and the department assistant's conversation.

CREATE TABLE IF NOT EXISTS briefings (
    day         date PRIMARY KEY,
    language    text NOT NULL DEFAULT 'en',
    since       timestamptz NOT NULL,
    sections    jsonb NOT NULL DEFAULT '[]'::jsonb,
    paragraph   text,
    counts      jsonb NOT NULL DEFAULT '{}'::jsonb,
    emailed_to  text[] NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- One row per message; a plan travels with the director's message that proposed it and
-- its status changes when a person confirms or dismisses it. No email text is ever stored.
CREATE TABLE IF NOT EXISTS assistant_messages (
    id           bigserial PRIMARY KEY,
    user_email   text NOT NULL,
    role         text NOT NULL,                     -- user | director
    text         text NOT NULL,
    citations    jsonb NOT NULL DEFAULT '[]'::jsonb,
    plan         jsonb,
    plan_status  text,                              -- proposed | confirmed | dismissed
    outcome      text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS assistant_messages_user_idx ON assistant_messages (user_email, id);
