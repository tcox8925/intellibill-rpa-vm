"""Loads coverage data from fhir_out/Coverage.json (Practice Fusion FHIR
Coverage resources) into "EDI_Tebra".patient_coverages, linked to
patient_header via patient_header_id. Patients are matched to their
patient_header row by beneficiary.reference ("Patient/<id>" -> source_id),
source = "practice_fusion".

cov_type is always "P" - the Coverage resource carries no primary/secondary
signal at all (no `order` field or equivalent; confirmed against the full
5,885-record feed), so there's nothing to derive it from yet.

Carrier resolution (utils/payer_lookup.py) and active/inactive coverage
state (update vs terminate+insert, utils/coverage_rules.py) are the exact
same machinery tebra/load_patient_coverages.py uses. That matters here
more than it does for Tebra: 618 patients in this feed have more than one
*genuinely distinct* Coverage record (real coverage history - e.g. BCBS
starting 2013, then Medicare starting 2022, both still present in the raw
export), on top of 316 coverage ids that are just exact duplicates of
another record. Duplicates are collapsed by id first (newest
meta.lastUpdated wins, same rule as load_patient_header.py), then each
patient's remaining distinct coverages are replayed through
decide_coverage_action in chronological order (earliest period.start
first) so the carrier-change history resolves the same way a real
sequence of incoming coverage updates would: older coverages get
terminated (marked inactive, never deleted) as newer ones come in,
leaving exactly one active "P" row per patient once history is applied.

insured_* fields depend on what kind of reference coverage.subscriber
actually is (see extract_subscriber):
  - "Patient/<id>" (true self-subscriber coverages, and also some
    spouse/other-coded rows where the source data still points subscriber
    back at the patient themselves - there's no way to tell those apart
    from a real self coverage) -> reuses patient_header's own already-
    parsed name/dob/gender/address, no extra lookup needed.
  - "RelatedPerson/<id>" (a genuinely different subscriber - spouse/
    dependent) -> looked up in fhir_out/RelatedPerson.json, deduped by id
    the same way Patient.json is (identical duplicate-id pattern - newest
    meta.lastUpdated wins). 5 of the 79 distinct RelatedPerson ids actually
    referenced by Coverage.subscriber in this feed aren't present in
    RelatedPerson.json at all, and none of the other 74 have a birthDate
    or gender - insured_dob/insured_gender stay null for every
    RelatedPerson subscriber today, not a mapping gap, just absent source
    data. insured_full_name is built First Middle Last using the same
    given/family word-splitting rule as load_patient_header.py's
    parse_name.
"""

import json
import os
import re
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.lookup_payers import LookupPayer
from models.patient_coverages import PatientCoverage
from models.patient_header import PatientHeader
from practice_fusion.load_patient_header import build_record, normalize_gender, parse_name
from practice_fusion.practice_fusion_full_export import fetch_patient_by_id
from utils.coverage_rules import (
    ACTION_TERMINATE_AND_INSERT,
    ACTION_UPDATE,
    decide_coverage_action,
    first_day_of_month,
    format_date,
    parse_date,
    terminate_end_date,
)
from utils.db import get_engine
from utils.payer_lookup import carrier_key, find_payer

BATCH_SIZE = 100
SOURCE = "practice_fusion"
COVERAGE_TYPE = "P"

COVERAGE_JSON_PATH = os.path.join(os.path.dirname(__file__), "fhir_out", "Coverage.json")
RELATED_PERSON_JSON_PATH = os.path.join(os.path.dirname(__file__), "fhir_out", "RelatedPerson.json")

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
REL_MAP = {"self": "S", "spouse": "U", "child": "C"}

# Columns copied wholesale onto an existing PatientCoverage row when
# decide_coverage_action says ACTION_UPDATE - same list tebra's loader uses.
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


