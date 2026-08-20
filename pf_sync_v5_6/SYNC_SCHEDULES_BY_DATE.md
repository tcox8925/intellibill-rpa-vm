# `sync-schedules-by-date` — catching patients Practice Fusion's report missed

## The problem, in plain words

Practice Fusion has two places that show an appointment: the **Schedule** page
(what a front-desk person sees, updates live) and the **Eligibility/Appointment
Report** (a CSV/XLSX export our whole pipeline — `nightly`, `full-sync-by-date`
— is built on top of).

Those two don't always agree. A patient can be marked **Seen** on the Schedule
page today, and still be missing from the Report for a while — the Report
just hasn't synced yet. Since every other command in this repo only ever
learns about a patient *from the Report*, a patient in that gap never gets
processed: no facesheet pull, no SOAP note, nothing — until the Report
eventually catches up, if it ever does before the operator forgets.

This is exactly what happened with a real patient (Murphy Alkhatib, 2026-07-01
appointment): visible and marked Seen on the Schedule, never appeared in the
Eligibility Report, so `full-sync-by-date` never saw her.

## What we built: `sync-schedules-by-date`

A **separate, standalone command/endpoint** that never touches the Eligibility
Report at all:

```
Schedule page (live scrape)
      │  keep only rows marked "Seen"
      ▼
compare against the queue: has this patient+date already been handled?
      │  no → build a record straight from what the Schedule scrape read
      ▼
open that patient's chart → pull facesheet + SOAP note PDF
      │
      ▼
zip + upload to Azure (rcm-attachments), same as every other command
```

It is **not** a stage inside `full-sync-by-date` — it's its own command, its
own endpoint, its own flow. `full-sync-by-date` and `nightly` are both
untouched by any of this; they still work exactly as before, Report-only.

### Why a separate command instead of bolting it onto `full-sync-by-date`

- `full-sync-by-date`'s whole job is the Report pipeline (pull → ingest →
  match → process). Mixing in a Report-bypass path inside that same function
  made it harder to reason about and, in practice, is what let the bugs below
  hide.
- This needs to be **re-run repeatedly on a rolling window** (see below) —
  that's a different operational rhythm than a one-shot Report pull, so it
  earns its own command rather than an extra flag on an existing one.

### Catching a patient who flips to "Seen" a few days late

A patient might show **Confirmed** (not yet seen) on the Schedule when you
first check — that's not a bug, it just means their visit hasn't happened
yet, and this command correctly skips them (visible in the output under
`not_seen_skipped`, not silently dropped).

If you call this with no explicit date at all, it doesn't just look at today —
it defaults to a **rolling window**, `[today - lookback_days, today]`
(`--lookback-days`, default 3). So if a patient's status only flips to Seen
two days after their actual visit, the next scheduled call still finds and
processes them, without anyone having to remember which past date to re-check.

Pass an explicit `--report-date` / `--start-date`/`--end-date` and that always
wins over the lookback default.

### A patient with two visits keeps both

If the same patient has two separate Seen appointments inside the requested
window (e.g. a visit on the 1st and another on the 5th), **both** get their
own facesheet pull — one is never allowed to silently overwrite the other.

## Bugs found and fixed while building this

These were all present in an earlier draft of this feature and are fixed now
— listed here so it's clear what was actually wrong, not just what changed:

1. **Wrong field names on the synthetic record.** The code building the
   injected queue record used field names that don't exist on `QueueRecord`
   (`appointment_time`, `provider_name`, `ehr_patient_guid_match`). This threw
   a `TypeError` every single time, which was silently swallowed by a
   catch-all `except Exception` — so the whole feature quietly did nothing,
   every run, with no visible error.
