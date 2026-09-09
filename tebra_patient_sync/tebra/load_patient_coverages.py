"""Python port of loadPatientCoverages.ts.

Loads patient coverage data from responses/Patients.json, creating
patient_coverages records for Primary and Secondary insurance policies,
linked to patient_header via patient_header_id. Patients are matched to
their patient_header row by source_id = patient.ID, source = "tebra".

No claim_header / claim_details references. Unlike the original TS
(which just deletes-then-inserts per coverage type), carrier resolution
goes through utils/payer_lookup.py (lookup_payers, defaulting to
professional claims when no claim type applies), and whether an incoming
coverage updates the existing active row, inserts alongside it, or
terminates it in favor of a new row is decided by
utils/coverage_rules.py's decide_coverage_action - the same active/
inactive rule pf_patient_load.py uses for Practice Fusion coverage.
"""

import json
import os
from datetime import date, datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from models.lookup_payers import LookupPayer
from models.patient_coverages import PatientCoverage
from models.patient_header import PatientHeader
from tebra.paths import PATIENTS_JSON_PATH
from utils.coverage_rules import (
    ACTION_TERMINATE_AND_INSERT,
    ACTION_UPDATE,
    decide_coverage_action,
    first_day_of_month,
    format_date,
    is_same_coverage,
    parse_date,
    terminate_end_date,
)
from utils.db import get_engine
from utils.payer_lookup import carrier_key, find_payer

BATCH_SIZE = 100

COVERAGE_TYPES = [("P", "Primary"), ("S", "Secondary")]

# Columns copied wholesale from an incoming coverage dict onto an existing
# PatientCoverage row when decide_coverage_action says ACTION_UPDATE.
UPDATE_COLUMNS = [
    "cov_status", "cov_car_id", "cov_car_nam", "cov_car_type", "cov_rel", "cov_sub_id",
    "cov_dep_id", "cov_dep_name", "cov_start_date", "cov_end_date", "insurance_type",
    "company_id", "company_name", "policy_number", "group_number", "copay", "deductible",
    "effective_start_date", "effective_end_date", "patient_relationship_to_insured",
    "insured_full_name", "insured_id_number", "insured_ssn", "insured_dob", "insured_gender",
    "insured_address1", "insured_address2", "insured_city", "insured_state", "insured_zip",
    "insured_country", "insured_notes", "plan_id", "plan_name", "plan_address1",
    "plan_address2", "plan_city", "plan_state", "plan_zip", "plan_country", "plan_phone",
    "plan_phone_ext", "plan_fax", "plan_fax_ext", "plan_adjuster_name",
]


def parse_amount(value):
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def is_patient_insured(relationship: str) -> bool:
    return relationship == "S"


def carrier_type_from_payer_types(payer_type: list | None) -> str:
    """cov_car_type from lookup_payers.payer_type:
    - exactly one type -> that type
    - anything else (zero, two, three, or more) -> "Commercial"
    """
    types = payer_type or []
    if len(types) == 1:
        return types[0]
    return "Commercial"


def default_effective_start() -> date:
    """A missing/unparseable effective_start_date defaults to Jan 1 of the
    current year, not today's date."""
    return date(date.today().year, 1, 1)


def fetch_active_payers(session: Session) -> list[tuple]:
    # lookup_payers has duplicate active rows sharing the same payer_name
    # (e.g. 4 separate "Meritain Health" rows with different payer_ids).
    # find_payer() returns the first name/alias match it sees, so without a
    # stable order here the same name can resolve to a different payer_id
    # on every call - the incoming and existing carrier keys then disagree
    # on a coverage that hasn't actually changed, causing an endless
    # terminate-and-insert loop instead of an update. Ordering by payer_id
    # makes "first match wins" deterministic: the same name always resolves
    # to the same (lowest) payer_id, every run.
    return session.execute(
        select(
            LookupPayer.payer_id,
            LookupPayer.payer_name,
            LookupPayer.payer_type,
            LookupPayer.transaction_type,
            LookupPayer.payer_alias,
        )
        .where(LookupPayer.active_status.is_(True))
        .order_by(LookupPayer.payer_id)
    ).all()


