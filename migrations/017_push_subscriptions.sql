-- Phase 11 S8: web push subscriptions of the installable Control Tower (endpoints only).

CREATE TABLE IF NOT EXISTS push_subscriptions (
    id          bigserial PRIMARY KEY,
    endpoint    text NOT NULL UNIQUE,
    p256dh      text NOT NULL,
    auth        text NOT NULL,
    user_email  text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
