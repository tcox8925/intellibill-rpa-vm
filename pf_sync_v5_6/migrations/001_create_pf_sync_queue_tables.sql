-- Migration: Practice Fusion sync queue -> RCM DB, ehr schema.
--
-- Replaces the JSON queue files (pf_appointment_queue.json and friends) as the
-- system of record for per-appointment success/failure/error state. The JSON
-- file lived on the VM's local disk with no locking across concurrent
-- readers/writers and no query surface for "show me everything that failed" --
-- these tables give pf_sync_pkg/store.py a real place to upsert rows into and
-- look failures/successes up from, while every caller (cli.py, server.py,
-- matching.py, ingest.py, pdf_pipeline.py, queue_admin.py, selftest.py) keeps
-- calling load_store/save_store/store_rows/append_run/finish_run unchanged.
--
-- `queue_key` namespaces rows by queue file basename (pf_appointment_queue.json,
-- practice_fusion_patients_fresh.csv.queue.json, etc.) so the existing
-- separation between queues is preserved inside one shared table instead of
-- separate files.
--
-- Run: psql -f migrations/001_create_pf_sync_queue_tables.sql "$RCM_DB_URL"
--  (or via the accompanying run_migration.py, which reads RCM_DB_* from .env)

CREATE TABLE IF NOT EXISTS ehr.ehr_pf_queue_rows (
    queue_key                   TEXT NOT NULL,
    row_id                      TEXT NOT NULL,

    practice                    TEXT,

    appointment_id              TEXT,
    appointment_date            TEXT,
    appointment_status          TEXT,
    appointment_type            TEXT,
    provider                    TEXT,
    service_location            TEXT,

    -- Identity from the appointment report.
    patient_name                TEXT,
    patient_dob                 TEXT,
    patient_phone               TEXT,
    patient_phone_normalized    TEXT,

    -- Resolved Practice Fusion patient identity.
    patient_id                  TEXT,
    ehr_patient_guid            TEXT,
    patient_match_status        TEXT DEFAULT 'unmatched',
    patient_match_method        TEXT,
    patient_match_score         DOUBLE PRECISION DEFAULT 0,
    patient_match_message       TEXT,
    patient_candidates          JSONB DEFAULT '[]'::jsonb,

    -- Encounter discovered from the authenticated patient Summary/timeline.
    encounter_id                TEXT,
    encounter_key                TEXT,
    encounter_date               TEXT,
    encounter_type                TEXT,
    encounter_code               TEXT,
    encounter_chief_complaint    TEXT,
    encounter_source             TEXT,

    -- Queue state: this is the success/failure/error record itself.
    status                       TEXT DEFAULT 'ready',
    status_reason                TEXT,
    message                      TEXT,
    attempt_count                INTEGER DEFAULT 0,
    review_count                 INTEGER DEFAULT 0,
    refresh_count                INTEGER DEFAULT 0,

    source_report_name           TEXT,
    source_row_json               JSONB DEFAULT '{}'::jsonb,

    created_at                   TIMESTAMPTZ,
    updated_at                   TIMESTAMPTZ,
    first_ready_at                TIMESTAMPTZ,
    processing_started_at         TIMESTAMPTZ,
    last_checked_at               TIMESTAMPTZ,
    processed_at                  TIMESTAMPTZ,

    selected_soap_note_text       TEXT,
    selected_sections              JSONB DEFAULT '[]'::jsonb,
    notes_selection_mode           TEXT,
    pdf_path                       TEXT,
    metadata_json_path              TEXT,
    elapsed_seconds                 DOUBLE PRECISION DEFAULT 0,
    error_message                   TEXT,
    scrape_run_id                   TEXT,

    -- Row-store bookkeeping (distinct from the business created_at/updated_at
    -- above, which mirror the original QueueRecord fields verbatim).
    db_created_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),
    db_updated_at                    TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT pk_ehr_pf_queue_rows PRIMARY KEY (queue_key, row_id)
);

COMMENT ON TABLE ehr.ehr_pf_queue_rows IS
    'Practice Fusion sync queue rows -- one per appointment/patient, carrying the success/failure/error state that used to live only in pf_appointment_queue.json. queue_key groups rows by source queue file.';
COMMENT ON COLUMN ehr.ehr_pf_queue_rows.status IS
    'ready | needs_attention | review | processed | ignored | failed-style states -- see pf_sync_pkg/constants.py and cli.py for the full state machine.';
COMMENT ON COLUMN ehr.ehr_pf_queue_rows.error_message IS
    'Last processing error for this row, if any. Reprocessing (queue_admin.reset_rows) clears this back to empty and flips status back to ready.';

-- Lookups the ops/reprocess workflows need: "show me everything that failed",
-- "find this row by GUID/appointment", "how many attempts has this had".
CREATE INDEX IF NOT EXISTS ix_ehr_pf_queue_rows_status
    ON ehr.ehr_pf_queue_rows (queue_key, status);
CREATE INDEX IF NOT EXISTS ix_ehr_pf_queue_rows_guid
    ON ehr.ehr_pf_queue_rows (queue_key, ehr_patient_guid);
CREATE INDEX IF NOT EXISTS ix_ehr_pf_queue_rows_appt
    ON ehr.ehr_pf_queue_rows (queue_key, appointment_id);
CREATE INDEX IF NOT EXISTS ix_ehr_pf_queue_rows_match_status
    ON ehr.ehr_pf_queue_rows (queue_key, patient_match_status);


CREATE TABLE IF NOT EXISTS ehr.ehr_pf_queue_runs (
    run_id          TEXT PRIMARY KEY,
    queue_key       TEXT NOT NULL,
    command         TEXT,
    status          TEXT,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    details         JSONB DEFAULT '{}'::jsonb,
    db_created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE ehr.ehr_pf_queue_runs IS
    'Run history for pf-sync commands (pull-report/ingest/match-patients/process/nightly/full) per queue_key -- replaces store["runs"] in the JSON queue file.';

CREATE INDEX IF NOT EXISTS ix_ehr_pf_queue_runs_queue_started
    ON ehr.ehr_pf_queue_runs (queue_key, started_at DESC);


CREATE TABLE IF NOT EXISTS ehr.ehr_pf_queue_meta (
    queue_key           TEXT PRIMARY KEY,
    schema_version       INTEGER DEFAULT 3,
    patient_mappings      JSONB DEFAULT '[]'::jsonb,
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE ehr.ehr_pf_queue_meta IS
    'One row per queue_key: schema_version and the manually-confirmed patient_mappings list -- replaces the top-level fields of the JSON queue store other than rows/runs.';
