-- Adds run timing to "EDI_Tebra".cron_job_executions. Apply BEFORE deploying
-- the matching cron_execution_log.py. cron_jobs.sql's CREATE TABLE includes these
-- columns for fresh installs; this is the upgrade for an existing table.
-- Additive and nullable, so code that doesn't know about them is unaffected.
--
-- Row lifecycle, written by cron_execution_log.py:
--   started     triggered_at          row inserted, run accepted (may be waiting for the browser lock)
--   processing  processing_started_at run actually began
--   success     finished_at           run finished, outcome check found no failures
--   failed      finished_at           raised / outcome check failed / never got the lock / process died
--                                     (error_description says which)

ALTER TABLE "EDI_Tebra".cron_job_executions
    ADD COLUMN IF NOT EXISTS processing_started_at timestamptz NULL,
    ADD COLUMN IF NOT EXISTS finished_at timestamptz NULL;

COMMENT ON COLUMN "EDI_Tebra".cron_job_executions.id IS
    'Execution id. For PF jobs this is the job_id returned by the triggering endpoint.';
COMMENT ON COLUMN "EDI_Tebra".cron_job_executions.triggered_at IS
    'When the run was accepted (status started).';
COMMENT ON COLUMN "EDI_Tebra".cron_job_executions.processing_started_at IS
    'When the run actually began (status processing) - after any lock wait.';
COMMENT ON COLUMN "EDI_Tebra".cron_job_executions.finished_at IS
    'When the run reached its final status (success or failed).';
COMMENT ON COLUMN "EDI_Tebra".cron_job_executions.response IS
    'request: trigger params; worker: owning host/pid; plus the job''s result summary once finished.';
