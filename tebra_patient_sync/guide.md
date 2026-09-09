# EHR Data Integration Guide — Practice Fusion (FHIR) + Tebra (SOAP)
**Practice:** Northwest Arkansas Internal Medicine, PLLC (NWARK)
**Purpose:** Pull clinical data from Practice Fusion and financial/billing data from
Tebra, land both to tables, and join them so charges tie to the correct patients.
**Tenant:** Runs as an IntelliBill Azure workload (secrets in the IntelliBill Key
Vault). 834 Labs service principals cannot reach IntelliBill Key Vault, so this
pipeline must run within the IntelliBill tenant.

> **One-line summary of the division of data:** Practice Fusion = the *clinical*
> record (diagnoses, procedures, observations, meds, notes, coverage). Tebra = the
> *money* (charges, payments, balances, claim status, insurance). Neither system
> holds the other's data; they are joined on patient identity + service date.

---

# Part 1 — Practice Fusion (FHIR API)

## 1.1 What it is
- **FHIR R4**, US Core 6.1.0 (USCDI), **read-only** clinical data.
- **SMART Backend Services** auth (2-legged, system app — no user/patient login).
- Provider-side clinical only. **No billing/ledger/payments/superbills** are exposed
  (confirmed: `ExplanationOfBenefit`, `InsurancePlan`, `Contract` are not granted, and
  would not carry payment data anyway). That gap is why Tebra is required.

## 1.2 Onboarding (one-time, already completed)
1. **Developer registration** via the PDS portal (`pfpds.practicefusion.com`) under the
   "IntelliBill RCM" identity — approved (case `01476940`). `client_id` issued on
   approval (a `client_secret` was also issued but is unused — see auth below).
2. **FHIR add-on enabled** at the practice level (free "early adopter"). This was the
   gate that made the practice appear in the directory / ServiceBaseURLs.
3. **App authorized** for NWARK in the EHR App Marketplace (System app). Appears under
   the practice's Authorized apps with "Revoke access."
4. **Verify access** (re-runnable checks for any new app/practice):
   - `python practice_fusion_full_export.py --selftest` — signs a JWT via Key Vault and
     verifies it against the published JWKS (proves key + signing + JWKS without calling
     PF). `fhir_token_minter.py --selftest` still works too (thin compatibility shim over
     the same code, kept only for `fhir_bulk_probe.py` / `fhir_sample_to_json.py`).
   - `python practice_fusion_full_export.py` — mints a live bearer token as part of the
     export run (proves `client_id` + auth).
   - `python fhir_bulk_probe.py` — confirms bulk `$export` is permitted: kicks a
     `Patient`-scoped export, expects `202` + a `Content-Location` status URL, polls once,
     then cancels the job (read-only; pulls no data). A `202` here = all-patient export is
     available; `403` = fall back to the REST crawl for the initial load.

## 1.3 Connection endpoints
| Item | Value |
|---|---|
| Org UUID | `d01a1865-6e13-4262-b0d5-0d897b5aa9a8` (NWARK) |
| FHIR base URL | `https://api.practicefusion.com/fhir/r4/v1/d01a1865-6e13-4262-b0d5-0d897b5aa9a8` |
| SMART config | `{BASE}/.well-known/smart-configuration` (discovers the token endpoint) |
| Capability | `{BASE}/metadata` |

## 1.4 Authentication — SMART Backend Services (private_key_jwt)
Flow (`client_credentials` + `private_key_jwt`, **RS384**):

1. Discover the **token endpoint** from `{BASE}/.well-known/smart-configuration`.
2. Build a **JWT client assertion**:
   - header: `alg=RS384`, `typ=JWT`, `kid=<RFC-7638 thumbprint>`
   - claims: `iss=sub=<client_id>`, `aud=<token endpoint>`, unique `jti`, short `exp` (≤300s)
3. **Sign the assertion with the Key Vault key** — the private key never leaves the
   vault (we SHA-384 the signing input locally; Key Vault signs the digest via RS384).