2. **`.get()` called on a dataclass.** The "is this patient already in the
   queue" check called `.get()` on `QueueRecord` objects, which don't have a
   `.get()` method (that's a dict method). This threw `AttributeError` on the
   very first real row — meaning it silently failed on every run where the
   queue already had normal data in it (i.e. every normal run), not just an
   empty-queue test.
3. **Dedupe key was patient-only, not patient+date.** The "already handled"
   check only looked at the patient's GUID, ignoring the appointment date. A
   patient with ANY prior appointment already in the queue — from a
   completely different day — would look "already covered" and a brand-new
   visit on a different date would get silently skipped. Fixed by keying on
   `(patient GUID, appointment date)` together.
4. **Multiple visits collapsed to one.** The original Schedule-scrape helper
   (`discover_via_schedule_range`) is deliberately deduped to one row per
   patient (keeps whichever day was scraped *last*) — correct for its real
   job (refreshing the patient registry), but wrong for this feature, since a
   patient's earlier visit in the same range would just vanish. Fixed by
   adding a second function, `discover_appointments_via_schedule_range`, that
   keeps every row scraped, tagged with its actual date. The original
   function is untouched and still used where it always was.
5. **Selectors that don't survive Practice Fusion's own markup.** The initial
   version read appointment status/time/type/provider using guesses about the
   DOM that turned out to be wrong once checked against the real page: the
   provider/type/status elements' numeric ID suffix does **not** line up with
   the patient cell's own row number even on the same row, and the status
   text's own CSS class name looks like an auto-generated build hash that can
   change between Practice Fusion releases. Fixed by matching on stable
   prefixes/attributes (not exact suffixed strings, not hash-like class
   names) — see `ScheduleScrapeConfig` below.
6. **A busy day could silently under-scrape.** The Schedule row DOM uses the
   same `data-table__cell`/`appointments-table__col--sm` component Practice
   Fusion's Patient List Report and Appointment Report tables use — and both
   of those are already confirmed to render only a portion of their rows into
   the DOM at once, the rest loading lazily as an inner `data-table-scroller`
   container is scrolled. The original code only waited passively for the row
   count to catch up, which does nothing if the table needs to actually be
   scrolled to render the rest. Fixed by `scroll_schedule_day_and_collect`,
   which reuses the exact same scroll-and-collect loop already proven live
   for the report pages, scoped to the Schedule table instead.
7. **A busy day could also span multiple numbered pages, separate from
   scrolling.** Scrolling only helps when everything is still one page, just
   rendered lazily. If Practice Fusion instead shows a real "Next page"
   control for a day with enough appointments, that's a different mechanism
   entirely. `scroll_and_paginate_schedule_day` checks for the same
   `pager-label`/`pager-btn-next` widget already confirmed live for the report
   pages, and clicks through it if present -- with zero behavior change on
   every day observed so far, where no such pager exists at all.

## No hardcoded selectors — config-driven, like the rest of the repo

Every Schedule-page selector this command uses (patient cell, provider name,
appointment type, status, start time, date-nav buttons, the tab itself) comes
from a config file, the same pattern `config/pf_pdf_sync_config.json` and
`config/pf_appointment_report_config.json` already use for the chart page and
report page. It never has a hardcoded literal:

```bash
python3 pf_soap_sync_v5_16.py write-schedule-config \
  --schedule-config-json config/pf_schedule_scrape_config.json
```

This writes the confirmed-working defaults to disk as plain JSON. If Practice
Fusion ever changes the Schedule page's markup, fix it by editing that JSON
file — no code change needed. An empty/missing file, or one with a placeholder
blank value, falls back to the built-in defaults automatically (same
self-healing behavior as the other two config files) and warns about any
key it doesn't recognize.

## Database, manifest, and Azure upload — verified, not new work

These three pieces already existed in the rest of the repo and this command
was built to use them exactly the same way, not to reinvent them:

- **Database**: the queue lives in Postgres (`ehr.ehr_pf_queue_rows`), not a
  JSON file — `load_store`/`save_store` already do a full read-then-write of
  every row for that queue, so a call to this command reads everything
  already there, adds only the genuinely new synthetic rows on top, and
  writes the whole set back. Nothing from a previous day's run — or from
  `nightly`/`full-sync-by-date`'s own rows — is dropped or overwritten.
- **Appointments manifest** (`pf_encounter_pdfs/appointments_<id>.json`): each
  call gets its own manifest tag (`<queue>_sync_schedules_<start>_to_<end>`),
  separate from `full-sync-by-date`'s own tag, so the two commands never
  collide in the same folder. Re-running this command for the *same* date
  range merges into that one manifest instead of creating duplicates
  (existing `write_appointments_metadata_json` behavior, unchanged).
- **Azure upload**: same `build_and_upload_zip` / `retry_orphaned_zips` calls
  `full-sync-by-date` already makes — PDFs get zipped with their manifest and
  uploaded to the `rcm-attachments` Azure Blob container, then deleted
  locally, exactly like every other command.

## What this means for `nightly`

**`nightly` is untouched and has the exact same Report-only limitation this
whole feature exists to work around** — it never looks at the Schedule page,
so a patient in that Report-sync gap is invisible to `nightly` too, same as
it was invisible to `full-sync-by-date` before this work.

Nothing about `nightly` was changed, and nothing here is wired into it. If you
want `nightly`'s queue to also benefit from this catch-up, run
`sync-schedules-by-date` against the *same* `--queue-json` `nightly` uses — it
reads/writes that same underlying Postgres queue, so a patient it injects
there is picked up by anything else pointed at that queue file too.

## Running it

CLI:
```bash
python3 pf_soap_sync_v5_16.py sync-schedules-by-date \
  --queue-json pf_appointment_queue.json \
  --config-json config/pf_pdf_sync_config.json \
  --schedule-config-json config/pf_schedule_scrape_config.json \
  --downloads-dir pf_encounter_pdfs \
  --practice "NWARK Internal Medicine" \
  --chrome-user-data-dir "$HOME/pf_rpa_chrome" \
  --lookback-days 3
```

HTTP:
```bash
curl -X POST http://localhost:8011/sync-schedules-by-date \
  -H "Content-Type: application/json" \
  -d '{"lookback_days": 3}'
```

Useful flags (same meaning as the rest of the repo):
- `--dry-run` — walk the logic, don't actually generate/upload PDFs.
- `--limit N` — cap how many records get processed this call.
- `--lookback-days N` — only applies when no explicit date is given; widen it
  if patients are flipping to Seen later than 3 days out.
- `--report-date` / `--start-date` / `--end-date` — pin to an exact
  date/range instead of the rolling window.

## What the output tells you

```json
{
  "discover": {"method": "schedule_range_per_visit", "rows_scraped": 42, "date_range": "..."},
  "inject_discovered": {
    "synthetic_records_created": 1,
    "discovered_visits_not_in_report": ["Murphy Alkhatib on 2026-07-01 (GUID: 6f69a406...)"],
    "not_seen_skipped": ["Jane Doe on 2026-08-20 (Confirmed)"]
  },
  "process": { "...": "same shape process/full-sync-by-date already return" },
  "rcm_upload": { "...": "blob_path, container, ..." }
}
```

- `discovered_visits_not_in_report` — who this call actually pulled a chart
  for, straight from the Schedule, bypassing the Report.
- `not_seen_skipped` — who it saw but correctly left alone because they're
  not Seen yet (expected, not an error).