def build_patient_header_map(session: Session) -> dict:
    """trimmed source_id -> PatientHeader row, for source = "tebra" - fetched
    once instead of once per patient (same fix as load_patient_header.py's
    existing-patient lookup: on a populated DB, a per-patient query against
    lower(trim(source))/trim(source_id) can't use a plain index and pays a
    full scan every time - multiplied by thousands of patients, that's the
    difference between a 10s local run against an empty table and a much
    slower dev run against a populated one)."""
    headers = session.execute(
        select(PatientHeader).where(func.lower(func.trim(PatientHeader.source)) == "tebra")
    ).scalars().all()
    return {(header.source_id or "").strip(): header for header in headers}


def build_active_coverage_map(session: Session, patient_header_ids: list) -> dict:
    """(patient_header_id, cov_type) -> a plain dict of just the columns
    process_coverage actually reads for its decisions, for the single active
    PatientCoverage row - fetched once for every patient instead of once per
    patient per coverage type. When more than one active row exists for the
    same key (shouldn't happen under the active/inactive rule, but this
    defensively matches the old per-query "most recent effective_start_date
    wins" behavior), the first one seen - ordered by effective_start_date
    desc - wins.

    A plain dict instead of the mapped PatientCoverage row on purpose: these
    rows are never mutated in place anymore (see process_coverage) - an
    ACTION_UPDATE/ACTION_TERMINATE_AND_INSERT decision instead appends a
    column dict to update_payload/insert_payload, applied as one bulk
    executemany per batch in run_load_patient_coverages instead of one
    UPDATE/INSERT per row.
    """
    if not patient_header_ids:
        return {}
    rows = session.execute(
        select(
            PatientCoverage.id,
            PatientCoverage.patient_header_id,
            PatientCoverage.cov_type,
            PatientCoverage.cov_car_id,
            PatientCoverage.cov_car_nam,
            PatientCoverage.cov_sub_id,
            PatientCoverage.effective_start_date,
            PatientCoverage.effective_end_date,
        )
        .where(
            PatientCoverage.patient_header_id.in_(patient_header_ids),
            PatientCoverage.active.is_(True),
        )
        .order_by(PatientCoverage.effective_start_date.desc().nullslast())
    ).all()
    coverage_map = {}
    for row in rows:
        coverage_map.setdefault(
            (row.patient_header_id, row.cov_type),
            {
                "id": row.id,
                "patient_header_id": row.patient_header_id,
                "cov_type": row.cov_type,
                "cov_car_id": row.cov_car_id,
                "cov_car_nam": row.cov_car_nam,
                "cov_sub_id": row.cov_sub_id,
                "effective_start_date": row.effective_start_date,
                "effective_end_date": row.effective_end_date,
            },
        )
    return coverage_map


def build_inactive_coverage_map(session: Session, patient_header_ids: list) -> dict:
    """(patient_header_id, cov_type) -> list of that patient/type's inactive
    (terminated) PatientCoverage rows, most recent effective_start_date
    first - reactivation candidates for process_coverage. Without this, a
    coverage that comes back exactly as it was before (e.g. a patient goes
    Aetna -> Cigna -> Aetna again) would insert a brand-new row identical to
    one already sitting inactive in history, instead of reactivating it -
    decide_coverage_action's ACTION_REACTIVATE exists precisely for this,
    but only fires when a candidate is actually offered to it."""
    if not patient_header_ids:
        return {}
    rows = session.execute(
        select(
            PatientCoverage.id,
            PatientCoverage.patient_header_id,
            PatientCoverage.cov_type,
            PatientCoverage.cov_car_id,
            PatientCoverage.cov_car_nam,
            PatientCoverage.cov_sub_id,
            PatientCoverage.effective_start_date,
            PatientCoverage.effective_end_date,
        )
        .where(
            PatientCoverage.patient_header_id.in_(patient_header_ids),
            PatientCoverage.active.is_(False),
        )
        .order_by(PatientCoverage.effective_start_date.desc().nullslast())
    ).all()
    candidates: dict = {}
    for row in rows:
        candidates.setdefault((row.patient_header_id, row.cov_type), []).append({
            "id": row.id,
            "cov_car_id": row.cov_car_id,
            "cov_car_nam": row.cov_car_nam,
            "cov_sub_id": row.cov_sub_id,
            "effective_start_date": row.effective_start_date,
            "effective_end_date": row.effective_end_date,
        })
    return candidates


