-- Phase 9: supplier performance.
-- Observed facts per received order line (the raw material) and one score
-- row per supplier per weekly run (what the scorecard and the planner read).
-- Numbers are computed in code; the model only writes the scorecard text.

CREATE TABLE IF NOT EXISTS lead_time_observations (
    id               bigserial PRIMARY KEY,
    partner_id       int NOT NULL,
    product_id       int,
    po_name          text NOT NULL,
    po_line_id       int NOT NULL,
    confirmed_at     timestamptz NOT NULL,      -- when the order was confirmed
    promised_date    date,                      -- first promise, from the confirmation snapshot
    received_at      timestamptz NOT NULL,      -- picking done
    qty_ordered      numeric(16,4) NOT NULL,
    qty_received     numeric(16,4) NOT NULL,
    lead_time_days   numeric(8,2) NOT NULL,     -- confirmed_at -> received_at
    on_time          boolean NOT NULL,          -- received on or before promised_date
    in_full          boolean NOT NULL,          -- qty_received >= qty_ordered
    observed_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (po_line_id, received_at)
);
CREATE INDEX IF NOT EXISTS lead_time_observations_partner_idx
    ON lead_time_observations (partner_id, received_at DESC);
CREATE INDEX IF NOT EXISTS lead_time_observations_product_idx
    ON lead_time_observations (product_id, received_at DESC);

CREATE TABLE IF NOT EXISTS supplier_scores (
    id                     bigserial PRIMARY KEY,
    run_id                 text NOT NULL,          -- the weekly run (case id)
    partner_id             int NOT NULL,
    partner_name           text NOT NULL,
    period_start           date NOT NULL,
    period_end             date NOT NULL,
    otif                   numeric(5,4),           -- 0..1, NULL when nothing received
    lead_time_mean_days    numeric(8,2),
    lead_time_sigma_days   numeric(8,2),
    promise_drift_days     numeric(8,2),
    response_hours_median  numeric(8,2),
    quality_rate           numeric(5,4),           -- discrepancy lines / received lines
    price_cv               numeric(8,4),           -- coefficient of variation of unit prices
    score                  numeric(5,2) NOT NULL,  -- 0..100, weights from settings
    samples                jsonb NOT NULL DEFAULT '{}'::jsonb,  -- counts behind each metric
    scorecard              text,                   -- the model's paragraph
    trends                 jsonb NOT NULL DEFAULT '[]'::jsonb,  -- flagged changes vs the previous run
    approval_id            int,
    applied_at             timestamptz,            -- when the partner and planning params were written
    computed_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, partner_id)
);
CREATE INDEX IF NOT EXISTS supplier_scores_partner_idx
    ON supplier_scores (partner_id, computed_at DESC);
