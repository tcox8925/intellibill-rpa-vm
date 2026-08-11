# pf_sync_v5_6 — Practice Fusion Facesheet/SOAP PDF Sync

Pulls Practice Fusion appointments, matches them to patients, and drives a real
Chrome browser (via Playwright/CDP) into each patient's chart to print a
Facesheet + SOAP note PDF for the appointment. No Practice Fusion API is used —
this is browser automation against the real EHR UI, so it needs a real, logged-in
Chrome session.

- Build: `PF-SOAP-SYNC-v5.16.0-batch-appointment-metadata`
- Code: `pf_sync_pkg/` (package). `pf_soap_sync_v5_16.py` is a thin CLI shim over it.
- HTTP API: `server.py` (FastAPI, port **8011**).
- No database — everything is files: JSON queue, CSV/XLSX reports, PDF output.

---

## 1. One-time setup

### 1.1 Python environment

```bash
cd /Users/hmg/Documents/RPA-VM-UPDATE/pf_sync_v5_6
python3 -m venv .venv
source .venv/bin/activate
which python3        # must print .../pf_sync_v5_6/.venv/bin/python3 — if not, activation didn't take
pip install -r requirements_pf_sync.txt
playwright install chromium
```

### 1.2 Credentials — `.env`

Credentials live in the **repo-root** `.env` (`/Users/hmg/Documents/RPA-VM-UPDATE/.env`),
shared with the other automations in this repo. `pf_sync_pkg/constants.py` loads it
automatically on import (via `python-dotenv`), so nothing here needs its own copy.

```
PF_USERNAME=your-practice-fusion-login
PF_PASSWORD=your-practice-fusion-password
PF_PRACTICE_TIMEZONE=America/Chicago   # only change if the practice isn't Central time
PF_SYNC_API_PORT=8011                  # server.py's port; myops/server.py owns 8010
PF_SYNC_API_ENV=development             # "development"/"local" enables /docs; anything else disables it
```

`.env` is gitignored — never commit real values.

### 1.3 Files this tool reads/writes (all relative to this folder unless you pass absolute paths)

| File | What it is |
|---|---|
| `config/pf_pdf_sync_config.json` | Selectors/behavior for the Print Chart flow (facesheet sections, notes mode). Already populated — don't touch unless Practice Fusion changes its UI. Tracked in git (not a secret/PHI, just UI selectors) — see `.gitignore`'s `pf_sync_v5_6/config/` exception. |
| `config/pf_appointment_report_config.json` | Selectors for pulling the Appointment report from Practice Fusion. Also tracked in git, same reason. |
| `practice_fusion_patients.csv` | Patient registry export (name/DOB/phone/PRN/GUID) used to match appointments to charts. Produced by `pull_patients.py` (§5) or supplied manually. |
| `pf_appointment_queue.json` | The persistent queue: every appointment row, its match status, and its processing status (`ready`/`needs_attention`/`processed`/`ignored`/`failed`). This is the file everything below reads and updates. |
| `pf_encounter_pdfs/` | Where generated PDFs (and their metadata JSON siblings) land. |

---

## 2. The pipeline, in order

Every appointment row moves through these stages. You do not have to run every
stage every time — if your queue is already ingested and matched (check with
`status`, below), skip straight to `process`.

```
 appointment report (CSV/XLSX)
        │  ingest
        ▼
 pf_appointment_queue.json  (rows: status=ready, patient unmatched)
        │  match-patients
        ▼
 pf_appointment_queue.json  (rows: status=ready, patient matched  |  needs_attention)
        │  process   (drives Chrome, generates PDFs)
        ▼
 pf_encounter_pdfs/*.pdf + rows: status=processed | failed
```

`nightly` = pull-report → ingest → match-patients → process, in one call.

---

## 3. Running it — CLI (simplest, run this yourself, watch Chrome the first time)

All commands below assume you're `cd`'d into `pf_sync_v5_6/` with the venv active.

### 3.1 Check everything is configured correctly (no browser needed for this part except a Chrome-exists check)

```bash
python3 pf_soap_sync_v5_16.py doctor \
  --config-json config/pf_pdf_sync_config.json \
  --report-config-json config/pf_appointment_report_config.json \
  --patients-file practice_fusion_patients.csv \
  --queue-json pf_appointment_queue.json
```
Expect `DOCTOR PASSED` at the end. Fix anything it flags before continuing.

### 3.2 See where your queue stands right now

```bash
python3 pf_soap_sync_v5_16.py status --queue-json pf_appointment_queue.json
```
This repo's queue currently shows: `ready: 83, ignored: 17, processed: 2, needs_attention: 3`.
The 3 `needs_attention` rows need a manual patient match (`resolve-patient`) before
they'll be picked up by `process` — they're skipped automatically otherwise.

### 3.3 Pull the PDFs for the 83 `ready` rows

```bash
python3 pf_soap_sync_v5_16.py process \
  --queue-json pf_appointment_queue.json \
  --config-json config/pf_pdf_sync_config.json \
  --downloads-dir pf_encounter_pdfs \
  --chrome-user-data-dir "$HOME/pf_rpa_chrome"
```