4. **POST to the token endpoint**: `grant_type=client_credentials`,
   `client_assertion_type=urn:ietf:params:oauth:client-assertion-type:jwt-bearer`,
   `client_assertion=<signed JWT>`, `scope=<system/*.rs …>`.
5. Response returns an **opaque bearer token** (reference token, not a JWT) with
   `expires_in`. Send it as `Authorization: Bearer <token>` on every FHIR call.

### Key material (in IntelliBill Key Vault)
| Item | Value |
|---|---|
| Key Vault | `kv-ibrcm-prd001` |
| Key name | `fhir-token` (RSA; key pair generated *in* the vault) |
| `kid` | `N-NovoNc82OwmshzwQlxRODqYFqj8Q9c7De1HIRVzRE` (must match the JWT header) |
| Public JWKS | `https://ibrcmdataprd001.z19.web.core.windows.net/jwks.json` (unauthenticated static site) |

### Token handling
- **Short-lived**; treat like a password. Cache in memory with a **60-second safety
  buffer** (renew before expiry); refresh reactively on any `401`.
- Do **not** persist tokens to disk — signing a fresh JWT is cheap.
- Auth to Key Vault is chosen by `PF_AUTH_MODE`, not an ambiguous `1`/unset flag:
  - `PF_AUTH_MODE=client_secret` (interim, buildout only) + `PF_KV_TENANT_ID` /
    `PF_KV_CLIENT_ID` / `PF_KV_CLIENT_SECRET` — an IntelliBill app registration
    with GET + SIGN on `fhir-token`.
  - `PF_AUTH_MODE=managed_identity` (default, production) — `DefaultAzureCredential`.
    No code change to move from interim to production, just this one env flip.
    Any other/misspelled value fails loudly instead of silently doing the wrong
    thing.
- **If more than one managed identity or subscription becomes reachable from
  where this runs** (e.g. a second Azure subscription gets added later), pin
  the exact identity Key Vault should trust with `PF_MANAGED_IDENTITY_CLIENT_ID`.
  Note this is the managed identity's **Client ID**, not the subscription ID —
  subscription ID doesn't gate Key Vault auth, the identity does. To look these
  up with the Azure CLI:
  ```
  az login
  az account list -o table                                   # see all subscriptions you can reach
  az account set --subscription "<name-or-id>"                # pick the right one
  az account show --query id -o tsv                            # subscription ID (for reference/docs)
  az account show --query tenantId -o tsv                      # tenant ID
  az identity list -o table                                    # user-assigned managed identities
  az identity show -g <resource-group> -n <identity-name> \
    --query clientId -o tsv                                    # -> PF_MANAGED_IDENTITY_CLIENT_ID
  ```
  Then confirm Key Vault access with `az role assignment list --assignee <clientId>
  --scope <vault-resource-id>` before wiring it into `.env`.

### Config (env / local `.env`, never in a mirrored repo)
```
PF_CLIENT_ID=<issued on approval>
PF_BASE_URL=https://api.practicefusion.com/fhir/r4/v1/d01a1865-6e13-4262-b0d5-0d897b5aa9a8
PF_AUTH_MODE=managed_identity
PF_MANAGED_IDENTITY_CLIENT_ID=<only if disambiguating multiple identities>
```

## 1.5 Scopes granted
All offered `system/*.rs` (SMART v2 granular read/search) for the USCDI set. There is no
self-serve way to add resources outside this list — the checklist *is* the ceiling for a
third-party System app.

## 1.6 What data we get (verified by survey — 10-record probe per resource)

**Populated & accessible (24):**
Patient, Coverage, Encounter, Condition, Procedure, Observation, MedicationRequest,
MedicationDispense, DiagnosticReport, DocumentReference, AllergyIntolerance, Immunization,
CarePlan, CareTeam, Goal, Device, ServiceRequest, Provenance, RelatedPerson, Organization,
Location, Practitioner (+ MessageHeader, infra).

**Accessible but empty (2):** `Medication` (meds are inline on MedicationRequest), `Group`
(no export groups defined).

