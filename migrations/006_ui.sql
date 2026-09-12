-- Phase 8: the Control Tower's users and the settings people change at runtime.

-- Local users until Entra ID (phase 10). Passwords are argon2 hashes.
CREATE TABLE IF NOT EXISTS ui_users (
    id            serial PRIMARY KEY,
    email         text NOT NULL UNIQUE,
    name          text NOT NULL,
    password_hash text NOT NULL,
    role          text NOT NULL,             -- viewer | approver | admin
    active        boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_login_at timestamptz
);

-- Settings that change without a restart (model per agent, follow-up policy, auto-send
-- suppliers, planning defaults). One row per version; the highest version is current.
CREATE TABLE IF NOT EXISTS settings_history (
    version     serial PRIMARY KEY,
    settings    jsonb NOT NULL,
    changed_by  text NOT NULL,
    note        text,
    changed_at  timestamptz NOT NULL DEFAULT now()
);