def check_missing_patient_on_pf(source_id: str):
    """Live FHIR read for one coverage beneficiary id that has no
    patient_header row - every run, no caching across runs (a "gone
    yesterday" id could get restored/re-visible, and a "gone" verdict is
    cheap to re-confirm anyway).

    Returns (verdict, patient) where verdict is:
    - "exists" (200) - patient is real and current on Practice Fusion; the
      full FHIR Patient resource is returned right alongside so the caller
      can insert its patient_header row immediately instead of making the
      caller re-fetch it (or re-run the whole Patient pull + header load
      separately just to pick up this one record).
    - "gone" (404) - deleted/merged on Practice Fusion's side, nothing to
      insert, patient is None.
    - "error: <message>" (network/5xx/etc) - inconclusive, patient is None.
    """
    try:
        return "exists", fetch_patient_by_id(source_id)
    except RuntimeError as exc:
        if "HTTP 404" in str(exc):
            return "gone", None
        return f"error: {exc}", None


def carrier_type_from_payer_types(payer_type: list | None) -> str:
    """cov_car_type from lookup_payers.payer_type - same rule as Tebra's
    loader: exactly one type -> that type, anything else -> "Commercial"."""
    types = payer_type or []
    if len(types) == 1:
        return types[0]
    return "Commercial"


def default_effective_start() -> date:
    """A missing/unparseable effective_start_date defaults to Jan 1 of the
    current year, not today's date - same convention as Tebra's loader."""
    return date(date.today().year, 1, 1)


def normalize_relationship(coverage: dict) -> str:
    """self -> S, spouse -> U, child -> C, anything else (including
    missing) -> O."""
    code = ((coverage.get("relationship") or {}).get("coding") or [{}])[0].get("code")
    return REL_MAP.get((code or "").strip().lower(), "O")


def extract_group_number(coverage: dict):
    for cls in coverage.get("class") or []:
        code = ((cls.get("type") or {}).get("coding") or [{}])[0].get("code")
        if code == "group":
            value = cls.get("value")
            # Some payors put an internal UUID in this slot instead of a
            # real group number (e.g. the "plan" class entry's own id
            # sometimes gets echoed here) - skip those, same rule as
            # extract_cov_sub_id below.
            if value and not UUID_RE.match(value):
                return value
            return None
    return None


def extract_cov_sub_id(coverage: dict):
    """First identifier value that doesn't look like a UUID - checked
    against all 5,885 identifiers in this feed, none of them ever do, but
    the guard stays in case a future pull includes an internal-id-shaped
    identifier that isn't a real subscriber id."""
    for ident in coverage.get("identifier") or []:
        value = ident.get("value")
        if value and not UUID_RE.match(value):
            return value
    return None


def extract_patient_source_id(coverage: dict):
    ref = (coverage.get("beneficiary") or {}).get("reference") or ""
    prefix = "Patient/"
    return ref[len(prefix):] if ref.startswith(prefix) else None


def extract_subscriber(coverage: dict):
    """(kind, id) for coverage.subscriber.reference:
    - ("patient", <id>) when subscriber is a Patient reference - covers both
      true self-subscriber coverages AND the odd spouse/other-coded rows
      where the source data still points subscriber back at the patient
      themselves (checked against the full feed - there's no way to tell
      those apart from a true self coverage, so they're treated the same).
    - ("related_person", <id>) when subscriber is a RelatedPerson reference -
      a genuinely different person (spouse/dependent).
    - (None, None) if subscriber is missing or an unrecognized reference shape.
    """
    ref = (coverage.get("subscriber") or {}).get("reference") or ""
    for prefix, kind in (("Patient/", "patient"), ("RelatedPerson/", "related_person")):
        if ref.startswith(prefix):
            return kind, ref[len(prefix):]
    return None, None


def load_related_person_map() -> dict:
    """id -> RelatedPerson record, deduped the same way Patient.json is
    (RelatedPerson.json has the identical duplicate-id pattern - 641 of
    6,716 unique ids repeated in the full feed, all exact duplicates -
    newest meta.lastUpdated wins). Missing file just means no subscriber
    enrichment is available yet; callers treat an empty map the same as
    "id not found"."""
    if not os.path.exists(RELATED_PERSON_JSON_PATH):
        print(f"  ⚠️  {RELATED_PERSON_JSON_PATH} not found - "
              f"RelatedPerson subscribers will have no insured_* details")
        return {}

    with open(RELATED_PERSON_JSON_PATH, "r", encoding="utf-8") as f:
        related_people = json.load(f)

    best_last_updated = {}
    by_id = {}
    for person in related_people:
        pid = person.get("id")
        if not pid:
            continue
        last_updated = (person.get("meta") or {}).get("lastUpdated") or ""
        if pid not in by_id or last_updated >= best_last_updated[pid]:
            by_id[pid] = person
            best_last_updated[pid] = last_updated
    return by_id


