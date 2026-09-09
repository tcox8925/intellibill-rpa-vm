# ehr-patient-sync
Synchronizes patient and coverage data from EHR systems such as Tebra and Practice Fusion into our internal database, supporting scheduled data ingestion, inserts, updates, and reliable API-based synchronization.


# Tebra Demographics Pull
Terba Pull from Tebra API

# Parse the Json Response
Parse the Json response and load demographics to patient header and insurance details to coverages tables 

# Coverage Rules
Patient matching (`load_patient_coverages.py`): a patient's `patient_header`
row is located by `lower(trim(source)) = 'tebra'` and `trim(source_id) = trim(ID)`
(case/whitespace-insensitive), never by name/DOB.

Carrier resolution: before any insert/update decision is made, the incoming
carrier name is resolved via `utils/payer_lookup.find_payer` against active
`lookup_payers` rows (matched by name or alias, narrowed by claim type -
a blank claim type defaults to professional). This resolution always runs
first, so the carrier fields written to the database - `cov_car_id`,
`cov_car_nam`, `cov_car_type` - are always the resolved values, whether the
row ends up being updated in place or replaced:
- Matched: `cov_car_id` = `payer_id`, `cov_car_nam` = `payer_name`,
  `cov_car_type` from the payer's `payer_type` list - exactly one type ->
  that type, anything else (zero, two, three, or more) -> `"Commercial"`.
- No match: `cov_car_id` stays `null` (never falls back to the raw
  Patients.json id), `cov_car_nam` falls back to the raw company name,
  `cov_car_type` = `"Commercial"`.

Active/inactive decision (`utils/coverage_rules.decide_coverage_action`) -
a patient never has two simultaneously active coverages of the same type
(`P`/`S`):

1. **No existing active coverage of this type** -> insert the new row as
   active. `effective_start_date` defaults to Jan 1 of the current year if
   missing/unparseable.
2. **Existing active coverage, same carrier + same subscriber id + same
   type** -> update the existing row in place (carrier fields, plan/insured
   fields, dates, etc. all refreshed from the incoming data).
3. **Existing active coverage, but carrier and/or subscriber id differ** ->
   always terminate the existing row and insert the new one as the sole
   active row for that type - **regardless of whether their date ranges
   overlap**. Terminating never deletes: the old row is set `active = false`
   with `effective_end_date` = the last day of the month before the new
   coverage starts, and the new row's `effective_start_date` = the 1st of
   that month. History is preserved, only the `active` flag changes.



Run Command:
python -m uvicorn app:app --reload 