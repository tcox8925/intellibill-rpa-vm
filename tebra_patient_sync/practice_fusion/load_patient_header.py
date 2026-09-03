"""Loads patient header data from fhir_out/Patient.json (Practice Fusion FHIR
Patient resources) into "EDI_Tebra".patient_header.

Mirrors tebra/load_patient_header.py's upsert shape, but client_id/group_id
are fixed constants for this source (Practice Fusion isn't matched to a
group by name the way Tebra's PracticeName is) rather than looked up.
"""

import json
import os
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.patient_header import PatientHeader
from utils.db import get_engine

BATCH_SIZE = 100
SOURCE = "practice_fusion"
CLIENT_ID = 9
GROUP_ID = 12

PATIENT_JSON_PATH = os.path.join(os.path.dirname(__file__), "fhir_out", "Patient.json")

GENDER_MAP = {"female": "F", "male": "M", "other": "O", "unknown": "O", "others": "O"}


def normalize_gender(raw_gender):
    """female -> F, male -> M, everything else (unknown/other/missing) -> O."""
    return GENDER_MAP.get((raw_gender or "").strip().lower(), "O")


def parse_name(name: dict):
    """first name = first word out of `given`; last name = last word out of
    `family`; every other word (extra given words, extra leading family
    words) goes to middle name.

    `given` can be several array elements ("given": ["Pedro", "Antonio"]) or
    a single element containing multiple words ("given": ["Mary Jane"]), and
    `family` can likewise be more than one word ("family": "Estrella
    Santiago") - both get split on whitespace and treated the same way.

    Examples:
      given=["Luz"], family="Bairez"
        -> first="Luz", last="Bairez", middle=None
      given=["Pedro", "Antonio"], family="Castillo"
        -> first="Pedro", last="Castillo", middle="Antonio"
      given=["Mishelle"], family="Estrella Santiago"
        -> first="Mishelle", last="Santiago", middle="Estrella"
      given=["Mary Jane", "Ann"], family="Rafael Fernandez"
        -> first="Mary", last="Fernandez", middle="Jane Ann Rafael"
    """
    given_words = [w for g in (name.get("given") or []) for w in g.split()]
    family_words = (name.get("family") or "").split()

    first_name = given_words[0] if given_words else None
    last_name = family_words[-1] if family_words else None
    middle_words = given_words[1:] + family_words[:-1]
    middle_name = " ".join(middle_words) if middle_words else None
    return first_name, last_name, middle_name


def parse_telecom(telecom: list):
    """First value wins per bucket - a patient normally has at most one
    phone number per use and one email, but if a feed ever repeats one,
    later entries are ignored rather than clobbering the first."""
    result = {"pat_email": None, "mobile_phone": None,
              "home_phone": None, "work_phone": None}
    for t in telecom or []:
        value = t.get("value")
        if not value:
            continue
        system, use = t.get("system"), t.get("use")
        if system == "email" and result["pat_email"] is None:
            result["pat_email"] = value
        elif system == "phone" and use == "mobile" and result["mobile_phone"] is None:
            result["mobile_phone"] = value
        elif system == "phone" and use == "home" and result["home_phone"] is None:
            result["home_phone"] = value
        elif system == "phone" and use == "work" and result["work_phone"] is None:
            result["work_phone"] = value
    return result


def build_record(patient: dict) -> dict:
    name = (patient.get("name") or [{}])[0]
    first_name, last_name, middle_name = parse_name(name)
    telecom = parse_telecom(patient.get("telecom"))

    # address[0] is always one of the real, populated copies in this feed
    # (later entries are just the same address duplicated in different
    # casing/line-splitting, or empty stubs) - see fhir_out/Patient.json.
    address = (patient.get("address") or [{}])[0]
    line = address.get("line") or []

    return {
        "source": SOURCE,
        "source_id": patient.get("id") or None,
        "pat_id": "",
        "client_id": CLIENT_ID,
        "group_id": GROUP_ID,
        "practice_id": None,
        "sub_lnam": last_name,
        "pat_fnam": first_name,
        "middle_name": middle_name,
        "pat_gender": normalize_gender(patient.get("gender")),
        "pat_dob": patient.get("birthDate") or "",
        "pat_addr1": line[0] if len(line) >= 1 else None,
        "pat_addr2": line[1] if len(line) >= 2 else None,
        "pat_city": address.get("city") or None,
        "pat_st": address.get("state") or None,
        "pat_zip": address.get("postalCode") or None,
        "country": address.get("country") or None,
        "pat_email": telecom["pat_email"],
        "mobile_phone": telecom["mobile_phone"],
        "home_phone": telecom["home_phone"],
        "work_phone": telecom["work_phone"],
        "pat_contact": telecom["mobile_phone"] or telecom["home_phone"] or telecom["work_phone"],
    }


