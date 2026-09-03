"""Python port of loadPatientHeader.ts.

Loads patient header data from responses/Patients.json, resolving each
patient's group/client via "EDI_Tebra"."group" (no claim_header /
claim_details references - client and group are the only lookups here).
"""

import json
import os
import re
from datetime import datetime

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from models.group import Group
from models.patient_header import PatientHeader
from tebra.paths import PATIENTS_JSON_PATH
from utils.db import get_engine

BATCH_SIZE = 100


def normalize_name(name: str) -> str:
    return re.sub(r"\s+", "", name.lower())


def build_group_map(session: Session) -> dict:
    """normalized group name -> {"group_id": ..., "client_id": ...}"""
    group_map = {}
    for group_id, client_id, grp_name in session.execute(
        select(Group.id, Group.client_id, Group.grp_name)
    ).all():
        if not grp_name:
            continue
        group_map[normalize_name(grp_name)] = {"group_id": group_id, "client_id": client_id}
    return group_map


def build_record(patient: dict, group_map: dict) -> dict:
    group_info = None
    practice_name = patient.get("PracticeName")
    if practice_name:
        group_info = group_map.get(normalize_name(practice_name))
        if not group_info:
            print(f"  ⚠️  No group found for patient group name: {practice_name}")

    return {
        "source": "tebra",
        "source_id": patient.get("ID") or None,
        "pat_id": "",
        "client_id": group_info["client_id"] if group_info else None,
        "group_id": group_info["group_id"] if group_info else None,
        # True only when PracticeName matched an "EDI_Tebra"."group" row -
        # used to skip inserting new patients with no resolvable group, and
        # to avoid nulling out an existing patient's client_id/group_id on
        # update when this run failed to resolve them.
        "_group_resolved": group_info is not None,
        "practice_id": None,
        "sub_lnam": patient.get("LastName") or None,
        "pat_fnam": patient.get("FirstName") or None,
        "middle_name": patient.get("MiddleName") or None,
        "prefix": patient.get("Prefix") or None,
        "suffix": patient.get("Suffix") or None,
        "pat_gender": patient.get("Gender") or None,
        "pat_dob": patient.get("DOB") or None,
        "active": patient.get("Active") == "True",
        "patient_full_name": patient.get("PatientFullName") or None,
        "age": patient.get("Age") or None,
        "ssn": patient.get("SSN") or None,
        "marital_status": patient.get("MaritalStatus") or None,
        "medical_record_number": patient.get("MedicalRecordNumber") or None,
        "pat_email": patient.get("EmailAddress") or None,
        "pat_contact": patient.get("WorkPhone") or patient.get("MobilePhone") or patient.get("HomePhone") or None,
        "work_phone": patient.get("WorkPhone") or None,
        "work_phone_ext": patient.get("WorkPhoneExt") or None,
        "mobile_phone": patient.get("MobilePhone") or None,
        "mobile_phone_ext": patient.get("MobilePhoneExt") or None,
        "home_phone": patient.get("HomePhone") or None,
        "home_phone_ext": patient.get("HomePhoneExt") or None,
        "pat_contact_consent": None,
        "pat_contact_method": None,
        "emergency_name": patient.get("EmergencyName") or None,
        "emergency_phone": patient.get("EmergencyPhone") or None,
        "emergency_phone_ext": patient.get("EmergencyPhoneExt") or None,
        "pat_addr1": patient.get("AddressLine1") or None,
        "pat_addr2": patient.get("AddressLine2") or None,
        "pat_city": patient.get("City") or None,
        "pat_st": patient.get("State") or None,
        "pat_zip": patient.get("ZipCode") or None,
        "country": patient.get("Country") or None,
        "employer_name": patient.get("EmployerName") or None,
        "employment_status": patient.get("EmploymentStatus") or None,
        "primary_care_physician_id": patient.get("PrimaryCarePhysicianId") or None,
        "primary_care_physician_full_name": patient.get("PrimaryCarePhysicianFullName") or None,
        "referring_provider_id": patient.get("ReferringProviderId") or None,
        "referring_provider_full_name": patient.get("ReferringProviderFullName") or None,
        "referral_source": patient.get("ReferralSource") or None,
        "collection_category_name": (patient.get("CollectionCategoryName") or "").strip() or None,
        "total_balance": float(patient["TotalBalance"]) if patient.get("TotalBalance") else None,
        "patient_balance": float(patient["PatientBalance"]) if patient.get("PatientBalance") else None,
        "insurance_balance": float(patient["InsuranceBalance"]) if patient.get("InsuranceBalance") else None,
        "alert_message": patient.get("AlertMessage") or None,
        "alert_show_patient_details": patient.get("AlertShowWhenDisplayingPatientDetails") == "True",
        "alert_show_encounters": patient.get("AlertShowWhenEnteringEncounters") == "True",
        "alert_show_payments": patient.get("AlertShowWhenPostingPayments") == "True",
        "alert_show_statements": patient.get("AlertShowWhenPreparingPatientStatements") == "True",
        "alert_show_appointments": patient.get("AlertShowWhenSchedulingAppointments") == "True",
        "alert_show_claims": patient.get("AlertShowWhenViewingClaimDetails") == "True",
        "last_appointment_date": patient.get("LastAppointmentDate") or None,
        "last_encounter_date": patient.get("LastEncounterDate") or None,
        "last_payment_date": patient.get("LastPaymentDate") or None,
        "last_statement_date": patient.get("LastStatementDate") or None,
        "default_case_id": patient.get("DefaultCaseID") or None,
        "default_case_name": patient.get("DefaultCaseName") or None,
        "default_case_description": patient.get("DefaultCaseDescription") or None,
        "default_service_location_id": patient.get("DefaultServiceLocationId") or None,
        "default_service_location_name": patient.get("DefaultServiceLocationName") or None,
        "default_rendering_provider_id": patient.get("DefaultRenderingProviderId") or None,
        "default_rendering_provider_name": patient.get("DefaultRenderingProviderFullName") or None,
    }


