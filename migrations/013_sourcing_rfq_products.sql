-- Phase 11 S4 follow-up: an invited RFQ remembers which products of the basket it asks for,
-- so an award can be split per line.

ALTER TABLE sourcing_round_rfqs ADD COLUMN IF NOT EXISTS products jsonb NOT NULL DEFAULT '[]';
