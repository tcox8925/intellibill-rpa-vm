"""Patient registry matching and persistent manual mappings."""

import csv
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pf_sync_pkg.identity import (
    identity_score,
    name_similarity,
    normalize_person_name,
    normalize_phone,
    parse_guid_from_url,
)
from pf_sync_pkg.ingest import alias_value, index_registry_by_dob
from pf_sync_pkg.models import QueueRecord
from pf_sync_pkg.store import append_run, finish_run, load_store, save_store, store_rows
from pf_sync_pkg.tabular import read_tabular_rows
from pf_sync_pkg.utils import clean, normalize_header, now_iso, parse_date

PATIENT_ALIASES: Dict[str, Tuple[str, ...]] = {
    "patient_id": (
        "patient_id", "patient id", "record_number", "record number", "prn"
    ),
    "ehr_patient_guid": (
        "ehr_patient_guid", "patient_guid", "patient guid", "pf_patient_guid", "id"
    ),
    "ehr_patient_url": (
        "ehr_patient_url", "patient url", "profile_url", "summary_url"
    ),
    "patient_name": (
        "patient_name", "patient name", "full_name", "full name", "name"
    ),
    "first_name": ("first_name", "first name"),
    "last_name": ("last_name", "last name"),
    "dob": ("dob", "date of birth", "birth date"),
    "mobile_phone": ("mobile_phone", "mobile phone", "cell phone"),
    "home_phone": ("home_phone", "home phone"),
    "work_phone": ("work_phone", "work phone"),
    "phone": ("phone", "phone number", "preferred contact"),
    "patient_status": ("patient_status", "patient status", "status"),
}


def map_patient_registry_row(source: Dict[str, Any]) -> Dict[str, Any]:
    normalized = {normalize_header(k): clean(v) for k, v in source.items()}
    mapped = {
        target: alias_value(normalized, aliases)
        for target, aliases in PATIENT_ALIASES.items()
    }
    if not mapped["patient_name"]:
        mapped["patient_name"] = clean(f"{mapped['first_name']} {mapped['last_name']}")
    if not mapped["ehr_patient_guid"]:
        mapped["ehr_patient_guid"] = parse_guid_from_url(mapped["ehr_patient_url"])
    phones = {
        normalize_phone(mapped[key])
        for key in ("mobile_phone", "home_phone", "work_phone", "phone")
        if normalize_phone(mapped[key])
    }
    return {
        "patient_id": mapped["patient_id"],
        "ehr_patient_guid": mapped["ehr_patient_guid"],
        "patient_name": mapped["patient_name"],
        "normalized_name": normalize_person_name(mapped["patient_name"]),
        "dob": parse_date(mapped["dob"]).isoformat() if parse_date(mapped["dob"]) else "",
        "phones": sorted(phones),
        "patient_status": mapped["patient_status"],
    }


def is_inactive_patient(patient: Dict[str, Any]) -> bool:
    """Return True only for registry rows explicitly marked inactive.

    Blank or unfamiliar statuses remain eligible so older exports without a status
    column continue to work. Practice Fusion values such as ``Inactive`` and
    ``Inactive patient`` are excluded before DOB/name/phone scoring.
    """
    status = re.sub(r"[^a-z]+", " ", clean(patient.get("patient_status")).casefold()).strip()
    return status == "inactive" or status.startswith("inactive ")


def load_patient_registry(path: str) -> List[Dict[str, Any]]:
    """Load only patient-matching fields, streaming CSV rows when possible.

    Existing PF patient exports can contain very large raw JSON/note columns. Those
    columns are irrelevant to identity matching, so they are not retained in memory.
    """
    suffix = Path(path).suffix.lower()
    patients: List[Dict[str, Any]] = []

    if suffix not in {".json", ".xlsx", ".xlsm"}:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ValueError(f"Tabular file has no header row: {path}")
            for row in reader:
                patient = map_patient_registry_row(row)
                if (patient["patient_id"] or patient["ehr_patient_guid"]) and not is_inactive_patient(patient):
                    patients.append(patient)
        return patients

    for row in read_tabular_rows(path):
        patient = map_patient_registry_row(row)
        if (patient["patient_id"] or patient["ehr_patient_guid"]) and not is_inactive_patient(patient):
            patients.append(patient)
    return patients