# Columns shared by both the UPDATE and INSERT path (identity columns -
# source/source_id/pat_id - and practice_id, which is always null, are
# handled separately below).
SHARED_COLUMNS = [
    "client_id", "group_id", "sub_lnam", "pat_fnam", "middle_name", "prefix", "suffix",
    "pat_gender", "pat_dob", "active", "patient_full_name", "age", "ssn", "marital_status",
    "medical_record_number", "pat_email", "pat_contact", "work_phone", "work_phone_ext",
    "mobile_phone", "mobile_phone_ext", "home_phone", "home_phone_ext", "pat_contact_consent",
    "pat_contact_method", "emergency_name", "emergency_phone", "emergency_phone_ext",
    "pat_addr1", "pat_addr2", "pat_city", "pat_st", "pat_zip", "country", "employer_name",
    "employment_status", "primary_care_physician_id", "primary_care_physician_full_name",
    "referring_provider_id", "referring_provider_full_name", "referral_source",
    "collection_category_name", "total_balance", "patient_balance", "insurance_balance",
    "alert_message", "alert_show_patient_details", "alert_show_encounters",
    "alert_show_payments", "alert_show_statements", "alert_show_appointments",
    "alert_show_claims", "last_appointment_date", "last_encounter_date", "last_payment_date",
    "last_statement_date", "default_case_id", "default_case_name", "default_case_description",
    "default_service_location_id", "default_service_location_name",
    "default_rendering_provider_id", "default_rendering_provider_name",
]


