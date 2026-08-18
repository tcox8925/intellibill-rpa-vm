-- Migration: add the insurance-filter-selected audit field to the queue table.
--
-- v5.19: the Print Chart modal's Patient insurance row now has its filter dropdown (All
-- insurance / Active insurance / Inactive insurance) driven to "Active insurance" whenever
-- the row is included (see chart_ui.select_insurance_active_filter). QueueRecord gained a
-- matching insurance_filter_selected field to record what the toggle actually confirmed, for
-- audit without re-driving the browser -- store.py derives its DB column list from
-- QueueRecord's dataclass fields, so this column must exist here before any row can be
-- upserted.
--
-- Run: psql -f migrations/002_add_insurance_filter_selected.sql "$RCM_DB_URL"
--  (or via the accompanying run_migration.py, which reads RCM_DB_* from .env)

ALTER TABLE ehr.ehr_pf_queue_rows
    ADD COLUMN IF NOT EXISTS insurance_filter_selected TEXT;

COMMENT ON COLUMN ehr.ehr_pf_queue_rows.insurance_filter_selected IS
    'The Print Chart modal insurance-filter toggle label confirmed for this record (e.g. "Active insurance") when the Patient insurance section was included; empty when insurance was not printed or the filter could not be confirmed.';