def format_full_name(first, middle, last) -> str | None:
    parts = [p for p in (first, middle, last) if p]
    return " ".join(parts) if parts else None


def extract_address_fields(resource: dict):
    """address[0].line[0]/[1] -> addr1/addr2, plus city/state/postalCode/
    country - same convention as load_patient_header.py's Patient address
    handling, reused here for RelatedPerson's address[0]."""
    address = (resource.get("address") or [{}])[0]
    line = address.get("line") or []
    return {
        "address1": line[0] if len(line) >= 1 else None,
        "address2": line[1] if len(line) >= 2 else None,
        "city": address.get("city") or None,
        "state": address.get("state") or None,
        "zip": address.get("postalCode") or None,
        "country": address.get("country") or None,
    }


def build_insured_fields(
    coverage: dict, pat_header: PatientHeader, header_map: dict, related_person_map: dict
) -> dict:
    """insured_* fields, sourced from whichever kind of subscriber this
    coverage actually has - see extract_subscriber.

    header_map is the SAME source_id -> PatientHeader map used to resolve
    the beneficiary, keyed by every practice_fusion patient_header row -
    not just the beneficiary's own. In every coverage checked so far, a
    "Patient/<id>" subscriber's id is identical to the beneficiary's, but
    that's a fact about today's data, not a guarantee - so this always
    looks the subscriber's id up for real instead of assuming it equals
    pat_header. If the subscriber turns out to be a genuinely different
    patient who isn't (yet) in patient_header, insured_* stays null rather
    than silently substituting the beneficiary's own info.
    """
    empty = {
        "insured_full_name": None, "insured_dob": None, "insured_gender": None,
        "insured_address1": None, "insured_address2": None, "insured_city": None,
        "insured_state": None, "insured_zip": None, "insured_country": None,
    }

    kind, subscriber_id = extract_subscriber(coverage)

    if kind == "patient":
        subscriber_header = header_map.get((subscriber_id or "").strip())
        if subscriber_header is None:
            # The subscriber's own patient_header row isn't loaded (either
            # this practice_fusion patient hasn't been synced yet, or the
            # id doesn't resolve at all) - nothing to populate from.
            return empty
        return {
            "insured_full_name": format_full_name(
                subscriber_header.pat_fnam, subscriber_header.middle_name, subscriber_header.sub_lnam
            ),
            "insured_dob": subscriber_header.pat_dob or None,
            "insured_gender": subscriber_header.pat_gender or None,
            "insured_address1": subscriber_header.pat_addr1,
            "insured_address2": subscriber_header.pat_addr2,
            "insured_city": subscriber_header.pat_city,
            "insured_state": subscriber_header.pat_st,
            "insured_zip": subscriber_header.pat_zip,
            "insured_country": subscriber_header.country,
        }

    if kind == "related_person":
        related_person = related_person_map.get(subscriber_id)
        if related_person is None:
            # Not resolvable in this pull (5 of 79 referenced ids in the
            # actual feed aren't in RelatedPerson.json at all) - nothing to
            # populate from, same as a missing subscriber.
            return empty

        name = (related_person.get("name") or [{}])[0]
        first_name, last_name, middle_name = parse_name(name)
        addr = extract_address_fields(related_person)
        raw_gender = related_person.get("gender")
        return {
            "insured_full_name": format_full_name(first_name, middle_name, last_name),
            "insured_dob": related_person.get("birthDate") or None,
            # Normalized the same way pat_header.pat_gender is (F/M/O) when
            # present, so this column means the same thing regardless of
            # which subscriber branch populated it - but unlike pat_gender
            # (NOT NULL, always defaults to O), insured_gender is nullable,
            # so a genuinely missing value stays null rather than being
            # fabricated as "O" (RelatedPerson.gender is absent on every
            # one of the 74 subscribers resolvable in this feed today).
            "insured_gender": normalize_gender(raw_gender) if raw_gender else None,
            "insured_address1": addr["address1"],
            "insured_address2": addr["address2"],
            "insured_city": addr["city"],
            "insured_state": addr["state"],
            "insured_zip": addr["zip"],
            "insured_country": addr["country"],
        }

    return empty


