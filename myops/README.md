# EHR Scrape Pipeline — Setup & Usage

RPA + API pipeline that scrapes Tebra/Kareo appointments, notes, facesheets, and
charges into PostgreSQL and delivers per-practice facesheet ZIPs to Azure Blob
(`834labs-sftp`). One `WorkSelector` decides *what* to process; one pipeline
processes it. Three modes: **daily**, **backfill**, **target**.

--- Project Run 
.venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port 8010


## 1. What's in the package

```
ehr_scrape/
├── ehr/                     # the package (import as `ehr.*`)
│   ├── __init__.py
│   ├── config.py            # all constants + env-aware settings (single source)
│   ├── selector.py          # WorkSelector — describes a run (mode + filters)
│   ├── query.py             # THE query builder — every WHERE clause / gate
│   ├── matching.py          # name-key + date helpers (pure)
│   ├── azure_conn.py        # Key Vault → ServicePrincipalCredential (cached)
│   ├── db.py                # pch connection, run logging, appointment upsert
│   ├── browser.py           # Playwright grid/filter/navigation helpers
│   ├── session.py           # login, practice select (+OTP), discovery, cleanup
│   ├── charges.py           # VIEW CHARGE scraper
│   ├── passes.py            # the passes: appointments, notes, facesheets, charges, patient-match
│   ├── zipbuild.py          # ZIP build + SFTP delivery
│   ├── patients.py          # patient-insurance roster scraper (→ wpo.ehr_patients)
│   ├── pipeline.py          # run(sel) orchestrator
│   └── cli.py               # command-line entry point
├── server.py                # FastAPI server (port 8010) — daily/ad-hoc triggers
├── otp_info.py              # (NOT in refactor — from existing working copy)
├── email_read.py            # (NOT in refactor — reads OTP email)
└── graph_auth.py            # (NOT in refactor — MS Graph auth for email_read)
```

> **Important:** `otp_info.py`, `email_read.py`, and `graph_auth.py` are a
> part of this package *only as examples* - graph auth setup needed in the new env. `session.py`
> and `patients.py` do `from otp_info import ...` for OTP; without them, login
> fails.

---

## 2. Prerequisites

- **Python 3.11+**
- **Playwright** with Chromium: `pip install playwright && playwright install chromium`
- Python packages: `playwright`, `psycopg2-binary`, `azure-identity`,
  `azure-keyvault-secrets`, `azure-storage-blob`, `fastapi`, `uvicorn`, `pydantic`
- A **desktop session** — the browser runs headed (`headless=False`), so it needs
  a logged-in Windows desktop (not a headless service).
- Network access to: Tebra (`app.kareo.com`), Azure Key Vault, Azure Blob,
  PostgreSQL (`pch`), and the OTP mailbox (via MS Graph in `email_read.py`).

---

## 3. Database tables

All in the `wpo` schema on the `pch` server
(`pch-db-dev001.postgres.database.azure.com`, db `postgres`). Three tables:

### 3.1 `wpo.ehr_appointments` — the core table (owned by this pipeline)

Every pass reads/writes this. Minimum columns the code depends on:

| column | type | written by | notes |
|---|---|---|---|
| `id` | serial PK | — | row id |
| `appt_id` | text | appointments | Tebra appointment id (natural key w/ entity) |
| `appt_date` | text `YYYY-MM-DD` | appointments | **stored as text; must be `YYYY-MM-DD`** |
| `appt_time` | text | appointments | |
| `patient_name` | text | appointments | `"Last, First"` from Tebra grid |
| `dob` | text | appointments | |
| `home_phone`, `mobile_phone` | text | appointments | |
| `provider_name`, `service_location`, `appt_reason` | text | appointments | |
| `appt_status` | text | appointments/notes | e.g. `Checked Out` |
| `appt_note` | text | notes | signed-note sentinel; `NULL` = needs note |
| `process_status` | text | facesheets | `NULL`/`''`→needs pull, `Processed`, `Error` |
| `process_error_stage`, `process_error_message` | text | facesheets | on failure |
| `retry_flag` | int | appointments/facesheets | `1` = flagged |
| `retry_reason` | text | appointments | `'Missed Charges'` drives re-download |
| `tebra_facesheet_id` | text | facesheets | facesheet id from URL |
| `patient_id` | text | facesheets | scraped Tebra Patient ID |
| `charge_status` | text | notes | badge, e.g. `Charge in billing` |
| `charge_data` | jsonb | charges | VIEW CHARGE scrape; `NULL` = not captured |
| `patient_match` | bool | patient-match | reconciled against `ehr_patients` |
| `file_path` | text | zip | blob path once delivered; `NULL` = undelivered |
| `entity`, `sub_entity`, `ehr_name`, `practice` | text | appointments | scoping keys |
| `updated_date` | timestamptz | all | `now()` on write |

**Charge columns migration** (required — the pipeline expects these):
```sql
ALTER TABLE wpo.ehr_appointments
  ADD COLUMN IF NOT EXISTS charge_status text,
  ADD COLUMN IF NOT EXISTS charge_data  jsonb;
```

**process_status default** — must NOT default to `''`; insert `NULL` explicitly
(the code does). If an old default exists:
```sql
ALTER TABLE wpo.ehr_appointments ALTER COLUMN process_status DROP DEFAULT;
```

### 3.2 `wpo.ehr_patients` — patient roster (owned by `patients.py`)

Self-managed by the patient scraper, which auto-creates it if missing and keeps
SCD-style history via `effective_end_date` (NULL = current). Key columns:
`ehr_name, patient_id, entity, sub_entity, practice, patient_name, dob, sex,
marital_status, primary_insurance_name, secondary_insurance_name,
primary_insurance_id, secondary_insurance_id, primary_plan_name,
secondary_plan_name, effective_start_date, effective_end_date`. Unique index on
`(ehr_name, patient_id, entity, sub_entity) WHERE effective_end_date IS NULL`.
`patient_match` in `ehr_appointments` reconciles against this table.

### 3.3 `wpo.ops_pch_logs` — run log

One row per run (and one per patient run). Columns used: `txn_id, script_name,
process_type, status, error, company_id, carrier_id, file_path, started_at,
ended_at`.

---

## 4. Azure / cloud setup — **all resources below are 834-specific; recreate them in the new tenant**

> Everything in this section is currently hardcoded to **834 Labs**. For a new
> environment (e.g. **IntelliBill**), none of it is reusable across the tenant
> boundary — you must stand up your **own** Key Vault, service principal,
> secrets, Postgres, and storage account, then update the values in `config.py`
> / `azure_conn.py` / `graph_auth.py` / `email_read.py` to point at them. The
> table in §4b lists every value to replace.

**What must exist in the new tenant:**

- **Key Vault** — your own vault (currently `keyvault-834analytics`) holding the
  service-principal secrets (`SynapseAccessClientId`, `SynapseAccessSecret`,
  `TenantId` today — names are arbitrary, but `azure_conn.py` and `graph_auth.py`
  must match whatever you use). The runtime identity needs **get** on them.
- **Service principal / app registration** — its own SP in the new AAD tenant,
  used for (a) minting AAD tokens for Postgres and (b) Microsoft Graph to read
  the OTP mailbox (§4a). Needs Graph **`Mail.Read`** (application, admin-consented)
  and Postgres access.
- **PostgreSQL** — the `wpo` tables (§3) on your server (currently
  `pch-db-dev001.postgres.database.azure.com`, user `834data_syndb_adm`, AAD
  auth). Update `POSTGRES_CONFIG_PCH` in `config.py`.
- **Storage account + container** — for ZIP delivery (currently `ibrcmdataprd001`
  / container `834labs-sftp`), with a folder per practice (fuzzy-matched by
  normalized name). Update `STORAGE_ACCOUNT_NAME` and the connection string.