def run_load_patient_header() -> int:
    if not os.path.exists(PATIENTS_JSON_PATH):
        raise FileNotFoundError(f"File not found: {PATIENTS_JSON_PATH}")

    print("⏳ Loading patient header data from Patients.json...")
    with open(PATIENTS_JSON_PATH, "r") as f:
        data = json.load(f)

    patients = data.get("PatientData", [])
    print(f"📄 Found {len(patients)} patients")
    print("⏳ Looking up group and client information...")

    engine = get_engine()
    total_processed = 0
    total_skipped = 0

    with Session(engine) as session:
        group_map = build_group_map(session)
        bulk_data = [build_record(patient, group_map) for patient in patients]

        # Queried once up front, not per batch - a patient inserted in an
        # earlier batch of this same run won't be re-matched against later
        # batches, but neither did the original loadPatientHeader.ts (each
        # of its batches only ever upserts patients from its own batch).
        # Matched case/whitespace-insensitively (lower(trim(source))) since
        # source is free text and may carry stray casing or padding from
        # other loaders/legacy rows.
        #
        # Fetched as plain dicts (patient_header_id/client_id/group_id only -
        # the only fields the update loop below ever reads back), not mapped
        # PatientHeader objects, so the update loop never needs a per-row
        # session.get() round trip - on a populated dev DB, thousands of
        # individual get() calls (one per patient being updated) is exactly
        # the same per-row round-trip cost we already fixed in
        # load_patient_coverages.py.
        #
        # Plain dicts rather than ORM objects for another reason: the update
        # loop below writes via a bulk Core update() (see below), which
        # doesn't sync an already-loaded ORM instance's in-memory attributes -
        # if the *same* source_id appears twice in the file across two
        # different batches, an ORM object's getattr() would see this
        # batch's client_id/group_id go stale after an earlier batch's bulk
        # write. Refreshing this dict in place after each batch keeps a later
        # batch's fallback read correct instead.
        existing = {
            (source_id or "").strip(): {
                "patient_header_id": patient_header_id,
                "client_id": client_id,
                "group_id": group_id,
            }
            for patient_header_id, source_id, client_id, group_id in session.execute(
                select(
                    PatientHeader.patient_header_id,
                    PatientHeader.source_id,
                    PatientHeader.client_id,
                    PatientHeader.group_id,
                ).where(func.lower(func.trim(PatientHeader.source)) == "tebra")
            ).all()
        }

        print("⏳ Performing bulk insert...")

        for i in range(0, len(bulk_data), BATCH_SIZE):
            batch = bulk_data[i : i + BATCH_SIZE]

            to_update = []
            to_insert = []
            skipped = 0
            for record in batch:
                key = (record["source_id"] or "").strip()
                existing_header = existing.get(key)
                if existing_header:
                    to_update.append((existing_header, record))
                elif record["_group_resolved"]:
                    to_insert.append(record)
                else:
                    # New patient with no resolvable group/client - never
                    # insert with null client_id/group_id.
                    skipped += 1
                    print(f"  ⚠️  Skipping new patient {record['source_id']} - no resolved group/client")

            total_skipped += skipped
            print(f"   📝 To UPDATE: {len(to_update)}, To INSERT: {len(to_insert)}, Skipped: {skipped}")

            # Bulk UPDATE and bulk INSERT (one executemany round trip each,
            # instead of one UPDATE/INSERT per patient) - ORM per-object
            # setattr()+commit was the slow part: SQLAlchemy flushes each
            # dirty object as its own UPDATE statement, so a batch of
            # existing patients meant one DB round trip per patient. Passing
            # a list of dicts to session.execute(update(Model), ...) /
            # insert(Model), ...) instead lets SQLAlchemy send the whole
            # batch as a single multi-row statement.
            now = datetime.utcnow()

            if to_update:
                update_payload = []
                for header, record in to_update:
                    row = {"patient_header_id": header["patient_header_id"]}
                    for column in SHARED_COLUMNS:
                        # Don't null out a previously-resolved client_id/group_id
                        # just because this run failed to match a group - write
                        # back the header's current value instead of omitting
                        # the column (every row in this bulk UPDATE must set the
                        # same columns).
                        if column in ("client_id", "group_id") and not record["_group_resolved"]:
                            row[column] = header[column]
                        else:
                            row[column] = record[column]
                    row["updated_at"] = now
                    row["loaded_at"] = now
                    update_payload.append(row)
                    # Refresh the shared `existing` entry in place - if this
                    # same source_id turns up again in a later batch (a
                    # duplicate row in the source file), its fallback read
                    # above must see this batch's resolved client_id/group_id,
                    # not the pre-update value the bulk UPDATE below never
                    # writes back onto this dict itself.
                    header["client_id"] = row["client_id"]
                    header["group_id"] = row["group_id"]
                session.execute(update(PatientHeader), update_payload)
                print(f"   ✅ Updated {len(to_update)} patient records")

            if to_insert:
                insert_payload = []
                for record in to_insert:
                    values = {column: record[column] for column in SHARED_COLUMNS}
                    values["source"] = record["source"]
                    values["source_id"] = record["source_id"]
                    values["pat_id"] = record["pat_id"]
                    values["practice_id"] = None
                    values["loaded_at"] = now
                    insert_payload.append(values)
                session.execute(insert(PatientHeader), insert_payload)

            session.commit()

            total_processed += len(batch)
            print(f"✓ Processed {total_processed}/{len(bulk_data)} patient headers")

    print("\n📊 Summary:")
    print(f"  ✅ Total processed: {len(patients)}")
    print(f"  ⚠️  Skipped (no resolved group/client): {total_skipped}")
    print(f"  📝 Batch size: {BATCH_SIZE}")

    return len(patients)


if __name__ == "__main__":
    run_load_patient_header()
    print("✅ Patient header data loaded successfully!")
