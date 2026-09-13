-- Phase 11 S4: the sourcing agent's rounds, invitations and negotiations.

CREATE TABLE IF NOT EXISTS sourcing_rounds (
    id                    bigserial PRIMARY KEY,
    case_id               text NOT NULL,
    status                text NOT NULL DEFAULT 'open',  -- open | comparing | awaiting_award | awarded | rejected | cancelled | failed
    source_po_name        text,                          -- the order the round started from, if any
    incumbent_partner_id  int,
    basket                jsonb NOT NULL DEFAULT '[]',   -- [{product_id, product, qty, last_paid, currency}]
    deadline              timestamptz NOT NULL,
    group_id              int,                           -- Odoo purchase.order.group of the alternative RFQs
    comparison            jsonb,                         -- the last QuoteComparison
    award_approval_id     int,
    awarded_partner_id    int,
    awarded_po_name       text,
    created_by            text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sourcing_rounds_status_idx ON sourcing_rounds (status, deadline);
CREATE INDEX IF NOT EXISTS sourcing_rounds_source_idx ON sourcing_rounds (source_po_name);

-- One row per invited supplier: the RFQ created for them and how far the invitation got.
CREATE TABLE IF NOT EXISTS sourcing_round_rfqs (
    id            bigserial PRIMARY KEY,
    round_id      bigint NOT NULL REFERENCES sourcing_rounds (id) ON DELETE CASCADE,
    partner_id    int NOT NULL,
    partner_name  text NOT NULL,
    po_id         int,
    po_name       text,
    status        text NOT NULL DEFAULT 'created',  -- created | sent | awaiting_approval | no_email | failed | declined
    thread_id     text,
    sent_at       timestamptz,
    replied_at    timestamptz
);
CREATE INDEX IF NOT EXISTS sourcing_round_rfqs_round_idx ON sourcing_round_rfqs (round_id, id);
CREATE INDEX IF NOT EXISTS sourcing_round_rfqs_partner_idx ON sourcing_round_rfqs (partner_id);

-- Counter-offers made on a quoted line, so rounds are counted and limits hold.
CREATE TABLE IF NOT EXISTS sourcing_negotiations (
    id             bigserial PRIMARY KEY,
    case_id        text NOT NULL,
    po_id          int NOT NULL,
    po_name        text NOT NULL,
    partner_id     int NOT NULL,
    product_id     int NOT NULL,
    round_no       int NOT NULL,
    current_price  numeric(14, 4) NOT NULL,
    offered_price  numeric(14, 4) NOT NULL,
    floor_price    numeric(14, 4) NOT NULL,
    target_price   numeric(14, 4) NOT NULL,
    status         text NOT NULL DEFAULT 'proposed',  -- proposed | sent | rejected | failed
    approval_id    int,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS sourcing_negotiations_po_idx ON sourcing_negotiations (po_name, product_id);
