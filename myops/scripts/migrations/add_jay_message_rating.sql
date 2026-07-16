-- Migration: Add rating column to jay_messages
ALTER TABLE wpo.jay_messages ADD COLUMN IF NOT EXISTS rating SMALLINT;
COMMENT ON COLUMN wpo.jay_messages.rating IS 'Message rating: NULL=unrated, 1=thumbs up, -1=thumbs down';