What happens:
1. Chrome launches with a dedicated, reusable profile at `~/pf_rpa_chrome` (created if it doesn't exist).
2. It navigates to Practice Fusion's login page. If the profile isn't already
   trusted, it types `PF_USERNAME`/`PF_PASSWORD` from `.env` and clicks Log in.
3. **If Practice Fusion challenges with OTP/security verification, a Chrome
   window is visible on your screen — complete that step yourself.** The
   script just waits; it does not bypass or auto-fill OTP.
4. Once authenticated, it works through the 83 `ready` rows: opens each
   patient's chart, prints Facesheet + the appointment-date SOAP note, saves
   the PDF into `pf_encounter_pdfs/`, and marks the row `processed`.
5. Prints a JSON summary (`processed`, `failed`, `skipped` counts) when done.

Useful flags:
- `--dry-run` — walk through the same logic without actually generating PDFs (good for a first pass to see what *would* happen).
- `--limit 5` — only process the first 5 ready rows (good for a smoke test before letting it run all 83).
- `--include-failed` — also retry rows currently marked `failed`.
- `--keep-browser-open` — leave Chrome open after the run instead of closing it.
- `--attach` — attach to a Chrome you already started yourself with `--remote-debugging-port=9222`, instead of letting the script launch/clone a profile.

Recommended first real run:
```bash
python3 pf_soap_sync_v5_16.py process \
  --queue-json pf_appointment_queue.json \
  --config-json config/pf_pdf_sync_config.json \
  --downloads-dir pf_encounter_pdfs \
  --chrome-user-data-dir "$HOME/pf_rpa_chrome" \
  --limit 3
```
Watch it complete 3 charts successfully, check the PDFs in `pf_encounter_pdfs/`,
then re-run without `--limit` for the rest.

### 3.4 Re-check status afterward

```bash
python3 pf_soap_sync_v5_16.py status --queue-json pf_appointment_queue.json
```

---

## 4. Running it — HTTP API (`server.py`, port 8011)

Start the server:
```bash
python3 -m uvicorn server:app --host 0.0.0.0 --port 8011
```
(or `--port $PF_SYNC_API_PORT` if you changed it in `.env`)

### 4.1 Health/version

```bash
curl http://127.0.0.1:8011/healthz
curl http://127.0.0.1:8011/version
```

### 4.2 Check queue status

```bash
curl -X POST http://127.0.0.1:8011/status \
  -H "Content-Type: application/json" \
  -d '{"queue_json": "/Users/hmg/Documents/RPA-VM-UPDATE/pf_sync_v5_6/pf_appointment_queue.json"}'
```

### 4.3 Pull PDFs (equivalent of `process` above)

```bash
curl -X POST http://127.0.0.1:8011/process \
  -H "Content-Type: application/json" \
  -d '{
    "queue_json": "/Users/hmg/Documents/RPA-VM-UPDATE/pf_sync_v5_6/pf_appointment_queue.json",
    "config_json": "/Users/hmg/Documents/RPA-VM-UPDATE/pf_sync_v5_6/config/pf_pdf_sync_config.json",
    "downloads_dir": "/Users/hmg/Documents/RPA-VM-UPDATE/pf_sync_v5_6/pf_encounter_pdfs",
    "chrome_user_data_dir": "/Users/hmg/pf_rpa_chrome",
    "limit": 3,
    "wait_for_completion": true
  }'
```
- `wait_for_completion: true` (default) blocks the HTTP call until the run finishes
  and returns the summary counts, or a 500 with the error.
- `wait_for_completion: false` returns `202 {"status": "started", "job_id": ...}`
  immediately and runs in a background thread — use this for long unattended runs;
  poll `/status` separately to watch progress.
- Only one browser job runs at a time (single global lock) — a second `/process`,
  `/pull-report`, `/full-sync`, `/refresh`, or `/nightly` call while one is already
  running gets rejected until the first finishes.
- Same OTP caveat as the CLI: the very first login needs a visible Chrome window.
  Do that first login via the CLI (§3.3) or with `"attach": true` pointed at a
  Chrome you already logged into, before relying on unattended `/process` calls.

### 4.4 Full nightly pipeline in one call

```bash
curl -X POST http://127.0.0.1:8011/nightly \
  -H "Content-Type: application/json" \
  -d '{
    "queue_json": "/Users/hmg/Documents/RPA-VM-UPDATE/pf_sync_v5_6/pf_appointment_queue.json",
    "config_json": "/Users/hmg/Documents/RPA-VM-UPDATE/pf_sync_v5_6/config/pf_pdf_sync_config.json",
    "report_config_json": "/Users/hmg/Documents/RPA-VM-UPDATE/pf_sync_v5_6/config/pf_appointment_report_config.json",
    "patients_file": "/Users/hmg/Documents/RPA-VM-UPDATE/pf_sync_v5_6/practice_fusion_patients.csv",
    "downloads_dir": "/Users/hmg/Documents/RPA-VM-UPDATE/pf_sync_v5_6/pf_encounter_pdfs",
    "practice": "your-practice-name",
    "chrome_user_data_dir": "/Users/hmg/pf_rpa_chrome"
  }'
```
This pulls today's appointment report from Practice Fusion, ingests it, matches
patients, and processes everything ready — the same thing the `nightly` CLI
command does.

---

## 5. Pulling a fresh patient registry (`pull_patients.py`)

`practice_fusion_patients.csv` is **not** kept up to date by `pf_soap_sync_v5_16.py`
at all — it's produced by a separate, standalone tool: `pull_patients.py` (a thin
entry point over `pf_sync_pkg/patient_scraper.py`, merged in from a prior
"Practice Fusion RPA" scraper and confirmed live against this account on
2026-08-10). It's a different shape from the rest of this repo's CLI — multi-phase
(discover → scrape), resumable via a queue — so it isn't folded into
`pf_soap_sync_v5_16.py`'s subcommands or `server.py`'s endpoints.

