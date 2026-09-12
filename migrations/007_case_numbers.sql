-- Phase 8: a short, human number for every case (shown as C00012).
-- case_id stays the primary key (it is in every log, trace and event);
-- the number is what people read and type. Existing rows are numbered in
-- creation order, new rows take the next value.

CREATE SEQUENCE IF NOT EXISTS cases_number_seq;

ALTER TABLE cases ADD COLUMN IF NOT EXISTS number bigint;

UPDATE cases AS c
SET number = numbered.rn
FROM (
    SELECT case_id, row_number() OVER (ORDER BY created_at, case_id) AS rn
    FROM cases
) AS numbered
WHERE c.case_id = numbered.case_id AND c.number IS NULL;

SELECT setval('cases_number_seq', coalesce((SELECT max(number) FROM cases), 0) + 1, false);

ALTER TABLE cases ALTER COLUMN number SET DEFAULT nextval('cases_number_seq');
ALTER TABLE cases ALTER COLUMN number SET NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS cases_number_idx ON cases (number);
