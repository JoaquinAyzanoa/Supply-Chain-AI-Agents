-- Phase 11 S2: what people decide, what the system suggests from it, who the suppliers are.

-- Every resolved approval, as a signal: what was proposed (facts only, never mail
-- bodies), what the person did with it, and how long it took.
CREATE TABLE IF NOT EXISTS decision_feedback (
    approval_id       int PRIMARY KEY,
    kind              text NOT NULL,
    agent             text,
    partner_id        int,
    po_name           text,
    status            text NOT NULL,             -- approved | rejected | expired
    edited            boolean NOT NULL DEFAULT false,
    edit_fields       jsonb NOT NULL DEFAULT '[]',
    edit_notes        jsonb NOT NULL DEFAULT '{}',   -- dropped lines, variance sizes: numbers only
    reason            text,
    facts             jsonb NOT NULL DEFAULT '{}',   -- the ActionFacts the request carried
    requested_at      timestamptz,
    resolved_at       timestamptz,
    seconds_to_decide double precision,
    resolved_by       text,
    via               text,                         -- api | odoo
    recorded_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS decision_feedback_resolved_idx ON decision_feedback (resolved_at DESC);
CREATE INDEX IF NOT EXISTS decision_feedback_kind_partner_idx ON decision_feedback (kind, partner_id);

-- What the weekly calibration proposes; one open row per key, closed when accepted or dismissed.
CREATE TABLE IF NOT EXISTS calibration_suggestions (
    id           bigserial PRIMARY KEY,
    key          text NOT NULL,
    kind         text NOT NULL,                  -- autonomy_rule | setting | attention
    title        text NOT NULL,
    detail       text NOT NULL,
    evidence     jsonb NOT NULL DEFAULT '{}',
    proposal     jsonb NOT NULL DEFAULT '{}',    -- the rule or the setting to apply
    status       text NOT NULL DEFAULT 'open',   -- open | accepted | dismissed
    created_at   timestamptz NOT NULL DEFAULT now(),
    resolved_at  timestamptz,
    resolved_by  text
);
CREATE UNIQUE INDEX IF NOT EXISTS calibration_suggestions_open_key
    ON calibration_suggestions (key) WHERE status = 'open';

-- Facts about a supplier the agents read when they write to it; edited by people.
CREATE TABLE IF NOT EXISTS supplier_profiles (
    partner_id  int PRIMARY KEY,
    language    text,
    formality   text,                            -- formal | neutral | informal
    greeting    text,
    sign_off    text,
    contacts    jsonb NOT NULL DEFAULT '[]',     -- names, never addresses beyond Odoo's
    notes       text NOT NULL DEFAULT '',
    facts       jsonb NOT NULL DEFAULT '{}',     -- maintained by the agents: reply time, last reply
    updated_at  timestamptz NOT NULL DEFAULT now(),
    updated_by  text
);

-- A rule change the planner rejected twice is not proposed again for a while.
CREATE TABLE IF NOT EXISTS planning_holds (
    product_id        int PRIMARY KEY,
    rejections        int NOT NULL DEFAULT 0,
    first_rejected_on date,
    held_until        date,
    last_approval_id  int,
    updated_at        timestamptz NOT NULL DEFAULT now()
);