**Forbidden — 403, not granted to this app tier (18):**
EpisodeOfCare, ExplanationOfBenefit, InsurancePlan, Contract, PractitionerRole,
HealthcareService, OrganizationAffiliation, MedicationAdministration, Flag, List,
Composition, Task, AuditEvent, ImagingStudy, Substance, Questionnaire,
QuestionnaireResponse, FamilyMemberHistory. None are needed; the financial-shaped ones
(EOB/InsurancePlan/Contract) would not carry payment data even if granted.

### Key resources for RCM
| Resource | Gives you |
|---|---|
| Patient | Demographics, address, phone, MRN (`PCLH-L3T-PJNL` style), status |
| Coverage | Insurance: payer (→Organization), plan, member, effective dates |
| Encounter | Visits (RCM fact anchor); references an `Account` that is **not** readable (403) |
| Condition | Diagnoses (→ ICD-10), encounter-linked |
| Procedure | Procedures (→ CPT) |
| Observation | Vitals, labs, **tobacco/smoking status** (US Core smokingstatus profile) |
| MedicationRequest | Prescriptions (meds inline) |
| DocumentReference | Clinical notes / C-CDA |

## 1.7 Data model / join within FHIR
- **PK** = resource `id` (UUID). **FK** = any `reference` field, formatted `"Type/id"`.
- **Patient is the hub** (nearly every resource carries `subject → Patient`).
- **Encounter is the sub-hub** (Condition/MedicationRequest reference it via `encounter`).
- Dimensions: Organization, Practitioner, Location. Coverage = insurance
  (`beneficiary → Patient`, `payor → Organization`).
- Empirically in a sample: Patient 177 inbound edges, Practitioner 86, Organization 73,
  Encounter 35. An `Account` reference appears on Encounter/Provenance but is unreadable —
  the financial layer sits just past the FHIR fence.

## 1.8 How to pull
**Initial full load → Bulk `$export`** (all-patient export is permitted for this app):
1. `GET {BASE}/Patient/$export` with `Prefer: respond-async` → `202` + `Content-Location`
   (async status URL). `_type=` optional (defaults to all compartment types); `_since=`
   optional.
2. Poll the status URL until `200` + a manifest (`output` = list of `{type, url}` NDJSON
   files). Honor `Retry-After` / `X-Progress` while `202`.
3. Download each NDJSON file, flatten, load per resource. `DELETE` the job to clean up.

**Incremental → REST crawl with `_lastUpdated`:**
- `GET {BASE}/{Resource}?_lastUpdated=gt{watermark}&_count=200`, follow `link[relation=next]`
  (page-based pagination — use the `next` URL verbatim, do not build `page=N`). Store the
  max `meta.lastUpdated` per resource as the watermark.

**Targeted → REST** `?patient={id}` or `/{Resource}/{id}`.

**Rate limits:** not documented by Practice Fusion. Be adaptive — honor `429` +
`Retry-After`, exponential backoff on `429/5xx`, gentle default pace (~5/sec).

## 1.9 Scripts (FHIR)
| Script | Purpose |
|---|---|
| `practice_fusion_full_export.py` | Self-contained: SMART backend auth (Key Vault signing, `--selftest`) + full REST-crawl snapshot → one JSON file per resource |
| `fhir_token_minter.py` | Compatibility shim re-exporting the auth functions above; kept so `fhir_bulk_probe.py` / `fhir_sample_to_json.py` don't need changes |
| `fhir_bulk_probe.py` | Confirm `$export` is permitted (kick → poll once → cancel) |
| `fhir_pipeline_8588.py` | `bulk` (export→NDJSON→CSV) and `crawl` (paginated deltas); `--limit`, `--since`, `--patient` |
| `fhir_sample_to_json.py` | Survey: 10 records per resource → one JSON file, array per resource |
| `fhir_link_model.py` | Extract the reference graph (PK/FK edges, hubs) from data |

---

# Part 2 — Tebra (Kareo SOAP API)

## 2.1 What it is
- **SOAP / XML** web service (Kareo lineage). No REST, no OAuth, no token.
- This is where **charges, payments, balances, claim status, and insurance** live — the
  financial/RCM data Practice Fusion does not expose.