def fetch_active_payers(session: Session) -> list[tuple]:
    """Same deterministic ordering rationale as tebra/load_patient_coverages.py's
    fetch_active_payers: lookup_payers has duplicate active rows sharing the
    same payer_name, so ordering by payer_id makes "first match wins"
    stable across runs instead of depending on undefined row order."""
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
    """trimmed source_id -> PatientHeader row, for source = "practice_fusion" -
    fetched once, same reasoning as tebra/load_patient_coverages.py's
    build_patient_header_map (avoids a per-coverage query that can't use a
    plain index)."""
    headers = session.execute(
        select(PatientHeader).where(func.lower(func.trim(PatientHeader.source)) == SOURCE)
    ).scalars().all()
    return {(header.source_id or "").strip(): header for header in headers}


def build_active_coverage_map(session: Session, patient_header_ids: list) -> dict:
    """(patient_header_id, cov_type) -> the single active PatientCoverage row.

    decide_coverage_action's active/inactive rule guarantees at most one
    active row per (patient_header_id, cov_type) as long as every write
    goes through this loader - but that's not something this function can
    enforce for rows that got there some other way (a manual insert, a bug
    in an older version, a different loader entirely). Unlike Tebra's
    identical-looking build_active_coverage_map (which just silently keeps
    the first row it sees - ordered by effective_start_date desc - and
    leaves any extra "active" rows exactly as they were), this one
    self-heals: the most recent row wins the map slot, same as before, but
    every OTHER active row found for that same key gets deactivated right
    here rather than left in a state where the DB itself claims two active
    coverages of the same type for one patient.
    """
    if not patient_header_ids:
        return {}
    rows = session.execute(
        select(PatientCoverage)
        .where(
            PatientCoverage.patient_header_id.in_(patient_header_ids),
            PatientCoverage.active.is_(True),
        )
        .order_by(PatientCoverage.effective_start_date.desc().nullslast())
    ).scalars().all()
    coverage_map = {}
    for row in rows:
        key = (row.patient_header_id, row.cov_type)
        if key in coverage_map:
            print(
                f"  ⚠️  Found more than one active coverage for patient_header_id="
                f"{row.patient_header_id} cov_type={row.cov_type!r} - deactivating "
                f"coverage id={row.id} (effective_start_date={row.effective_start_date}), "
                f"keeping id={coverage_map[key].id} (effective_start_date="
                f"{coverage_map[key].effective_start_date}) as the active one"
            )
            row.active = False
            row.updated_at = datetime.utcnow()
            continue
        coverage_map[key] = row
    return coverage_map