def find_reactivation_candidate(
    candidates: list,
    payer,
    company_name: str,
    incoming_sub_id: object,
    coverage_type: str,
    active_payers: list,
):
    """Pops and returns the first inactive candidate (from build_inactive_
    coverage_map's per-key list) whose carrier+subscriber+type exactly
    matches the incoming coverage, or None. Popping it means a later
    duplicate patient entry in the same run won't try to reactivate the
    same now-already-reactivated row a second time - active_coverage_map
    holds the reactivated row as the active one for any further lookup."""
    for index, candidate in enumerate(candidates):
        candidate_payer = find_payer(candidate["cov_car_nam"], "", active_payers)
        candidate_key = carrier_key(candidate_payer, candidate["cov_car_nam"], stored_id=candidate["cov_car_id"] or "")
        incoming_key = carrier_key(payer, company_name or "", stored_id=candidate["cov_car_id"] or "")
        if is_same_coverage(
            candidate_key, incoming_key, candidate["cov_sub_id"], incoming_sub_id, coverage_type, coverage_type,
        ):
            return candidates.pop(index)
    return None


def build_coverage_record(patient: dict, pat_header: PatientHeader, coverage_type: str, prefix: str, payer) -> dict:
    """prefix is "Primary" or "Secondary" - matches the PrimaryInsurancePolicy*
    / SecondaryInsurancePolicy* field naming in Patients.json."""

    def field(name: str):
        return patient.get(f"{prefix}InsurancePolicy{name}")

    relationship = field("PatientRelationshipToInsured")
    insured = is_patient_insured(relationship)

    raw_company_id = field("CompanyID")
    raw_company_name = field("CompanyName")

    if payer is not None:
        # Matched: id/name/type come from lookup_payers, never the raw
        # Patients.json values.
        carrier_id = payer.payer_id
        carrier_name = payer.payer_name
        carrier_type = carrier_type_from_payer_types(payer.payer_type)
    else:
        # No match: cov_car_id stays null (there's no resolved carrier id
        # to fall back to) - only cov_car_nam falls back to the raw name.
        carrier_id = None
        carrier_name = raw_company_name
        carrier_type = "Commercial"

    return {
        "patient_header_id": pat_header.patient_header_id,
        "source": "tebra",
        "client_id": pat_header.client_id,
        "group_id": pat_header.group_id,
        "practice_id": pat_header.practice_id,
        "pat_id": pat_header.pat_id,
        "pat_source": pat_header.source,
        "pat_sub_lnam": pat_header.sub_lnam,
        "pat_fnam": pat_header.pat_fnam,
        "pat_dob": pat_header.pat_dob,
        "cov_type": coverage_type,
        "insurance_type": prefix,
        "cov_status": None,
        "cov_car_type": carrier_type,
        "cov_car_id": carrier_id,
        "cov_car_nam": carrier_name,
        "company_id": raw_company_id,
        "company_name": raw_company_name or None,
        "policy_number": field("Number") or None,
        "group_number": field("GroupNumber") or None,
        "copay": parse_amount(field("Copay")),
        "deductible": parse_amount(field("Deductible")),
        "effective_start_date": field("EffectiveStartDate") or None,
        "effective_end_date": field("EffectiveEndDate") or None,
        "cov_rel": relationship or None,
        "patient_relationship_to_insured": relationship or None,
        "insured_full_name": (
            f"{patient.get('FirstName')} {patient.get('LastName')}"
            if insured
            else (field("InsuredFullName") or None)
        ),
        # InsuredIDNumber always comes in as-is - patient.ID is only used to
        # locate the correct patient_header row, never as insurance data.
        "insured_id_number": field("InsuredIDNumber") or None,
        "insured_ssn": patient.get("SSN") if insured else (field("InsuredSocialSecurityNumber") or None),
        "insured_dob": patient.get("DOB") if insured else (field("InsuredDateOfBirth") or None),
        "insured_gender": patient.get("Gender") if insured else (field("InsuredGender") or None),
        "insured_address1": patient.get("AddressLine1") if insured else (field("InsuredAddressLine1") or None),
        "insured_address2": patient.get("AddressLine2") if insured else (field("InsuredAddressLine2") or None),
        "insured_city": patient.get("City") if insured else (field("InsuredCity") or None),
        "insured_state": patient.get("State") if insured else (field("InsuredState") or None),
        "insured_zip": patient.get("ZipCode") if insured else (field("InsuredZipCode") or None),
        "insured_country": patient.get("Country") if insured else (field("InsuredCountry") or None),
        "insured_notes": field("InsuredNotes") or None,
        "plan_id": field("PlanID") or None,
        "plan_name": field("PlanName") or None,
        "plan_address1": field("PlanAddressLine1") or None,
        "plan_address2": field("PlanAddressLine2") or None,
        "plan_city": field("PlanCity") or None,
        "plan_state": field("PlanState") or None,
        "plan_zip": field("PlanZipCode") or None,
        "plan_country": field("PlanCountry") or None,
        "plan_phone": field("PlanPhoneNumber") or None,
        "plan_phone_ext": field("PlanPhoneNumberExt") or None,
        "plan_fax": field("PlanFaxNumber") or None,
        "plan_fax_ext": field("PlanFaxNumberExt") or None,
        "plan_adjuster_name": field("PlanAdjusterFullName") or None,
        "cov_sub_id": field("Number") or None,
        "cov_dep_id": field("Number") if insured else None,
        "cov_dep_name": None,
        "cov_start_date": None,
        "cov_end_date": None,
    }


