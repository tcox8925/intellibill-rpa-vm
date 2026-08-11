# EHR RPA Scraper — Code Implementation Notes

Scope of this doc: the scraper, `rpa_queue.py`, the queue schema, and how scraped rows map into the existing `wpo.ehr_patients` table. Endpoints, secret provisioning/consumption, infra, and security are out of scope (handled separately). Where a secret or connection string is needed, it is read from an **environment variable**; how that env var gets populated is out of scope.

Artifacts:
- `practice_fusion_rpa_to_csv.py` — the PF adapter + scraper + run loop
- `rpa_queue.py` — queue store (Postgres / local-file) + login gate
- `queue_schema.sql` — queue + job-control DDL

---

## 1. Two phases, one queue

The run splits into **discover** and **scrape**, connected by `wpo.rpa_scrape_queue`. This is what makes it resumable and multi-worker.

```
DISCOVER  -- upsert targets -->  rpa_scrape_queue  -- claim batch -->  SCRAPE
(report sweep)                   (status lifecycle)                    (chart scrape)
                                                                         | upsert
                                                                         v
                                                                  wpo.ehr_patients
```

- **Discover** (`--mode discover`): the age-bucket report sweep enumerates every patient GUID for a practice and upserts each into the queue.
- **Scrape** (`--mode scrape`): workers claim `pending` rows, scrape Summary + Profile, upsert into `ehr_patients`, mark the queue row `done`.
- **`--mode both`**: discover then scrape in one process (equivalent to today's single-box run).

The queue **is** the checkpoint. A stop/crash resumes by re-claiming `pending` (plus stale `in_progress` rows reclaimed by age). The old `checkpoint.json` and seed CSV are no longer state in production — the queue table replaces them.

---

## 2. Queue backends (`rpa_queue.py`)

One interface, two backends, chosen by whether a DSN is present:

- **PostgresQueue** — production. DSN comes from `--queue-dsn` / `RPA_QUEUE_DSN` (env). Requires `psycopg2-binary`.
- **FileQueue** — local dev. A JSON file (`--queue-file`); no DB, no psycopg2. Same methods.

```python
store = rpa_queue.make_queue(dsn=args.queue_dsn, file_path=args.queue_file or default)
```

Methods used by the scraper:

| Method | Used in | Purpose |
|---|---|---|
| `upsert_target(scope, source_id, url, change_signature, run_id)` | discover | Insert new / re-`pending` a changed target; leave unchanged rows alone |
| `claim_batch(scope, limit, run_id)` | scrape | Atomically claim N `pending` rows (`FOR UPDATE SKIP LOCKED`) |
| `mark_done(queue_id, result=None)` | scrape | Row complete |
| `mark_error(queue_id, err)` | scrape | Row failed (keeps `last_error`, `attempts`) |
| `reclaim_stale(seconds)` | scrape start | Reset crashed workers' `in_progress` back to `pending` |
| `stats(scope)` | logging | Counts per status |
| `set_state / get_signal / clear_signal` | login gate | Job status + UI continue signal |

**Scope** = the six-part identity stamped on every row:

```python
Scope(ehr_name, practice, group_name="", entity="patient", sub_entity="")
```

Set from `--ehr-name`, `--practice`, `--group`, `--entity`, `--sub-entity`.

---

## 3. Queue schema (`queue_schema.sql`)

Natural key on `wpo.rpa_scrape_queue`:

```
(ehr_name, group_name, practice, entity, sub_entity, source_id)
```

`source_id` = the EHR-native id (PF chart GUID). The `UNIQUE` on this key gives us dedup and idempotent discovery upserts. `status` drives the lifecycle (`pending -> in_progress -> done|error`). `change_signature` is the cheap change detector carried from the list view.

`wpo.rpa_job_control` holds `state` (job writes) and `signal` (UI writes) — see the login gate (section 5).

---

## 4. Mapping scraped rows -> `wpo.ehr_patients`

The scrape worker upserts into the existing patients table instead of writing CSV. Two additive DB changes:

```sql
ALTER TABLE wpo.ehr_patients
  ADD COLUMN IF NOT EXISTS group_name        text,
  ADD COLUMN IF NOT EXISTS summary           jsonb,
  ADD COLUMN IF NOT EXISTS patient_note      jsonb,
  ADD COLUMN IF NOT EXISTS raw_patient       jsonb,
  ADD COLUMN IF NOT EXISTS raw_insurance     jsonb,
  ADD COLUMN IF NOT EXISTS change_signature  text,
  ADD COLUMN IF NOT EXISTS run_id            uuid,
  ADD COLUMN IF NOT EXISTS scraped_at        timestamptz;

-- Idempotent upsert target; 'id' holds the EHR-native id (= queue.source_id).
CREATE UNIQUE INDEX IF NOT EXISTS ux_ehr_patients_natkey
  ON wpo.ehr_patients (ehr_name, group_name, practice, entity, sub_entity, id);
```

> Confirm the actual `ehr_patients` column names/types with the team; the mapping below reflects what the scraper produces, adjust names to match.

**Field mapping (scraped row -> column):**

| Scraped field | Column | Notes |
|---|---|---|
| `source_id` (GUID) | `id` | part of natural key |
| scope | `ehr_name`, `group_name`, `practice`, `entity`, `sub_entity` | |
| `patient_name`, `dob`, `sex`, `status` | same | `status` = Active/Inactive |
| `record_number` (PRN) | `patient_id` / `record_number` | per existing schema |
| `email`, `home_phone`, `mobile_phone`, `work_phone` | same | |
| `address_line_1/2`, `city`, `state`, `zip_code` | same | |
| `payment_preference` | `payment_preference` | |
| primary/secondary insurance fields | same | empty for Self Pay |
| `summary` | `summary` (jsonb) | |
| `patient_note_json` | `patient_note` (jsonb) | |
| `raw_patient_json`, `raw_insurance_json` | `raw_patient`, `raw_insurance` (jsonb) | |
| `report_signature` | `change_signature` | drives incremental re-scrape |
| run uuid | `run_id` | |
| now() | `scraped_at`, `updated_date` | |

**Upsert function** (replaces `append_csv_row` in scrape mode):

```python
from psycopg2.extras import Json
import json

def _loads(s):
    try:
        return json.loads(s) if s else {}
    except Exception:
        return {}

def upsert_patient(conn, scope, row: dict) -> None:
    cols = [
        "id","ehr_name","group_name","practice","entity","sub_entity",
        "patient_name","dob","sex","status","patient_id",
        "email","home_phone","mobile_phone","work_phone",
        "address_line_1","address_line_2","city","state","zip_code",
        "payment_preference",
        "primary_insurance_name","secondary_insurance_name",
        "primary_insurance_id","secondary_insurance_id",
        "summary","patient_note","raw_patient","raw_insurance",
        "change_signature","run_id",
    ]
    vals = [
        row["id"], scope.ehr_name, scope.group_name, scope.practice,
        scope.entity, scope.sub_entity,
        row.get("patient_name"), row.get("dob"), row.get("sex"), row.get("status"),
        row.get("record_number") or row.get("patient_id"),
        row.get("email"), row.get("home_phone"), row.get("mobile_phone"), row.get("work_phone"),
        row.get("address_line_1"), row.get("address_line_2"), row.get("city"),
        row.get("state"), row.get("zip_code"), row.get("payment_preference"),
        row.get("primary_insurance_name"), row.get("secondary_insurance_name"),
        row.get("primary_insurance_id"), row.get("secondary_insurance_id"),
        Json(_loads(row.get("summary"))), Json(_loads(row.get("patient_note_json"))),
        Json(_loads(row.get("raw_patient_json"))), Json(_loads(row.get("raw_insurance_json"))),
        row.get("report_signature"), row.get("scrape_run_id"),
    ]
    # add scraped_at, updated_date via now()
    all_cols = cols + ["scraped_at", "updated_date"]
    placeholders = ",".join(["%s"] * len(vals) + ["now()", "now()"])
    update_set = ",".join(
        f"{c}=EXCLUDED.{c}" for c in all_cols
        if c not in ("id","ehr_name","group_name","practice","entity","sub_entity")
    )
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO wpo.ehr_patients ({','.join(all_cols)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (ehr_name, group_name, practice, entity, sub_entity, id) "
            f"DO UPDATE SET {update_set}",
            vals,
        )
    conn.commit()
```

**Wire-in:** in the scrape loop of `practice_fusion_rpa_to_csv.py`, when a DSN is configured, call `upsert_patient(conn, scope, row)` instead of `append_csv_row(...)`, then `store.mark_done(qid)`. Keep the CSV path as the local fallback when there's no DSN.

**Incremental:** discovery recomputes `change_signature`; if it differs from the stored value the queue row flips to `pending`, the worker re-scrapes, and the upsert overwrites the `ehr_patients` row in place. Unchanged patients are skipped entirely.

---

## 5. Login gate (replaces terminal `input()`)

The previous flow paused on `input(">>> press ENTER")`. That's replaced by `rpa_queue.wait_for_start_signal(...)`, which:

1. Writes `rpa_job_control.state='awaiting_login'` with an instruction message.
2. Blocks, polling `get_signal(job_id)` until it sees `continue` (proceed) or `stop` (abort).
3. Locally, if a TTY is present, a keypress also releases it — so dev runs are unchanged.

The operator completes login/OTP in the browser, then something flips `signal='continue'` (the UI/endpoint side is out of scope). The scraper only reads the signal. The same `state='reauth_required'` path is used when PF re-challenges mid-run — the scraper detects the security-check page and waits on the gate again.

The scraper's responsibility ends at: set state, poll signal, proceed. Populating the signal is the caller's concern.

---

## 6. `--mode` behavior

- `discover`: sweep -> `upsert_target` per GUID -> set `state='done'` -> exit (no scraping).
- `scrape`: `reclaim_stale` -> loop `claim_batch` -> `scrape_patient_profile` -> upsert/CSV -> `mark_done`/`mark_error` -> `close_active_chart_tab` -> periodic reload flush.
- `both`: discover then fall straight into scrape.

Scope + queue selection are set once at the top of `main()` from the new flags; everything downstream (`scrape_patient_profile`, tab-close, flush) is unchanged from the working local scraper.

---

## 7. Adapter seam (for the next EHR)

The PF-specific pieces are `discover` (the age-bucket report sweep) and `scrape` (Summary+Profile). Everything else — queue, modes, upsert, login gate, resume — is EHR-agnostic. A second EHR is a new adapter implementing those two, with no queue/DB changes.

---

## 8. Open items

- Confirm real `wpo.ehr_patients` column names to finalize the mapping/upsert.
- Decide `sub_entity` grain: one queue row per patient (current) vs. per tab.
- Wire `upsert_patient` into the scrape loop behind the DSN flag (CSV stays as local fallback).
