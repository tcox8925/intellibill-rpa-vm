# Practice Fusion SOAP PDF Sync

Playwright-driven automation that pulls Practice Fusion appointments, matches them to
patients, and generates a Facesheet/SOAP-note PDF per encounter. Same shape as
[`ehr-scrape/`](../ehr-scrape/) (which does the equivalent job for Tebra/Kareo), but
targets Practice Fusion.

Current build: `PF-SOAP-SYNC-v5.13.0-summary-only-appointment-check` (see `BUILD_ID` in
`pf_soap_sync_v5_13.py`).

## Files

- `pf_soap_sync_v5_13.py` — the worker. Run `python pf_soap_sync_v5_13.py --help` for the
  full command list (`self-test`, `doctor`, `pull-report`, `ingest`, `match-patients`,
  `resolve-patient`, `process`, `refresh`, `nightly`, `status`, `reset`).
- `pf_pdf_sync_config.json` — CSS/XPath selectors for the print/export flow (print modal,
  encounter list, facesheet checkboxes, etc.).
- `pf_appointment_report_config.json` — selectors for the appointment report pull. Several
  fields are blank in the shipped copy and need to be filled in against the live PF UI
  before `pull-report` will work.
- `run_pf_sync_tests.ps1` — end-to-end smoke test (self-test → doctor → pull-report →
  ingest → match-patients → one-record dry run). Pass `-ChromeUserDataDir`,
  `-SourceUserDataDir`, and `-SourceProfile` for your machine; there's no personal-account
  default checked in.

Sample data that shipped with the download (appointment CSVs, a patient registry CSV, a
queue JSON, a sample encounter PDF) was **not** copied into the repo — it contains real
patient names, DOB, and PRNs, and this repo's `.gitignore` already treats that class of
file as untracked output rather than checked-in fixtures.

## State today (v5.13)

Persistence is entirely local files, not a database:

- Appointment report → CSV/XLSX
- Queue + patient mappings + run history → a single JSON file (`pf_appointment_queue.json`)
- Patient registry → CSV/JSON/XLSX
- Output → PDF files on disk

Auth: Playwright attaches to a dedicated Chrome profile over CDP. Username/password can
come from `PF_USERNAME`/`PF_PASSWORD` env vars; OTP/security-verification still requires a
human (or an external OTP source) in the loop — there's a one-time interactive bootstrap
to establish the authenticated session, which is then reused.

## Gap to production (from the VM handoff doc)

A vendor handoff doc (`Practice_Fusion_SOAP_Sync_VM_Handoff_Revised.pdf`, v5.13 baseline)
was provided alongside this code and explicitly frames v5.13 as **pre-production**. It
lays out a target Windows VM architecture and calls out the following as still missing —
none of this is implemented here yet, this is scope for a follow-up task:

1. **SQL repository layer.** Replace the JSON queue + patient CSV with a real DB as the
   system of record. Four tables are specified: `dbo.pf_appointments` (common appointment
   table, unique on `(practice_name, source_row_id)`), `dbo.pf_patient_registry`,
   `dbo.pf_patient_mapping_override` (manually resolved chart mappings), and
   `dbo.pf_refresh_jobs` (job lifecycle for the manual-refresh API — one queued/running job
   per appointment max).
2. **Azure Key Vault config.** All credentials/settings (`PF-Username`, `PF-Password`,
   `PF-Sql-Connection-String`, `PF-Sync-Config-Json`, `PF-Report-Config-Json`,
   `PF-Practice-Name`, `PF-Time-Zone`, Chrome profile paths, debug port, downloads dir,
   worker script path, nightly report offset) should be pulled from Key Vault at runtime
   instead of local `.env`/JSON files. The two JSON config files in this folder should
   become Key Vault secrets that get materialized to disk only at run time (v5.13 still
   needs file paths on disk).
3. **FastAPI manual-refresh service.** Two endpoints, backed by `dbo.pf_refresh_jobs`:
   - `POST /appointments/{appointment_pk}/refresh` → `202` with a `job_id`, queues the work
     and returns immediately (no nightly pull via this API).
   - `GET /refresh-jobs/{job_id}` → job status/result.
4. **Nightly Scheduled Task wrapper.** A Windows Task Scheduler job running
   `run_nightly.ps1` that does the full pull → upsert → match → process cycle against the
   SQL tables. No nightly FastAPI endpoint — scheduler-only.
5. **Single-worker lock.** Nightly and the refresh API must never drive the browser
   concurrently — they share one Chrome profile. Needs a SQL app lock / lock row / named
   mutex so only one Playwright job runs at a time across both entry points.

Browser automation behavior itself (chart navigation, SOAP-note selection, PDF generation)
is considered tested as of v5.13 per the handoff and is not part of the gap list.