- **OTP mailbox + Graph** — see §4a.

Because 834 Labs and the new tenant are architecturally distinct, a service
principal in one **cannot** reach Key Vault / Postgres / Blob / mailbox in the
other. Provision within the tenant that owns the Tebra login being automated.

### 4b. Exact values to replace

| resource | file(s) | current 834 value | replace with |
|---|---|---|---|
| Key Vault name | `azure_conn.py`, `graph_auth.py` | `keyvault-834analytics` | your Key Vault |
| SP secret names | `azure_conn.py`, `graph_auth.py` | `SynapseAccessClientId`, `SynapseAccessSecret`, `TenantId` | your secret names (must hold your SP's client id / secret / tenant id) |
| Postgres host/db/user | `config.py` (`POSTGRES_CONFIG_PCH`) | `pch-db-dev001...`, `postgres`, `834data_syndb_adm` | your Postgres server / db / AAD user |
| Storage account | `config.py` (`STORAGE_ACCOUNT_NAME`) | `ibrcmdataprd001` | your storage account |
| Storage connection string | env `AZURE_STORAGE_CONNECTION_STRING` (fallback in `config.py`) | 834 account key (hardcoded — rotate) | your account's connection string (via env / Key Vault) |
| SFTP container | `zipbuild.py` (`SFTP_CONTAINER`) | `834labs-sftp` | your delivery container |
| OTP Graph auth | `graph_auth.py` | 834 vault + SynapseAccess SP | your vault + SP (§4a) |
| OTP mailbox | `email_read.py` (`MAILBOX_UPN`) | `jpoorna@834labs.com` | your OTP mailbox (§4a) |
| Tebra login | env `TEBRA_EMAIL` / `TEBRA_PASSWORD` (fallback in `config.py`) | 834 Tebra account | the Tebra account for the new env |
| Tenant identity | `config.py` (`ENTITY`, `SUB_ENTITY`) | `270681372`, `270681372001` | the new entity / sub-entity ids |



---

## 4a. OTP / email (Microsoft Graph) — **environment-specific, must be re-set up**

Tebra prompts for a one-time passcode (OTP) at login. The pipeline reads that
code automatically from an email inbox via Microsoft Graph. This is currently
wired entirely to **834 Labs** and **must be replaced for a new tenant such as
IntelliBill** — it will not work as-is because both the auth *and* the mailbox
belong to 834.

**How it works today (the chain):**

1. `session.py` / `patients.py` call `handle_tebra_otp_if_present(...)` which calls
   `fetch_latest_tebra_otp_code(...)` in `email_read.py`.
2. `email_read.py` reads a specific mailbox via Graph:
   - `MAILBOX_UPN = "jpoorna@834labs.com"` — the inbox the OTP email lands in.
   - It polls `GET /users/{MAILBOX_UPN}/mailFolders/Inbox/messages`, matches the
     OTP email by subject, extracts the code, then marks the message read.
3. The Graph bearer token comes from `graph_auth.py` →
   `get_graph_access_token()`, which reads the **834 Key Vault**
   (`keyvault-834analytics`) for the `SynapseAccess` service-principal secrets
   (`SynapseAccessClientId` / `SynapseAccessSecret` / `TenantId`) and requests
   scope `https://graph.microsoft.com/.default`.

So three things are 834-specific and must become IntelliBill's own:

| what | file | current (834) value | what IntelliBill needs |
|---|---|---|---|
| Graph auth (app registration) | `graph_auth.py` | `keyvault-834analytics` + `SynapseAccess*` SP | An **IntelliBill** app registration (client id / secret / tenant id) stored in an **IntelliBill Key Vault** |
| Graph permission | Azure AD | app has Graph `Mail.Read` on the 834 tenant | The IntelliBill app needs **`Mail.Read`** (application permission, admin-consented) scoped to read the OTP mailbox |
| OTP mailbox | `email_read.py` `MAILBOX_UPN` | `jpoorna@834labs.com` | The **IntelliBill mailbox** that will receive Tebra's OTP email (i.e. the address the Tebra account uses for OTP delivery) |

**Setup steps for IntelliBill:**

1. In the **IntelliBill** Azure AD tenant, create an app registration (or reuse
   an existing service principal). Grant it Microsoft Graph **`Mail.Read`**
   *application* permission and get tenant admin consent. (Application permission
   + admin consent is what lets it read the mailbox without an interactive user;
   for tighter scope, use an Application Access Policy to limit it to just the
   OTP mailbox.)
2. Store that SP's `tenantId`, `clientId`, `clientSecret` in an **IntelliBill
   Key Vault**, and point `graph_auth.py` at that vault + secret names (replace
   `KEY_VAULT_NAME` and the three `*_KEY` names). The runtime identity must have
   **get** on those secrets.
3. Set `MAILBOX_UPN` in `email_read.py` to the IntelliBill mailbox that receives
   the Tebra OTP. Make sure Tebra's login account is configured to send its OTP
   to that mailbox.
4. Confirm the OTP email **subject** match in `email_read.py` (`OTP_SUBJECT`)
   still matches what Tebra actually sends to that mailbox — adjust if Tebra's
   template differs.
5. Test: trigger a login (e.g. a `--no-upload --skip-patients` run) and confirm
   the OTP is read and login proceeds. If it hangs at OTP, check (a) the app has
   `Mail.Read` + consent, (b) `MAILBOX_UPN` is correct, (c) the subject matches.

> **Tenant boundary note:** 834 Labs and IntelliBill are separate tenants — an
> 834 SP cannot read an IntelliBill mailbox and vice-versa. The OTP auth, Key
> Vault, and mailbox must all live in (and belong to) the tenant that owns the
> Tebra login being automated.

---

## 5. Environment variables

| var | required? | default | purpose |
|---|---|---|---|
| `EHR_DOWNLOAD_DIR` | recommended | `C:\Users\myopsadmin\Downloads\acc` | local scratch dir for PDFs/ZIPs |
| `TEBRA_EMAIL` | recommended | hardcoded fallback | Tebra login |
| `TEBRA_PASSWORD` | recommended | hardcoded fallback | Tebra login |
| `AZURE_STORAGE_CONNECTION_STRING` | **strongly recommended** | hardcoded fallback | blob delivery — **rotate the fallback key and set this** |

Dev box example (cmd, quote the whole assignment to avoid trailing-space bugs):
```
set "EHR_DOWNLOAD_DIR=C:\Users\poorn\Microsoft\Downloads\acc"
```

> **Security:** `config.py` still contains hardcoded fallbacks for the Tebra
> password and the storage key. This repo mirrors to external GitHub — rotate
> the storage key, move credentials to env/Key Vault, and drop the fallbacks
> before shipping.

---

## 6. First-time setup in a new environment

1. Unzip the package so `ehr/` and `server.py` sit in one folder.
2. Copy `otp_info.py`, `email_read.py`, `graph_auth.py` next to `server.py`.
   **New tenant (e.g. IntelliBill): you must re-point the OTP Graph auth and
   mailbox — see §4a.**
3. `pip install` the packages in §2; `playwright install chromium`.
4. Ensure the three `wpo` tables exist and run the charge-column migration (§3.1).
   (`ehr_patients` auto-creates; `ehr_appointments` and `ops_pch_logs` must exist.)
5. Set env vars (§5). **Stand up your own Azure resources — Key Vault, SP +
   secrets, Postgres, storage account, OTP mailbox — and repoint the code at
   them per the §4b replacement table.** Confirm the runtime identity can read
   your Key Vault + reach your Postgres/blob.
6. Confirm `config.py` values match the environment: `ENTITY`, `SUB_ENTITY`,
   `EHR_NAME`, `POSTGRES_CONFIG_PCH`, `STORAGE_ACCOUNT_NAME` (all §4b).
7. Smoke test with a dry run (see §7).

---

## 7. Usage — CLI

Run from the folder that contains `ehr/`.

**Daily** (all practices, current date; patients-first; unbounded notes/facesheet
backlog sweep):
```
python -m ehr.cli
```

**Backfill** a date window (one practice):
```
python -m ehr.cli --practice "PrePost+ Tennessee" --start 2026-07-08 --end 2026-07-11
```

**Target** a single appointment / patient (operates on existing DB rows; makes its
own ZIP):
```
python -m ehr.cli --appt-id 1300 --practice "PrePostPlus Germantown"
python -m ehr.cli --patient "Moore" --date 2026-02-16 --practice "PrePostPlus Germantown"
```

**Flags**

| flag | effect |
|---|---|
| `--practice "<name>"` | pin one practice (else all discovered) |
| `--start / --end YYYY-MM-DD` | backfill window |
| `--appt-id / --patient / --date` | target mode |
| `--skip-patients` | skip the patient roster scrape |
| `--scrape-patients` | force patient scrape even in target mode |
| `--no-upload` | **dry run**: build ZIP locally, skip SFTP + `file_path` write-back |

Patient scrape defaults: **on** for daily/backfill, **off** for target.

Safe smoke test (no delivery to prod):
```
python -m ehr.cli --practice "PrePost+ Tennessee" --start 2026-07-08 --end 2026-07-11 --no-upload --skip-patients
```

---

## 8. What a run does (pass order)

Per practice, each in its **own fresh browser** (login → work → close):

1. **appointments** (daily/backfill only) — scrape worklist for the window →
   upsert rows; scrape Tebra's *Missed Charges* view → set `retry_reason`.
2. **notes** — read Finished tab (all filters checked), mark signed notes,
   capture `charge_status` badge. Daily = unbounded (catches late signs).
3. **facesheets** — per-patient dedup; download signed+unprocessed rows AND
   Missed-Charges re-downloads → `process_status='Processed'`, write
   `tebra_facesheet_id` + `patient_id`.
4. **charges** — for `charge_status='Charge in billing'` + `charge_data IS NULL`,
   open VIEW CHARGE → write `charge_data` jsonb.
5. **zip** — build ONE ZIP of undelivered processed rows (`file_path IS NULL`;
   daily/backfill) or the targeted rows (target), deliver to the practice's blob
   folder, write `file_path` back.
6. **patient-match** — reconcile `patient_id` against `ehr_patients`.

---

## 9. Server (FastAPI, port 8010) — endpoint reference

`server.py` is a thin layer over `run()`. All POST bodies are JSON. The three
identity fields (`entity`, `sub_entity`, `ehr_name`) are **optional everywhere**
— if omitted they fall back to `config.py` (`ENTITY` / `SUB_ENTITY` / `EHR_NAME`).
All run-triggering endpoints kick the work off on a **background thread** and
return `{"status":"started", ...}` immediately (they do not block until the run
finishes); progress/errors land in the console log and `wpo.ops_pch_logs`.

Common response codes:
- `200` — job accepted / started
- `400` — bad dates (`/run-tebra` only): `end_date` before `start_date`, or range
  > 7 days inclusive (`MAX_DATE_RANGE_DAYS = 6`)
- `409` — a job with the same lock key is already running (per-practice for
  `/run-tebra`, global per job type for the daily endpoints)

Start the server (headed, in the logged-on desktop session):
```
pythonw.exe -m uvicorn server:app --host 0.0.0.0 --port 8010
```

---

### `GET /healthz`
Liveness check. No params. → `{"status":"ok"}`

---

### `POST /run-tebra` — ad-hoc backfill (one practice, date window)
Runs the pipeline in **backfill** mode for one practice. Patient scrape runs
(backfill default = on).

Body (`TebraRequest`):

| field | type | required | default | notes |
|---|---|---|---|---|
| `start_date` | str `YYYY-MM-DD` | **yes** | — | window start |
| `end_date` | str `YYYY-MM-DD` | **yes** | — | window end; ≤ 7 days inclusive from start |
| `practice_name` | str | **yes** | — | must resolve to a Tebra practice (normalized match) |
| `entity` | str | no | config `ENTITY` | |
| `sub_entity` | str | no | config `SUB_ENTITY` | |
| `ehr_name` | str | no | config `EHR_NAME` | |

Validation: `400` if `end_date < start_date` or range > 7 days. `409` if a run
for the same `entity::practice_name` is already in progress.

Response: `{"status":"started","practice":<name>,"start_date":...,"end_date":...}`

```bash
curl -X POST http://localhost:8010/run-tebra \
  -H "Content-Type: application/json" \
  -d '{"start_date":"2026-07-08","end_date":"2026-07-11","practice_name":"PrePost+ Tennessee"}'
```

---

### `POST /run-tebra-daily` — Tebra daily, all practices (today)
Runs **daily** mode across all discovered practices (patient scrape on by
default). `409` if a Tebra-daily is already running.

Body (`DailyRequest`): `entity`, `sub_entity`, `ehr_name` — all optional
(fall back to config). Send `{}` to use config defaults.

Response: `{"status":"started","date":"<today>"}`

```bash
curl -X POST http://localhost:8010/run-tebra-daily \
  -H "Content-Type: application/json" -d '{}'
```

---

### `POST /run-patient-insurance-daily` — patient roster only
Runs just the patient-insurance scrape (`ehr.patients`) → `wpo.ehr_patients`.
No Tebra appointment/facesheet work. `409` if already running.

Body (`DailyRequest`): optional identity fields. Response: `{"status":"started"}`

```bash
curl -X POST http://localhost:8010/run-patient-insurance-daily \
  -H "Content-Type: application/json" -d '{}'
```

---

### `POST /run-combined-daily` — the scheduled job (patients, then Tebra)
The nightly scheduled task calls this. Runs the patient scrape **first** (with
its own error attribution), then the Tebra daily via `run(sel, scrape_patients=False)`
(so patients aren't scraped twice). Logs one `COMBINED_DAILY` row. `409` if
already running.

Body (`DailyRequest`): optional identity fields.

Response: `{"status":"started","job_id":<uuid>,"date":"<today>","message":...}`

```bash
curl -X POST http://localhost:8010/run-combined-daily \
  -H "Content-Type: application/json" -d '{}'
```

---

**Notes**
- There is **no** dedicated single-appointment (`target` mode) endpoint — target
  runs are CLI-only (`--appt-id` / `--patient`). The server exposes daily and
  windowed-backfill triggers.
- `--no-upload` (dry run) is a CLI flag only; server endpoints always deliver.
- Deploy as an at-logon scheduled task (`ExecutionTimeLimit=0`, restart on
  failure). **After deploying new code, Stop-ScheduledTask then Start-ScheduledTask
  to reload the server process.**

---

## 10. Operational notes / gotchas

- **`appt_date` is text `YYYY-MM-DD`.** Date filters compare as strings — the
  format must hold or windows silently match nothing.
- **Re-delivery** is keyed on `file_path`. To force a re-ZIP, set the rows'
  `file_path` back to `NULL`.
- **"present but NOT signed yet"** in the notes log is a correct skip, not a
  miss. Unbounded daily re-checks pick them up once signed.
- **Practice names**: the UI tile, the `--practice` arg, and the DB `practice`
  value may differ in spacing (e.g. `PrePost+Tennessee` vs `PrePost+ Tennessee`).
  Login and SFTP matching are normalized; the ZIP abbreviation is stable. Prefer
  the DB `practice` value when passing `--practice`.
- **Known scope gap:** `charge_status` is only captured on dates that have
  notes-needing appointments, so an appointment signed+facesheeted *then* charged
  later isn't revisited. Broaden the notes/charge dashboard scan if that case
  matters.