def _reactivate_candidate(
    candidate_id: int,
    incoming: dict,
    new_start: date,
    new_end: date | None,
    now: datetime,
    map_key: tuple,
    pat_header: PatientHeader,
    coverage_type: str,
    update_payload: list,
    active_coverage_map: dict,
) -> None:
    """Appends the bulk-UPDATE row that brings an inactive candidate back to
    active with this run's fields, and updates active_coverage_map so it's
    now the active row for map_key (same UPDATE_COLUMNS refresh ACTION_UPDATE
    already does, plus flipping active back on)."""
    reactivate_values = {
        column: incoming[column] for column in UPDATE_COLUMNS
        if column not in ("effective_start_date", "effective_end_date")
    }
    reactivate_values["effective_start_date"] = format_date(new_start)
    reactivate_values["effective_end_date"] = format_date(new_end)
    reactivate_values["active"] = True
    reactivate_values["updated_at"] = now
    update_payload.append({"id": candidate_id, **reactivate_values})
    active_coverage_map[map_key] = {
        "id": candidate_id,
        "patient_header_id": pat_header.patient_header_id,
        "cov_type": coverage_type,
        "cov_car_id": incoming["cov_car_id"],
        "cov_car_nam": incoming["cov_car_nam"],
        "cov_sub_id": incoming["cov_sub_id"],
        "effective_start_date": reactivate_values["effective_start_date"],
        "effective_end_date": reactivate_values["effective_end_date"],
    }


