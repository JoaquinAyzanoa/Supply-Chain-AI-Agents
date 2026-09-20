-- Phase 11: what the agents did without asking, by rule of the autonomy policy.
-- One row per automatic action; "auto_notice" rows carry the inverse write and
-- can be reverted until revert_until. Ids and summaries only, never mail bodies.

CREATE TABLE IF NOT EXISTS auto_actions (
    id           bigserial PRIMARY KEY,
    case_id      text,
    run_id       text,
    agent        text NOT NULL,
    kind         text NOT NULL,                 -- approval kind the rule replaced
    level        text NOT NULL,                 -- auto | auto_notice
    rule_id      text,                          -- the policy rule that decided
    summary      text NOT NULL,
    po_id        int,
    po_name      text,
    partner_id   int,
    payload      jsonb NOT NULL DEFAULT '{}',   -- what was applied (ids, dates, amounts)
    revert       jsonb,                         -- the inverse write, NULL when not revertible
    revert_until timestamptz,
    reverted_at  timestamptz,
    reverted_by  text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS auto_actions_created_idx ON auto_actions (created_at DESC);
CREATE INDEX IF NOT EXISTS auto_actions_case_idx ON auto_actions (case_id);