def mapping_identity(record: QueueRecord) -> Dict[str, str]:
    return {
        "normalized_name": normalize_person_name(record.patient_name),
        "dob": parse_date(record.patient_dob).isoformat() if parse_date(record.patient_dob) else "",
        "phone": normalize_phone(record.patient_phone),
    }


def find_saved_mapping(record: QueueRecord, mappings: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    identity = mapping_identity(record)
    candidates: List[Tuple[float, Dict[str, Any]]] = []
    for mapping in mappings:
        mapping_dob = clean(mapping.get("dob"))
        if identity["dob"] and mapping_dob and identity["dob"] != mapping_dob:
            continue
        similarity = name_similarity(record.patient_name, clean(mapping.get("patient_name")))
        if similarity < 0.90:
            continue
        mapped_phone = normalize_phone(clean(mapping.get("phone")))
        if identity["phone"] and mapped_phone and identity["phone"] != mapped_phone:
            # A phone mismatch does not automatically invalidate a manually confirmed
            # name+DOB mapping, but it reduces its priority.
            similarity -= 0.10
        candidates.append((similarity, mapping))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score = candidates[0][0]
    best = [item[1] for item in candidates if abs(item[0] - best_score) < 0.001]
    distinct = {(item.get("patient_id"), item.get("ehr_patient_guid")) for item in best}
    return best[0] if len(distinct) == 1 else None


def add_or_update_mapping(
    mappings: List[Dict[str, Any]],
    record: QueueRecord,
    patient: Dict[str, Any],
    source: str,
) -> None:
    identity = mapping_identity(record)
    timestamp = now_iso()
    for mapping in mappings:
        if (
            clean(mapping.get("patient_id")) == clean(patient.get("patient_id"))
            and clean(mapping.get("normalized_name")) == identity["normalized_name"]
            and clean(mapping.get("dob")) == identity["dob"]
        ):
            mapping.update(
                {
                    "ehr_patient_guid": clean(patient.get("ehr_patient_guid")),
                    "patient_name": record.patient_name or clean(patient.get("patient_name")),
                    "normalized_name": identity["normalized_name"],
                    "dob": identity["dob"],
                    "phone": identity["phone"],
                    "source": source,
                    "updated_at": timestamp,
                }
            )
            return
    mappings.append(
        {
            "mapping_id": str(uuid.uuid4()),
            "patient_id": clean(patient.get("patient_id")),
            "ehr_patient_guid": clean(patient.get("ehr_patient_guid")),
            "patient_name": record.patient_name or clean(patient.get("patient_name")),
            "normalized_name": identity["normalized_name"],
            "dob": identity["dob"],
            "phone": identity["phone"],
            "source": source,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )


def apply_patient_match(
    record: QueueRecord,
    patient: Dict[str, Any],
    method: str,
    score: float,
) -> None:
    record.patient_id = clean(patient.get("patient_id")) or record.patient_id
    record.ehr_patient_guid = clean(patient.get("ehr_patient_guid")) or record.ehr_patient_guid
    record.patient_match_status = "matched"
    record.patient_match_method = method
    record.patient_match_score = round(float(score), 4)
    record.patient_match_message = ""
    record.patient_candidates = []
    record.message = ""
    if record.status == "needs_attention":
        record.status = "ready"
        record.status_reason = "patient_match_resolved"
    record.updated_at = now_iso()


def candidate_summary(patient: Dict[str, Any], score: float) -> Dict[str, Any]:
    return {
        "patient_id": patient.get("patient_id", ""),
        "ehr_patient_guid": patient.get("ehr_patient_guid", ""),
        "patient_name": patient.get("patient_name", ""),
        "dob": patient.get("dob", ""),
        "phones": patient.get("phones", []),
        "name_score": round(score, 4),
    }


def match_patients(
    queue_json: str,
    patients_file: str,
    fuzzy_threshold: float = 0.82,
    rematch_all: bool = False,
    dob_match_threshold: float = 0.85,
) -> Dict[str, int]:
    store = load_store(queue_json)
    rows = store_rows(store)
    registry = load_patient_registry(patients_file)
    dob_buckets = index_registry_by_dob(registry)
    mappings = store.setdefault("patient_mappings", [])
    counts = {
        "matched": 0,
        "reused_mapping": 0,
        "needs_attention": 0,
        "already_matched": 0,
        "ignored": 0,
    }

    for record in rows:
        if record.status == "ignored":
            counts["ignored"] += 1
            continue
        # v5.4: no longer requires patient_id. Records for the 631 registry patients
        # with no PRN never satisfied that condition and were re-scored from scratch on
        # every run.
        if (
            not rematch_all
            and record.patient_match_status == "matched"
            and record.ehr_patient_guid
        ):
            counts["already_matched"] += 1
            continue

        saved = find_saved_mapping(record, mappings)
        if saved:
            apply_patient_match(record, saved, "saved_mapping", 1.0)
            counts["reused_mapping"] += 1
            continue

        appointment_dob = (
            parse_date(record.patient_dob).isoformat() if parse_date(record.patient_dob) else ""
        )

        # v5.4: DOB first, then name.
        #
        # The previous order scored the appointment name against all 8288 registry rows
        # and only then filtered by DOB, so an exact DOB match could never rescue a name
        # that landed under fuzzy_threshold. Ten of twelve unresolved rows in the
        # 2026-07-25..30 window were in the registry with an exact DOB match, differing
        # only by a dropped middle name or second surname. Scoring inside the DOB bucket
        # lets a lower name threshold apply safely, because DOB has already done the
        # discriminating -- and it turns an 8288-row scan into a 1-3 row scan.
        if appointment_dob and appointment_dob in dob_buckets:
            bucket = dob_buckets[appointment_dob]
            scored = [
                (identity_score(record.patient_name, patient["patient_name"]), patient)
                for patient in bucket
            ]
            candidates = [
                (score, patient) for score, patient in scored if score >= dob_match_threshold
            ]
            candidates.sort(key=lambda item: item[0], reverse=True)
            name_candidates = sorted(scored, key=lambda item: item[0], reverse=True)
        else:
            # No usable DOB, or no chart carries it. Fall back to the whole registry at
            # the stricter name threshold.
            scored = [
                (identity_score(record.patient_name, patient["patient_name"]), patient)
                for patient in registry
            ]
            name_candidates = [
                (score, patient) for score, patient in scored if score >= fuzzy_threshold
            ]
            name_candidates.sort(key=lambda item: item[0], reverse=True)
            candidates = list(name_candidates)
        if len(candidates) > 1 and record.patient_phone_normalized:
            phone_candidates = [
                (score, patient)
                for score, patient in candidates
                if record.patient_phone_normalized in patient.get("phones", [])
            ]
            if phone_candidates:
                candidates = phone_candidates

        # Lowering the in-bucket threshold lets more than one chart clear the bar, so an
        # unambiguously better score should still resolve rather than escalate. Charts
        # that are genuinely indistinguishable (a duplicated chart with the same DOB and
        # the same phone) stay ambiguous and reach a human, which is the intent.
        if len(candidates) > 1:
            best_score = candidates[0][0]
            runner_up = candidates[1][0]
            leaders = [item for item in candidates if abs(item[0] - best_score) < 0.001]
            if len(leaders) == 1 and (best_score - runner_up) >= 0.08:
                candidates = leaders

        if len(candidates) == 1:
            score, patient = candidates[0]
            method = "fuzzy_name_dob_phone" if record.patient_phone_normalized else "fuzzy_name_dob"
            apply_patient_match(record, patient, method, score)
            add_or_update_mapping(mappings, record, patient, method)
            counts["matched"] += 1
            continue

        record.patient_match_status = "needs_attention"
        record.patient_match_method = ""
        record.patient_match_score = 0.0
        record.status = "needs_attention"
        record.status_reason = "patient_match_ambiguous" if candidates else "patient_match_not_found"
        if candidates:
            message = (
                f"More than one patient matched name/DOB/phone for {record.patient_name}. "
                "Assign the visible Practice Fusion patient ID manually."
            )
            display_candidates = candidates[:10]
        else:
            message = (
                f"No unique patient match was found for {record.patient_name} "
                f"DOB {record.patient_dob or '<missing>'}. Assign the patient manually."
            )
            display_candidates = name_candidates[:10]
        record.patient_match_message = message
        record.message = message
        record.patient_candidates = [
            candidate_summary(patient, score) for score, patient in display_candidates
        ]
        record.updated_at = now_iso()
        counts["needs_attention"] += 1

    run_id = append_run(
        store,
        "match-patients",
        {
            "patients_file": str(Path(patients_file).resolve()),
            "registry_rows": len(registry),
            "fuzzy_threshold": fuzzy_threshold,
        },
    )
    finish_run(store, run_id, "success", counts)
    save_store(queue_json, store, rows)
    return counts


def select_queue_rows(
    rows: Sequence[QueueRecord],
    row_id: str = "",
    appointment_id: str = "",
    patient_id: str = "",
    encounter_id: str = "",
) -> List[QueueRecord]:
    selected = []
    for record in rows:
        if row_id and record.row_id != row_id:
            continue
        if appointment_id and record.appointment_id != appointment_id:
            continue
        if patient_id and record.patient_id != patient_id:
            continue
        if encounter_id and encounter_id not in {record.encounter_id, record.encounter_key}:
            continue
        selected.append(record)
    return selected


def resolve_patient_manually(
    queue_json: str,
    patient_id: str,
    ehr_patient_guid: str,
    row_id: str = "",
    appointment_id: str = "",
    patients_file: str = "",
    resolved_patient_name: str = "",
) -> Dict[str, int]:
    store = load_store(queue_json)
    rows = store_rows(store)
    selected = select_queue_rows(rows, row_id=row_id, appointment_id=appointment_id)
    if not selected:
        raise ValueError("No queue row matched --row-id/--appointment-id.")

    patient: Dict[str, Any] = {
        "patient_id": patient_id,
        "ehr_patient_guid": ehr_patient_guid,
        "patient_name": resolved_patient_name,
    }
    if patients_file:
        registry = load_patient_registry(patients_file)
        matches: List[Dict[str, Any]] = []
        if patient_id:
            matches = [item for item in registry if item["patient_id"] == patient_id]
        # v5.4: allow lookup by GUID alone. 631 registry patients carry no PRN, so
        # requiring --patient-id made those charts impossible to resolve by either route.
        if len(matches) != 1 and ehr_patient_guid:
            guid_matches = [
                item for item in (matches or registry)
                if item["ehr_patient_guid"] == ehr_patient_guid
            ]
            if len(guid_matches) == 1:
                matches = guid_matches
        if len(matches) == 1:
            patient = matches[0]

    patient["patient_id"] = clean(patient.get("patient_id")) or patient_id
    patient["ehr_patient_guid"] = clean(patient.get("ehr_patient_guid")) or ehr_patient_guid
    if not patient["ehr_patient_guid"]:
        raise ValueError(
            "The patient GUID is required for chart navigation. Supply --ehr-patient-guid, "
            "or --patient-id together with --patients-file containing that patient ID."
        )
    if not patient["patient_id"]:
        # Not fatal: the GUID drives chart navigation and PDF naming falls back to it.
        print(
            "WARNING: resolving without a patient_id/PRN. The GUID is sufficient for "
            "chart navigation; generated PDFs will be named using the GUID.",
            flush=True,
        )

    mappings = store.setdefault("patient_mappings", [])
    primary = selected[0]
    add_or_update_mapping(mappings, primary, patient, "manual")

    # Apply the confirmed mapping to every appointment row representing the same
    # appointment identity, not only the one selected for manual resolution.
    identity = mapping_identity(primary)
    applied = 0
    for record in rows:
        record_identity = mapping_identity(record)
        # v5.4: a matching name alone is no longer enough to fan the resolution out.
        # When the resolved row had no DOB, the previous condition applied the mapping to
        # every same-name row regardless of DOB, which could attach one chart to a
        # different patient sharing a name (juniors, twins).
        same_name_dob = (
            record_identity["normalized_name"] == identity["normalized_name"]
            and bool(identity["dob"])
            and record_identity["dob"] == identity["dob"]
        )
        if record in selected or same_name_dob:
            apply_patient_match(record, patient, "manual", 1.0)
            applied += 1

    save_store(queue_json, store, rows)
    return {"resolved_rows": applied}