- **WSDL:** `https://webservice.kareo.com/services/soap/2.1/KareoServices.svc?singleWsdl`
- Recommended client: Python `zeep` (loads the WSDL; pass requests as nested dicts).

## 2.2 Authentication — RequestHeader on every call
There is no token step. Every operation carries a **RequestHeader**:
| Field | Notes |
|---|---|
| `CustomerKey` | Account API secret. Enabled by a System Admin via a Tebra Customer Care case. Treat as a password. |
| `User` | Login of an API-permissioned user (use a dedicated integration/service user). |
| `Password` | That user's password. **Must be XML-escaped** (`&`→`&amp;`, etc.) — a raw `&` breaks auth. |

Plus **`PracticeName` is required in the Filter of every Get** call (scopes the query and
keeps responses small). Use the exact string, e.g. `Northwest Arkansas Internal Medicine, PLLC`.

### Config (env / local `.env`)
```
TEBRA_CUSTOMER_KEY=<account key>
TEBRA_USER=<api user login>
TEBRA_PASSWORD=<password>
TEBRA_PRACTICE=Northwest Arkansas Internal Medicine, PLLC
```
For production, these belong in the IntelliBill Key Vault alongside `fhir-token`.

## 2.3 Account structure
The Tebra account is **multi-practice**: PrePost+Tennessee, PrePostPlus Atlanta (Buckhead),
PrePostPlus Germantown, PrePostPlus Nashville (Midtown), The PreOp Center, **and NWARK
(Northwest Arkansas Internal Medicine, PLLC, ID 99491)**. NWARK is the practice that
matches the Practice Fusion instance — always filter to it for the PF↔Tebra join. (NWARK
billing is lightly used: ~30 procedures / ~$766 charges historically, so charge volumes
are small — that is real, not a bug.)

## 2.4 Gotchas / hard constraints (learned by testing)
- **XML-escape the password** (raw `&` fails auth).
- **Date filters must be paired** (a `From…` requires the matching `To…`).
- **`GetCharges` and `GetAppointments` cap the date window at 60 days** — chunk wider
  ranges into ≤60-day slices and loop.
- **`IncludeUnapprovedCharges` must be a boolean `true`**, not the string `"T"`
  (a `"T"` yields "Error converting data type nvarchar to bit").
- **`GetProcedureCodes` is plural** (singular `GetProcedureCode` does not exist).
- **`GetPractices` throws a harmless .NET null** when filtered by `PracticeName`; use an
  `Active` filter instead, or ignore it (not needed for the join).
- **Per-method rate limits (documented) — pace to these:**
  | Method | Limit |
  |---|---|
  | `GetPatient` (single) | 4 / sec (1 per ¼s) |
  | `GetPatients`, `GetCharges`, `GetPayments`, `GetAppointments`, `GetTransactions` | 1 / sec |
  | `GetProviders`, `GetServiceLocations`, `GetProcedureCodes`, `GetPractices` | ~2 / sec (1 per ½s) |
  Retry on the "429 … wait to try again" message with backoff.
- Every response carries `ErrorResponse` (IsError/ErrorMessage) and `SecurityResponse`
  (Authenticated/Authorized/PermissionsMissing) — check these to distinguish auth vs.
  permission vs. empty-result.

## 2.5 What data we get (verified against NWARK)
Working Get methods: `GetProviders`, `GetServiceLocations`, `GetProcedureCodes`,
`GetPatients`, `GetCharges`, `GetPayments`, `GetAppointments`, `GetTransactions`.

### GetPatients — 196 fields, rich demographics + insurance + balances
Key columns: `ID`, `MedicalRecordNumber` (native format e.g. `RM221593`), `FirstName`,
`LastName`, `PatientFullName`, `DOB`, `Gender`, address/phone/email, `PatientBalance`,
`InsuranceBalance`, `TotalBalance`, and full **primary & secondary insurance** blocks
(company, plan, policy number, group, copay, deductible, effective dates, insured details).
**No `ExternalID` column is returned.**