def build_coverage_record(
    coverage: dict, pat_header: PatientHeader, payer, header_map: dict, related_person_map: dict
) -> dict:
    payor_display = ((coverage.get("payor") or [{}])[0]).get("display") or None
    cov_rel = normalize_relationship(coverage)
    group_number = extract_group_number(coverage)
    cov_sub_id = extract_cov_sub_id(coverage)
    effective_start_date = (coverage.get("period") or {}).get("start") or None
    insured = build_insured_fields(coverage, pat_header, header_map, related_person_map)

    if payer is not None:
        # Matched: id/name/type come from lookup_payers, never the raw
        # Coverage.json payor display - same rule as Tebra's loader.
        carrier_id = payer.payer_id
        carrier_name = payer.payer_name
        carrier_type = carrier_type_from_payer_types(payer.payer_type)
    else:
        carrier_id = None
        carrier_name = payor_display
        carrier_type = "Commercial"

    return {
        "patient_header_id": pat_header.patient_header_id,
        "source": SOURCE,
        "client_id": pat_header.client_id,
        "group_id": pat_header.group_id,
        "practice_id": pat_header.practice_id,
        "pat_id": pat_header.pat_id,
        "pat_source": pat_header.source,
        "pat_sub_lnam": pat_header.sub_lnam,
        "pat_fnam": pat_header.pat_fnam,
        "pat_dob": pat_header.pat_dob,
        "cov_type": COVERAGE_TYPE,
        "insurance_type": None,
        "cov_status": coverage.get("status") or None,
        "cov_car_type": carrier_type,
        "cov_car_id": carrier_id,
        "cov_car_nam": carrier_name,
        "company_id": None,
        "company_name": payor_display,
        "policy_number": None,
        "group_number": group_number,
        "copay": None,
        "deductible": None,
        "effective_start_date": effective_start_date,
        "effective_end_date": None,
        "cov_rel": cov_rel,
        "patient_relationship_to_insured": cov_rel,
        "insured_full_name": insured["insured_full_name"],
        "insured_id_number": cov_sub_id,
        "insured_ssn": None,
        "insured_dob": insured["insured_dob"],
        "insured_gender": insured["insured_gender"],
        "insured_address1": insured["insured_address1"],
        "insured_address2": insured["insured_address2"],
        "insured_city": insured["insured_city"],
        "insured_state": insured["insured_state"],
        "insured_zip": insured["insured_zip"],
        "insured_country": insured["insured_country"],
        "insured_notes": None,
        "plan_id": None,
        "plan_name": None,
        "plan_address1": None,
        "plan_address2": None,
        "plan_city": None,
        "plan_state": None,
        "plan_zip": None,
        "plan_country": None,
        "plan_phone": None,
        "plan_phone_ext": None,
        "plan_fax": None,
        "plan_fax_ext": None,
        "plan_adjuster_name": None,
        "cov_sub_id": cov_sub_id,
        "cov_dep_id": None,
        "cov_dep_name": None,
        "cov_start_date": None,
        "cov_end_date": None,
    }


def process_coverage(
    session: Session,
    coverage: dict,
    pat_header: PatientHeader,
    active_payers: list,
    active_coverage_map: dict,
    header_map: dict,
    related_person_map: dict,
) -> str | None:
    payor_display = ((coverage.get("payor") or [{}])[0]).get("display") or None
    payer = find_payer(payor_display, "", active_payers)
    incoming = build_coverage_record(coverage, pat_header, payer, header_map, related_person_map)

    new_start = parse_date(incoming["effective_start_date"]) or default_effective_start()
    new_end = parse_date(incoming["effective_end_date"])

    map_key = (pat_header.patient_header_id, COVERAGE_TYPE)
    existing = active_coverage_map.get(map_key)

    if existing is None:
        incoming["effective_start_date"] = format_date(new_start)
        incoming["effective_end_date"] = format_date(new_end)
        new_coverage = PatientCoverage(**incoming)
        session.add(new_coverage)
        # Keep the in-memory map current so the NEXT coverage in this same
        # patient's chronological replay sees this one as the active row to
        # compare against, instead of re-querying per coverage.
        active_coverage_map[map_key] = new_coverage
        return f"  ✓ Inserted coverage for patient {pat_header.source_id}: {payor_display}"

    existing_payer = find_payer(existing.cov_car_nam, "", active_payers)
    existing_key = carrier_key(existing_payer, existing.cov_car_nam, stored_id=existing.cov_car_id or "")
    # Same stored_id fallback as existing_key - if find_payer fails to
    # resolve a payer on both sides this run (e.g. the payer got
    # deactivated in lookup_payers since it was last matched), both keys
    # fall back to the SAME already-known carrier id instead of existing
    # falling back to "id:<old id>" while incoming falls back to a bare
    # "name:<raw name>" key that can never equal it - which would flag a
    # resolution hiccup as a carrier change and wrongly terminate+insert a
    # coverage that hasn't actually changed.
    incoming_key = carrier_key(payer, payor_display or "", stored_id=existing.cov_car_id or "")

    action = decide_coverage_action(
        existing_carrier_key=existing_key,
        incoming_carrier_key=incoming_key,
        existing_subscriber_id=existing.cov_sub_id,
        incoming_subscriber_id=incoming["cov_sub_id"],
        existing_type=existing.cov_type or "",
        incoming_type=COVERAGE_TYPE,
    )

    if action == ACTION_UPDATE:
        for column in UPDATE_COLUMNS:
            if column in ("effective_start_date", "effective_end_date"):
                # incoming[column] is still the raw Coverage.json value here
                # (never defaulted/parsed like the insert/terminate branches
                # do) - source usually omits it entirely, so blindly copying
                # it over would null out the existing row's real start/end
                # date every time an unchanged coverage gets re-synced. Only
                # overwrite when the source actually reports a real date
                # this run; otherwise leave the stored one alone.
                parsed = parse_date(incoming[column])
                if parsed is None:
                    continue
                setattr(existing, column, format_date(parsed))
                continue
            setattr(existing, column, incoming[column])
        existing.updated_at = datetime.utcnow()
        return f"  ✓ Updated existing coverage for patient {pat_header.source_id}: {payor_display}"

    # ACTION_TERMINATE_AND_INSERT: a patient never has two active coverages
    # of the same type at once, so any carrier/subscriber change always
    # terminates the old row (regardless of whether the date ranges happen
    # to overlap) and inserts the new one as the sole active row.
    assert action == ACTION_TERMINATE_AND_INSERT
    normalized_new_start = first_day_of_month(new_start)
    existing_start = parse_date(existing.effective_start_date)
    existing.effective_end_date = format_date(terminate_end_date(new_start, existing_start))
    existing.active = False
    existing.updated_at = datetime.utcnow()

    incoming["effective_start_date"] = format_date(normalized_new_start)
    incoming["effective_end_date"] = format_date(new_end)
    new_coverage = PatientCoverage(**incoming)
    session.add(new_coverage)
    active_coverage_map[map_key] = new_coverage
    return (
        f"  ✓ Coverage changed for patient {pat_header.source_id}: old coverage terminated, "
        f"new one starts {normalized_new_start}"
    )


