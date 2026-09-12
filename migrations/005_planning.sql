-- Phase 7: inventory planning.
-- Numbers only: parameters a planner edits, one row per run, one row per
-- proposed line with the inputs that produced it (so any quantity can be
-- recomputed by hand) and what was finally applied.

CREATE TABLE IF NOT EXISTS planning_params (
    product_id           int PRIMARY KEY,
    abc_class            text NOT NULL,          -- A | B | C
    service_level        numeric(5,4) NOT NULL,  -- 0.90 .. 0.99
    review_period_days   int NOT NULL,
    max_coverage_days    int NOT NULL,
    lead_time_mean_days  numeric(8,2),           -- measured by phase 9; NULL = supplier's promise
    lead_time_sigma_days numeric(8,2),           -- measured; NULL = ratio x promise
    source               text NOT NULL DEFAULT 'default',  -- default | planner | measured
    updated_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS planning_runs (
    run_id        text PRIMARY KEY,
    case_id       text NOT NULL,
    kind          text NOT NULL,                  -- daily_plan | review_product | what_if
    as_of         date NOT NULL,
    warehouse_id  int NOT NULL,
    status        text NOT NULL,                  -- proposed | awaiting_approval | applied | rejected | failed | simulated
    approval_id   int,
    summary       text,
    totals        jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS planning_runs_as_of_idx ON planning_runs (as_of DESC, created_at DESC);

CREATE TABLE IF NOT EXISTS planning_lines (
    id            bigserial PRIMARY KEY,
    run_id        text NOT NULL REFERENCES planning_runs (run_id),
    line_id       text NOT NULL,                  -- stable within the run: <run_id>:<product_id>
    product_id    int NOT NULL,
    product_ref   text NOT NULL,
    warehouse_id  int NOT NULL,
    inputs        jsonb NOT NULL,                 -- on hand, incoming, forecast, sigma, LT, params
    outputs       jsonb NOT NULL,                 -- ss, rop, order_up_to, order_qty, proposed min/max
    action        text NOT NULL,                  -- update_rule | create_rfq | update_rule_and_rfq | hold | manual_review | none
    exception     text,
    explanation   text,
    accepted      boolean,
    applied_at    timestamptz,
    applied       jsonb,                          -- orderpoint id, RFQ id/name, quantities written
    UNIQUE (run_id, product_id)
);
CREATE INDEX IF NOT EXISTS planning_lines_product_idx ON planning_lines (product_id, id DESC);
