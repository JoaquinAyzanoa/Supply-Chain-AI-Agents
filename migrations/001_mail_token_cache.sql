-- MSAL token cache for the delegated (development) mail mode. One row: the
-- serialised cache (refresh tokens included), shared by every container so
-- the device-code login happens once. Encrypted at rest in phase 10.
CREATE TABLE IF NOT EXISTS mail_token_cache (
    id         smallint PRIMARY KEY CHECK (id = 1),
    blob       text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