def run_load_patient_coverages() -> int:
    if not os.path.exists(COVERAGE_JSON_PATH):
        raise FileNotFoundError(f"File not found: {COVERAGE_JSON_PATH}")

    print("⏳ Loading coverage data from Coverage.json...")
    with open(COVERAGE_JSON_PATH, "r", encoding="utf-8") as f:
        coverages = json.load(f)
    print(f"📄 Found {len(coverages)} coverage record(s)")

    # Collapse exact duplicate coverage ids first (same rule as
    # load_patient_header.py: newest meta.lastUpdated wins, ties go to the
    # last occurrence in the file).
    best_last_updated = {}
    by_coverage_id = {}
    for coverage in coverages:
        cid = coverage.get("id")
        if not cid:
            continue
        last_updated = (coverage.get("meta") or {}).get("lastUpdated") or ""
        if cid not in by_coverage_id or last_updated >= best_last_updated[cid]:
            by_coverage_id[cid] = coverage
            best_last_updated[cid] = last_updated
    deduped = list(by_coverage_id.values())
    print(f"📄 {len(deduped)} unique coverage record(s) after de-duplicating by coverage id")

    # Group by patient, then sort each patient's coverages by period.start
    # ascending, so replaying them through decide_coverage_action resolves
    # carrier-change history in the right order (oldest -> terminated,
    # newest -> the sole active row) instead of order-of-appearance in the
    # raw export, which isn't guaranteed to be chronological.
    by_patient = {}
    skipped_no_patient_ref = 0
    for coverage in deduped:
        source_id = extract_patient_source_id(coverage)
        if not source_id:
            skipped_no_patient_ref += 1
            continue
        by_patient.setdefault(source_id, []).append(coverage)

    for source_id, patient_coverages in by_patient.items():
        patient_coverages.sort(
            key=lambda c: parse_date((c.get("period") or {}).get("start")) or default_effective_start()
        )

    if skipped_no_patient_ref:
        print(f"  ⚠️  Skipped {skipped_no_patient_ref} coverage(s) with no beneficiary Patient reference")

    related_person_map = load_related_person_map()
    print(f"📄 {len(related_person_map)} unique RelatedPerson record(s) available for subscriber enrichment")

    engine = get_engine()
    processed = 0

    with Session(engine) as session:
        active_payers = fetch_active_payers(session)

        print("⏳ Looking up existing patient headers and coverages...")
        header_map = build_patient_header_map(session)
        active_coverage_map = build_active_coverage_map(
            session, [header.patient_header_id for header in header_map.values()]
        )

        patient_ids = list(by_patient.keys())
        missing_exists_on_pf = []
        missing_gone_on_pf = []
        missing_check_errors = []
        for i in range(0, len(patient_ids), BATCH_SIZE):
            batch_patient_ids = patient_ids[i : i + BATCH_SIZE]

            for source_id in batch_patient_ids:
                pat_header = header_map.get(source_id)
                if pat_header is None:
                    verdict, patient = check_missing_patient_on_pf(source_id)
                    if verdict == "exists":
                        missing_exists_on_pf.append(source_id)
                        pat_header = PatientHeader(**build_record(patient))
                        session.add(pat_header)
                        session.flush()  # assigns patient_header_id, needed by process_coverage below
                        header_map[source_id] = pat_header
                        print(
                            f"  ✅ No patient_header found for practice_fusion source_id "
                            f"{source_id} - it still exists on Practice Fusion, inserted a "
                            f"patient_header row for it and loading its coverage(s) now"
                        )
                        # falls through to the coverage loop below - no separate
                        # re-run of the header loader needed for this one patient
                    elif verdict == "gone":
                        missing_gone_on_pf.append(source_id)
                        print(
                            f"  ⚠️  No patient_header found for practice_fusion source_id "
                            f"{source_id} - confirmed gone on Practice Fusion (404), skipping"
                        )
                        continue
                    else:
                        missing_check_errors.append((source_id, verdict))
                        print(
                            f"  ⚠️  No patient_header found for practice_fusion source_id "
                            f"{source_id} - live check failed ({verdict}), skipping"
                        )
                        continue

                for coverage in by_patient[source_id]:
                    try:
                        message = process_coverage(
                            session, coverage, pat_header, active_payers, active_coverage_map,
                            header_map, related_person_map,
                        )
                        if message:
                            print(message)
                            processed += 1
                    except Exception as exc:
                        print(f"  ✗ Error processing coverage {coverage.get('id')} for patient {source_id}: {exc!r}")

            session.commit()
            print(f"✓ Processed batch {i // BATCH_SIZE + 1} "
                  f"({min(i + BATCH_SIZE, len(patient_ids))}/{len(patient_ids)} patients)")

    total_missing = len(missing_exists_on_pf) + len(missing_gone_on_pf) + len(missing_check_errors)

    print("\n📊 Summary:")
    print(f"  ✅ Total coverage records processed: {processed}")
    if total_missing:
        print(f"  ⚠️  Coverage beneficiaries that had no patient_header row at the start of this run: {total_missing}")
        print(f"      -> existed on Practice Fusion - inserted patient_header + loaded coverage(s): {len(missing_exists_on_pf)}")
        print(f"      -> confirmed gone on Practice Fusion (404), skipped: {len(missing_gone_on_pf)}")
        if missing_check_errors:
            print(f"      -> live check inconclusive (network/API error), skipped: {len(missing_check_errors)}")

    # Always written, every run - overwritten in place (not appended), so it
    # always reflects only the most recent run's state, whether or not
    # anything was actually missing that time.
    report_path = os.path.join(os.path.dirname(__file__), "fhir_out", "coverage_missing_patients_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "run_at": datetime.utcnow().isoformat() + "Z",
                "total_missing": total_missing,
                "inserted_patient_header_and_loaded_coverage": missing_exists_on_pf,
                "confirmed_gone_on_practice_fusion": missing_gone_on_pf,
                "live_check_inconclusive": [
                    {"source_id": sid, "error": err} for sid, err in missing_check_errors
                ],
            },
            f,
            indent=2,
        )
    print(f"  📄 Missing-patient report written to {report_path}")
    print(f"  📝 Batch size: {BATCH_SIZE}")

    return processed


if __name__ == "__main__":
    run_load_patient_coverages()
    print("✅ Practice Fusion patient coverages data loaded successfully!")