def process_coverage(
    patient: dict,
    pat_header: PatientHeader,
    coverage_type: str,
    prefix: str,
    active_payers: list,
    active_coverage_map: dict,
    inactive_candidates: dict,
    insert_payload: list,
    update_payload: list,
) -> str | None:
    """Decides insert/update/terminate-and-insert/reactivate for one
    patient/coverage type, same rules as before, but never writes to the DB
    directly - it appends a plain column dict to insert_payload or
    update_payload (both accumulated across a whole batch and flushed as one
    bulk executemany each in run_load_patient_coverages, instead of one
    INSERT/UPDATE per row)."""
    company_id = patient.get(f"{prefix}InsurancePolicyCompanyID")
    company_name = patient.get(f"{prefix}InsurancePolicyCompanyName")
    if not company_id and not company_name:
        return None

    # Empty claim type defaults to professional (see utils/payer_lookup.py) -
    # Tebra patient coverage is treated the same way Practice Fusion's is.
    payer = find_payer(company_name, "", active_payers)
    incoming = build_coverage_record(patient, pat_header, coverage_type, prefix, payer)

    new_start = parse_date(incoming["effective_start_date"]) or default_effective_start()
    new_end = parse_date(incoming["effective_end_date"])

    map_key = (pat_header.patient_header_id, coverage_type)
    existing = active_coverage_map.get(map_key)
    now = datetime.utcnow()

    if existing is None:
        # Before inserting a brand-new row, check whether this exact
        # coverage (same carrier+subscriber+type) already exists inactive in
        # this patient's history - e.g. it was terminated once before and is
        # now coming back. Reactivating it instead avoids piling up
        # duplicate-looking rows for what's really the same policy.
        candidate = find_reactivation_candidate(
            inactive_candidates.get(map_key, []), payer, company_name, incoming["cov_sub_id"], coverage_type, active_payers,
        )
        if candidate is not None:
            _reactivate_candidate(
                candidate["id"], incoming, new_start, new_end, now, map_key, pat_header, coverage_type,
                update_payload, active_coverage_map,
            )
            return f"  ✓ Reactivated {coverage_type} coverage for patient {pat_header.source_id}: {company_name}"

        incoming["effective_start_date"] = format_date(new_start)
        incoming["effective_end_date"] = format_date(new_end)
        incoming["active"] = True  # explicit so every insert_payload row shares the same keys
        insert_payload.append(incoming)
        # Keep the in-memory map current - active_coverage_map is fetched
        # once up front (not re-queried per patient), so without this a
        # duplicate patient entry later in the same run wouldn't see this
        # insert and would insert a second row instead of updating it.
        # "_pending_row" points at the same dict sitting in insert_payload -
        # id stays None until the bulk INSERT actually runs, so a later
        # duplicate this run mutates the pending row in place (see below)
        # rather than targeting a bulk UPDATE against a row that doesn't
        # exist in the DB yet.
        active_coverage_map[map_key] = {
            "id": None,
            "patient_header_id": pat_header.patient_header_id,
            "cov_type": coverage_type,
            "cov_car_id": incoming["cov_car_id"],
            "cov_car_nam": incoming["cov_car_nam"],
            "cov_sub_id": incoming["cov_sub_id"],
            "effective_start_date": incoming["effective_start_date"],
            "effective_end_date": incoming["effective_end_date"],
            "_pending_row": incoming,
        }
        return f"  ✓ Inserted {coverage_type} coverage for patient {pat_header.source_id}: {company_name}"

    existing_payer = find_payer(existing["cov_car_nam"], "", active_payers)
    existing_key = carrier_key(existing_payer, existing["cov_car_nam"], stored_id=existing["cov_car_id"] or "")
    # Same stored_id fallback as existing_key - if find_payer fails to
    # resolve a payer on both sides this run (e.g. the payer got
    # deactivated in lookup_payers since it was last matched), both keys
    # fall back to the SAME already-known carrier id instead of existing
    # falling back to "id:<old id>" while incoming falls back to a bare
    # "name:<raw name>" key that can never equal it - which would flag a
    # resolution hiccup as a carrier change and wrongly terminate+insert a
    # coverage that hasn't actually changed.
    incoming_key = carrier_key(payer, company_name or "", stored_id=existing["cov_car_id"] or "")

    action = decide_coverage_action(
        existing_carrier_key=existing_key,
        incoming_carrier_key=incoming_key,
        existing_subscriber_id=existing["cov_sub_id"],
        incoming_subscriber_id=incoming["cov_sub_id"],
        existing_type=existing["cov_type"] or "",
        incoming_type=coverage_type,
    )

    if action == ACTION_UPDATE:
        update_values = {}
        for column in UPDATE_COLUMNS:
            if column in ("effective_start_date", "effective_end_date"):
                # incoming[column] is still the raw Patients.json value here
                # (never defaulted/parsed like the insert/terminate branches
                # do) - source usually omits it entirely, so blindly copying
                # it over would null out the existing row's real start/end
                # date every time an unchanged coverage gets re-synced. Only
                # overwrite when the source actually reports a real date
                # this run; otherwise fall back to the existing row's own
                # value - every dict in the same bulk UPDATE batch must set
                # the same columns, so the column can't just be omitted the
                # way the old per-row setattr() version left it untouched.
                parsed = parse_date(incoming[column])
                update_values[column] = format_date(parsed) if parsed is not None else existing[column]
                continue
            update_values[column] = incoming[column]
        update_values["updated_at"] = now

        if existing["id"] is None:
            # Still a pending insert from earlier in this same run (the
            # source file listed this patient/coverage type twice) - mutate
            # the pending insert_payload row in place instead of appending a
            # bulk UPDATE against a row that hasn't been inserted yet.
            existing["_pending_row"].update(update_values)
        else:
            update_payload.append({"id": existing["id"], **update_values})

        # Keep the fields process_coverage itself reads current, for any
        # further duplicate later in this same run.
        for tracked in ("cov_car_id", "cov_car_nam", "cov_sub_id", "effective_start_date", "effective_end_date"):
            existing[tracked] = update_values[tracked]

        return f"  ✓ Updated existing {coverage_type} coverage for patient {pat_header.source_id}: {company_name}"

    # ACTION_TERMINATE_AND_INSERT: a patient never has two active coverages
    # of the same type at once, so any carrier/subscriber change always
    # terminates the old row (regardless of whether the date ranges
    # happen to overlap) and inserts the new one as the sole active row.
    assert action == ACTION_TERMINATE_AND_INSERT
    normalized_new_start = first_day_of_month(new_start)
    existing_start = parse_date(existing["effective_start_date"])
    terminate_end = format_date(terminate_end_date(new_start, existing_start))

    if existing["id"] is None:
        existing["_pending_row"]["effective_end_date"] = terminate_end
        existing["_pending_row"]["active"] = False
    else:
        update_payload.append({
            "id": existing["id"],
            "effective_end_date": terminate_end,
            "active": False,
            "updated_at": now,
        })

    # The currently-active row is terminated either way (a patient never has
    # two active coverages of the same type at once) - but the replacement
    # doesn't have to be a brand-new row. If this exact coverage (same
    # carrier+subscriber+type) already exists inactive in this patient's
    # history - e.g. it was replaced once before and has now come back -
    # reactivate that old row instead of inserting yet another one that
    # looks identical to it.
    candidate = find_reactivation_candidate(
        inactive_candidates.get(map_key, []), payer, company_name, incoming["cov_sub_id"], coverage_type, active_payers,
    )
    if candidate is not None:
        _reactivate_candidate(
            candidate["id"], incoming, normalized_new_start, new_end, now, map_key, pat_header, coverage_type,
            update_payload, active_coverage_map,
        )
        return (
            f"  ✓ Reactivated {coverage_type} coverage for patient {pat_header.source_id}: {company_name} "
            f"(previous {coverage_type} coverage terminated)"
        )

    incoming["effective_start_date"] = format_date(normalized_new_start)
    incoming["effective_end_date"] = format_date(new_end)
    incoming["active"] = True
    insert_payload.append(incoming)
    active_coverage_map[map_key] = {  # same reason as the insert branch above
        "id": None,
        "patient_header_id": pat_header.patient_header_id,
        "cov_type": coverage_type,
        "cov_car_id": incoming["cov_car_id"],
        "cov_car_nam": incoming["cov_car_nam"],
        "cov_sub_id": incoming["cov_sub_id"],
        "effective_start_date": incoming["effective_start_date"],
        "effective_end_date": incoming["effective_end_date"],
        "_pending_row": incoming,
    }
    return (
        f"  ✓ Coverage changed for patient {pat_header.source_id}: old {coverage_type} coverage "
        f"terminated, new one starts {normalized_new_start}"
    )