# Columns shared by both the UPDATE and INSERT path.
SHARED_COLUMNS = [
    "client_id", "group_id", "sub_lnam", "pat_fnam", "middle_name",
    "pat_gender", "pat_dob", "pat_addr1", "pat_addr2", "pat_city", "pat_st",
    "pat_zip", "country", "pat_email", "mobile_phone", "home_phone",
    "work_phone", "pat_contact",
]


def run_load_patient_header() -> int:
    if not os.path.exists(PATIENT_JSON_PATH):
        raise FileNotFoundError(f"File not found: {PATIENT_JSON_PATH}")

    print("⏳ Loading patient header data from Patient.json...")
    with open(PATIENT_JSON_PATH, "r", encoding="utf-8") as f:
        patients = json.load(f)
    print(f"📄 Found {len(patients)} patient record(s)")

    # The feed itself contains duplicate patient records (same id appearing
    # more than once - Practice Fusion re-emits a patient whenever its
    # address/demographics changed since the previous pull). Collapse to one
    # record per source_id before touching the DB, keeping whichever
    # occurrence has the newest meta.lastUpdated (not just file order), so a
    # single run never tries to insert the same source_id twice and always
    # loads the most current snapshot of a duplicated patient.
    best_last_updated = {}
    by_source_id = {}
    for patient in patients:
        record = build_record(patient)
        key = (record["source_id"] or "").strip()
        if not key:
            print(f"  ⚠️  Skipping patient with no id: {patient.get('id')!r}")
            continue
        last_updated = (patient.get("meta") or {}).get("lastUpdated") or ""
        if key not in by_source_id or last_updated >= best_last_updated[key]:
            by_source_id[key] = record
            best_last_updated[key] = last_updated
    bulk_data = list(by_source_id.values())
    print(f"📄 {len(bulk_data)} unique patient(s) after de-duplicating by source_id (newest meta.lastUpdated wins)")

    engine = get_engine()
    total_processed = 0

    with Session(engine) as session:
        # Matched case/whitespace-insensitively, same as tebra/load_patient_header.py -
        # source_id.trim() and lower(trim(source)) == 'practice_fusion'.
        existing = {
            (header.source_id or "").strip(): header
            for header in session.execute(
                select(PatientHeader).where(
                    func.lower(func.trim(PatientHeader.source)) == SOURCE
                )
            ).scalars()
        }
        print(f"⏳ {len(existing)} existing '{SOURCE}' patient_header row(s) found")

        print("⏳ Performing bulk upsert...")
        for i in range(0, len(bulk_data), BATCH_SIZE):
            batch = bulk_data[i : i + BATCH_SIZE]

            to_update = []
            to_insert = []
            for record in batch:
                key = record["source_id"].strip()
                existing_header = existing.get(key)
                if existing_header:
                    to_update.append((existing_header, record))
                else:
                    to_insert.append(record)

            print(f"   📝 To UPDATE: {len(to_update)}, To INSERT: {len(to_insert)}")

            for header, record in to_update:
                for column in SHARED_COLUMNS:
                    setattr(header, column, record[column])
                header.updated_at = datetime.utcnow()
                header.loaded_at = datetime.utcnow()

            for record in to_insert:
                values = {column: record[column] for column in SHARED_COLUMNS}
                values["source"] = record["source"]
                values["source_id"] = record["source_id"]
                values["pat_id"] = record["pat_id"]
                values["practice_id"] = record["practice_id"]
                values["loaded_at"] = datetime.utcnow()
                new_header = PatientHeader(**values)
                session.add(new_header)
                # Newly inserted rows must also be visible to later batches
                # in this same run, in case a duplicate source_id somehow
                # slipped through de-duplication.
                existing[record["source_id"]] = new_header

            session.commit()

            total_processed += len(batch)
            print(f"✓ Processed {total_processed}/{len(bulk_data)} patient headers")

    print("\n📊 Summary:")
    print(f"  ✅ Total unique patients processed: {len(bulk_data)}")
    print(f"  📝 Batch size: {BATCH_SIZE}")

    return len(bulk_data)


if __name__ == "__main__":
    run_load_patient_header()
    print("✅ Practice Fusion patient header data loaded successfully!")