### GetCharges — the financial fact table
Returns: `EncounterID`, `PatientID`, `PatientName`, `PatientDateOfBirth`,
`ServiceStartDate`/`ServiceEndDate`, `PostingDate`, `ProcedureCode` (+ modifiers),
`DiagnosisCode1–4`, `Units`, `UnitCharge`, `TotalCharges`, `AdjustedCharges`, `Receipts`,
`InsuranceBalance`, `PatientBalance`, `TotalBalance`, rendering/scheduling/referring
providers, `Status`, `BilledTo`, `EncounterStatus`.

**Filterable** by: date windows (`FromServiceDate`/`ToServiceDate`, `FromPostingDate`/…,
`FromCreatedDate`/…, `FromLastModifiedDate`/…), `PatientName`, `Status`, `BilledTo`,
`ProcedureCode`, `DiagnosisCode`, provider/location names, `EncounterStatus`,
`IncludeUnapprovedCharges`. **Note:** the only patient filter is `PatientName` — there is
no PatientID/MRN/ExternalID charge filter.

### Two different "status" concepts (important)
- **`EncounterStatus`** = coding **workflow** state: `Draft` / `Review` / `Approved` /
  `Rejected`. At NWARK effectively always `Approved` — **not** the useful axis.
- **`Status` + `BilledTo`** (+ the balance fields) = the **claim / AR** status you want.
  "Outstanding" = `TotalBalance > 0` (or `InsuranceBalance`/`PatientBalance > 0`);
  `BilledTo` = Insurance vs. Patient. Enumerated `Status` values should be read from live
  data (the guide names the field, not its values), then filtered server-side via `Status`
  or client-side on balances.

`GetPayments` and `GetTransactions` provide the payment/adjustment ledger side.

## 2.6 Scripts (Tebra)
| Script | Purpose |
|---|---|
| `tebra_sample_to_excel.py` | Sample core Get methods → one workbook, sheet per method; flags `--practice`, `--days`, `--limit`, `--status`, `--billed-to`, `--include-unapproved`, `--patient-name`, `--patient-external-id` |

---

# Part 3 — Joining Practice Fusion ↔ Tebra (NOT YET DETERMINED)

> **Status:** The cross-system join has **not** been designed or validated yet. This
> section records only the verified facts about patient identifiers in each system. The
> actual join key and matching approach are still to be decided in a later phase.

## 3.1 Identifier facts (verified)
The two systems share **no common primary key**:
- Practice Fusion patient id is a FHIR UUID (`8d1a9ecc-…`); its MRN is `PCLH-L3T-PJNL` style.
- Tebra patient id is its own integer (`259`); its MRN is native (`RM221593`).
- Tebra's `PatientExternalID` (the field *designed* to hold a third-party id) was checked
  and is **not populated** with the PF id, and is not returned by `GetPatients`.

Both systems do expose patient **name** and **DOB**, and `GetCharges` returns
`ServiceStartDate` + `PatientName`, so a time- and patient-aligned join is *possible* — but
the exact key, normalization, and match-quality handling are **open items**, not settled.

## 3.2 Open items for the join phase
- Decide and validate the join key (candidates: name+DOB; or backfilling PF id into Tebra
  `PatientExternalID` to make it deterministic).
- Prove any chosen key on a known patient present in **both** systems.
- Confirm enumerated Tebra `Status` values from live data; build the outstanding-charges filter.

---

# Part 4 — Security notes
- All secrets (PF Key Vault creds, Tebra `CustomerKey`/`User`/`Password`) belong in the
  **IntelliBill Key Vault**, never in a repo or a `.env` that could be mirrored.
- The FHIR private key is generated in and never leaves Key Vault.
- Tebra password must be XML-escaped in the SOAP header.
- Prefer a dedicated Tebra integration/service user (scoped, rotatable), not a person's login.


# how to run
python3 -m venv venv

source venv/bin/activate 
pip install requests pandas python-dotenv PyJWT azure-identity azure-keyvault-keys
python practice_fusion_full_export.py --selftest   # verify Key Vault signing, no PF call
python practice_fusion_full_export.py              # full snapshot export
python fhir_pipeline_8588.py bulk --since "2026-07-25T00:00:00Z"