def run_load_patient_coverages() -> int:
    if not os.path.exists(PATIENTS_JSON_PATH):
        raise FileNotFoundError(f"File not found: {PATIENTS_JSON_PATH}")

    print("⏳ Loading patient coverages data from Patients.json...")
    with open(PATIENTS_JSON_PATH, "r") as f:
        data = json.load(f)

    patients = data.get("PatientData", [])
    print(f"📄 Found {len(patients)} patients")
    print("⏳ Processing coverage records...")

    engine = get_engine()
    processed = 0
    skipped_unresolved = 0

    with Session(engine) as session:
        active_payers = fetch_active_payers(session)

        # Both queried once up front, not once per patient (same fix
        # load_patient_header.py already got): matched
        # case/whitespace-insensitively (lower(trim(source)) /
        # trim(source_id)) since source is free text and may carry stray
        # casing or padding from other loaders/legacy rows.
        print("⏳ Looking up existing patient headers and coverages...")
        header_map = build_patient_header_map(session)
        patient_header_ids = [header.patient_header_id for header in header_map.values()]
        active_coverage_map = build_active_coverage_map(session, patient_header_ids)
        inactive_candidates = build_inactive_coverage_map(session, patient_header_ids)

        for i in range(0, len(patients), BATCH_SIZE):
            batch = patients[i : i + BATCH_SIZE]

            # Accumulated across the whole batch, then each flushed as one
            # bulk executemany at the end of the batch instead of one
            # UPDATE/INSERT per coverage - the same fix load_patient_header.py
            # got: per-row session.add()/setattr() meant one DB round trip per
            # coverage, which dominates runtime once most patients already
            # have existing coverage rows to update.
            insert_payload: list = []
            update_payload: list = []

            for patient in batch:
                # ID is the Tebra patient id - it's matched against
                # patient_header.source_id, scoped to source = "tebra".
                source_id = (patient.get("ID") or "").strip()
                try:
                    pat_header = header_map.get(source_id)

                    if pat_header is None:
                        print(f"  ⚠️  No patient_header found for patient source_id {source_id}")
                        continue

                    if pat_header.client_id is None or pat_header.group_id is None:
                        # Same "never resolved" case load_patient_header.py already
                        # refuses to insert a new patient for - a coverage row built
                        # from this pat_header would just copy the same unresolved
                        # null client_id/group_id (build_coverage_record pulls both
                        # straight from pat_header), so skip it here too instead of
                        # loading coverages against an unresolved patient.
                        print(
                            f"  ⚠️  Skipping coverages for patient {source_id} - "
                            f"no resolved group/client"
                        )
                        skipped_unresolved += 1
                        continue

                    for coverage_type, prefix in COVERAGE_TYPES:
                        message = process_coverage(
                            patient, pat_header, coverage_type, prefix, active_payers, active_coverage_map,
                            inactive_candidates, insert_payload, update_payload,
                        )
                        if message:
                            print(message)
                            processed += 1

                except Exception as exc:
                    print(f"  ✗ Error processing coverages for patient {patient.get('ID')}: {exc!r}")

            # update_payload mixes two different column shapes - a full
            # ACTION_UPDATE row (every key in UPDATE_COLUMNS + updated_at) and
            # a terminate row (id/effective_end_date/active/updated_at only).
            # A single bulk UPDATE statement needs uniform keys across all its
            # rows (mismatched keys silently fall back to one UPDATE per row -
            # exactly what this is meant to avoid), so split by key shape
            # before executing.
            update_groups: dict[frozenset, list] = {}
            for row in update_payload:
                update_groups.setdefault(frozenset(row.keys()), []).append(row)
            for rows in update_groups.values():
                session.execute(update(PatientCoverage), rows)

            if insert_payload:
                session.execute(insert(PatientCoverage), insert_payload)

            session.commit()
            print(f"✓ Processed batch {i // BATCH_SIZE + 1} ({min(i + BATCH_SIZE, len(patients))}/{len(patients)} patients)")

    print("\n📊 Summary:")
    print(f"  ✅ Total coverage records: {processed}")
    print(f"  ⚠️  Skipped (no resolved group/client): {skipped_unresolved}")
    print(f"  📝 Batch size: {BATCH_SIZE}")

    return processed


if __name__ == "__main__":
    run_load_patient_coverages()
    print("✅ Patient coverages data loaded successfully!")
