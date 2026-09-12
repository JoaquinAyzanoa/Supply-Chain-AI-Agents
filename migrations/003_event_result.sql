-- Phase 5: the director records what it did with each accepted event.
ALTER TABLE event_inbox ADD COLUMN IF NOT EXISTS result jsonb;
