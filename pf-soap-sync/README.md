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

## API bridge (myops/pf/)

`myops/server.py` exposes the manual-refresh side of the handoff's target API,
implemented as a subprocess bridge over this worker (see `myops/pf/config.py`,
`myops/pf/runner.py`, `myops/pf/jobs.py`):

- `POST /appointments/{row_id}/refresh` → `202` with a job — matches the handoff's
  `POST /appointments/{appointment_pk}/refresh` shape, using the queue JSON's `row_id`
  as a stand-in for the not-yet-existent SQL `appointment_pk`.
- `GET /refresh-jobs/{job_id}` → job status/result, matching `GET /refresh-jobs/{job_id}`.
- `GET /pf-status` → queue counts (not part of the handoff spec, kept for visibility).

Job state (`myops/pf/jobs.py`) is an **in-memory** dict standing in for
`dbo.pf_refresh_jobs` — it does not survive an API restart and isn't shared across
multiple worker processes. Nightly is deliberately **not** exposed via FastAPI — the
handoff is explicit that nightly is a Windows Scheduled Task, not an API call
(`pf.runner.run_nightly()` exists as a plain function for that future wrapper script,
just not wired to an endpoint).

A cross-process lock file (`myops/pf/config.WORKER_LOCK_FILE`, default
`pf-soap-sync/.pf_worker.lock`) is acquired by `run_nightly()`/`run_refresh()` before
touching the browser, standing in for the handoff's SQL app lock / OS mutex (Section 7).
**This only works once a nightly wrapper script also acquires the same lock file** —
none exists yet, so today only concurrent API refresh calls are actually protected.

## Gap to production (from the VM handoff doc)

A vendor handoff doc (`Practice_Fusion_SOAP_Sync_VM_Handoff_Revised.pdf`, v5.13 baseline)
was provided alongside this code and explicitly frames v5.13 as **pre-production**. Status
of each item:

1. **SQL repository layer — not implemented.** Replace the JSON queue + patient CSV with
   a real DB as the system of record. Four tables are specified: `dbo.pf_appointments`
   (common appointment table, unique on `(practice_name, source_row_id)`),
   `dbo.pf_patient_registry`, `dbo.pf_patient_mapping_override` (manually resolved chart
   mappings), and `dbo.pf_refresh_jobs` (job lifecycle — one queued/running job per
   appointment max). The in-memory job store above is a stopgap, not this table.
2. **Azure Key Vault config — not implemented.** All credentials/settings (`PF-Username`,
   `PF-Password`, `PF-Sql-Connection-String`, `PF-Sync-Config-Json`,
   `PF-Report-Config-Json`, `PF-Practice-Name`, `PF-Time-Zone`, Chrome profile paths,
   debug port, downloads dir, worker script path, nightly report offset) should be pulled
   from Key Vault at runtime instead of local `.env`/JSON files. `myops/pf/config.py`
   currently reads these from `.env`, same convention as `ehr/config.py`.
3. **FastAPI manual-refresh service — implemented, with caveats.** Endpoint shapes match
   (see API bridge section above), but job state is in-memory rather than backed by
   `dbo.pf_refresh_jobs`, and `row_id` stands in for `appointment_pk`.
4. **Nightly Scheduled Task wrapper — not implemented.** No `run_nightly.ps1`/equivalent
   exists yet to run under Windows Task Scheduler. `pf_runner.run_nightly()` is ready to be
   called from one.
5. **Single-worker lock — partially implemented.** The lock file exists and the API side
   acquires it, but it only actually prevents a collision once the (not yet built) nightly
   wrapper acquires the same file.

Browser automation behavior itself (chart navigation, SOAP-note selection, PDF generation)
is considered tested as of v5.13 per the handoff and is not part of the gap list.
