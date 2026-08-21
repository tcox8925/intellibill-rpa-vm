-- Migration: add the SOAP-note match-mode audit field to the queue table.
--
-- v5.20: sync-schedules-by-date can now fall back to the most recent SOAP note dated on
-- or before the appointment date when no exact-date note exists (explicit user decision,
-- 2026-08-21; see chart_ui.select_soap_note_for_date). QueueRecord gained a matching
-- soap_note_match_mode field to record "exact" vs. "fallback_most_recent_on_or_before:<date>"
-- for audit without re-driving the browser -- store.py derives its DB column list from
-- QueueRecord's dataclass fields, so this column must exist here before any row can be
-- upserted.
--
-- Run: psql -f migrations/003_add_soap_note_match_mode.sql "$RCM_DB_URL"
--  (or via the accompanying run_migration.py, which reads RCM_DB_* from .env)

ALTER TABLE ehr.ehr_pf_queue_rows
    ADD COLUMN IF NOT EXISTS soap_note_match_mode TEXT;

COMMENT ON COLUMN ehr.ehr_pf_queue_rows.soap_note_match_mode IS
    'How the printed SOAP note was matched: "exact" for a real appointment-date match, '
    '"fallback_most_recent_on_or_before:<date>" when sync-schedules-by-date instead picked '
    'the most recent note dated on/before the appointment date (never a future-dated note). '
    'Empty for every other command, which never enables this fallback.';