**How it works:**
1. **Discover**: opens Reports → Patient list report, adds an Age criterion set to
   "Range", sweeps `--age-from`..`--age-to` in `--age-bucket-size`-year windows
   (splitting a window further if it hits PF's 1000-row cap), and queues every
   patient GUID it finds.
2. **Scrape**: drains that queue, opening each patient's chart to pull demographics,
   insurance, guarantor, pharmacy, and clinical summary into one CSV row per
   patient — the same schema `practice_fusion_patients.csv` already uses.
3. **`--mode both`** does both in one run (this is what originally produced the
   8,026-row file).

```bash
python3 pull_patients.py \
  --attach --debug-port 9222 \
  --practice "NWARK Internal Medicine" \
  --mode both \
  --age-from 0 --age-to 120 --age-bucket-size 5 \
  --out practice_fusion_patients_fresh.csv
```

(`--attach` reuses an already-logged-in Chrome at that debug port — see §3.3's OTP
caveat; omit it to let the tool launch/clone a profile itself, same as the other
commands in this repo.)

**Two real gotchas already found and fixed, worth knowing about if this ever needs
touching again:**
- The Patient list report's results are virtualized *inside each numbered page* —
  a 50-row page only renders ~23 `<tr>` elements at a time in
  `[data-element='data-table-scroller']`; scraping without scrolling that inner
  container silently drops roughly half of every page. `scroll_report_and_collect()`
  in `patient_scraper.py` scrolls it before reading rows.
- Credentials come from `PF_USERNAME`/`PF_PASSWORD` in the repo-root `.env` (loaded
  automatically) — there is no hardcoded fallback anymore.

`--mode scrape`/`both` visits every patient's chart individually, so a full 0-120
sweep is slow (expect it to take a while for a few thousand patients) — `discover`
alone is fast and a good first check. Progress is checkpointed
(`<output>.checkpoint.json`) and the target queue (`--queue-file`, or `--queue-dsn`
for Postgres) is resumable if it's interrupted.

Whatever column headers the resulting CSV ends up with, `match-patients` already
normalizes arbitrary headers (name/DOB/phone/PRN/GUID aliases) — the same forgiving
mapping used for the appointment report — so the output doesn't need to match an
exact schema.

See `RPA_Scraper_Implementation.md` for the queue/DB design (including a documented,
not-yet-wired-up path to write straight into a shared `wpo.ehr_patients` Postgres
table alongside Tebra data, instead of CSV).

---

## 6. Handling `needs_attention` rows

Rows land here when a patient couldn't be uniquely matched (ambiguous name/DOB,
or no match found at all). `process`/`nightly` skip these on purpose rather than
guess. Resolve one manually:

```bash
python3 pf_soap_sync_v5_16.py resolve-patient \
  --queue-json pf_appointment_queue.json \
  --row-id <row_id from status output> \
  --ehr-patient-guid <the correct Practice Fusion chart GUID> \
  --patients-file practice_fusion_patients.csv
```
The mapping is remembered (`patient_mappings` in the queue JSON), so the same
patient auto-resolves on future ingests.

---

## 7. Troubleshooting

- **`command not found: python`** — macOS/Linux only ship `python3`. Always use `python3`, and confirm `which python3` points inside `.venv/bin/` after `source .venv/bin/activate`.
- **Login form visible but nothing happens** — `PF_USERNAME`/`PF_PASSWORD` are missing or wrong in `.env`; the script raises a clear error naming this rather than hanging silently.
- **Stuck on "security verification/OTP is open"** — expected; go complete it in the visible Chrome window. The script polls and continues automatically once you're through.
- **`CHECKBOX_NOT_SETTABLE` / `FACESHEET_SELECTION_MISMATCH` / `SOAP_NOTE_NOT_FOUND_FOR_DATE`** — Practice Fusion's UI changed a selector; check `config/pf_pdf_sync_config.json` against the current page, or re-run `doctor`.
- **`pull_patients.py` seems to be missing patients** — check whether `scroll_report_and_collect()` is actually scrolling; the Patient list report virtualizes rows inside each page (see §5).
- **A second browser job returns 409/"already running"** — only one Chrome/CDP session runs at a time through `server.py`; wait for the current job or check `/status`.
- **Port 8011 already in use** — set `PF_SYNC_API_PORT` in `.env` to something else and restart.
