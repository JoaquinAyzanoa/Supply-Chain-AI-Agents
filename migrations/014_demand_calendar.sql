-- Phase 11 S5: the demand calendar (promotions, holidays, projects) people maintain.

CREATE TABLE IF NOT EXISTS demand_calendar (
    id          bigserial PRIMARY KEY,
    kind        text NOT NULL,                 -- promotion | holiday | project
    name        text NOT NULL,
    start_date  date NOT NULL,
    end_date    date NOT NULL,
    product_id  int,                           -- one product, or NULL
    category    text,                          -- a category path, or NULL (everything)
    factor      numeric(8, 3) NOT NULL DEFAULT 1,  -- demand multiplier while it runs
    quantity    numeric(14, 3) NOT NULL DEFAULT 0, -- project: units over the period
    note        text NOT NULL DEFAULT '',
    created_by  text,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS demand_calendar_dates_idx ON demand_calendar (start_date, end_date);
