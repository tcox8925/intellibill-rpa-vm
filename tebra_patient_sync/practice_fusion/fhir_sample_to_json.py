#!/usr/bin/env python3
"""
fhir_sample_to_json.py — sample every supported Practice Fusion FHIR resource
and write ONE JSON file: one array of records per resource (+ a summary block).

Read-only: the Practice Fusion FHIR API only supports read/search, and this
script only issues GETs. It reuses fhir_token_minter.get_access_token() for auth,
so run it from the same folder (its .env supplies PF_CLIENT_ID / PF_BASE_URL) and
with an Azure login that can sign with the `fhir-token` key (az login, or the
PF_AUTH_MODE=client_secret app-reg path).

Per resource it grabs up to ROWS_PER_RESOURCE records. For patient-scoped
resources it samples across up to SAMPLE_PATIENTS patients until it has enough,
so the output shows real populated data rather than empty results.

Usage:
    pip install requests
    python fhir_sample_to_json.py
Output: pf_fhir_sample.json in the current folder.
"""

import json
import os
import sys
import time

import requests

import fhir_token_minter as mint  # reuse the working auth

BASE_URL = os.environ.get("PF_BASE_URL") or mint.BASE_URL
ROWS_PER_RESOURCE = int(os.getenv("PF_ROWS", "10"))
SAMPLE_PATIENTS = int(os.getenv("PF_SAMPLE_PATIENTS", "10"))
OUT_PATH = os.getenv("PF_OUT", "pf_fhir_sample.json")
TIMEOUT = 60

# Resources the server advertises in its CapabilityStatement, split by whether a
# bare search works (non-patient) vs. needing a patient context (patient-scoped).
PATIENT_SCOPED = [
    "AllergyIntolerance", "CarePlan", "CareTeam", "Condition", "Coverage",
    "Device", "DiagnosticReport", "DocumentReference", "Encounter",
    "EpisodeOfCare", "ExplanationOfBenefit", "FamilyMemberHistory", "Flag",
    "Goal", "Immunization", "List", "MedicationAdministration",
    "MedicationDispense", "MedicationRequest", "Observation", "Procedure",
    "Provenance", "QuestionnaireResponse", "RelatedPerson", "ServiceRequest",
    "Specimen",
]
NON_PATIENT = [
    "Organization", "Location", "Practitioner", "PractitionerRole",
    "HealthcareService", "InsurancePlan", "Medication", "Substance",
    "OrganizationAffiliation", "Group", "Questionnaire", "Task",
    "MessageHeader", "Composition", "Contract", "ImagingStudy", "AuditEvent",
]

_session = requests.Session()


def _auth_header(force=False):
    return {"Authorization": f"Bearer {mint.get_access_token(force=force)}",
            "Accept": "application/json"}


def _get(url, params=None):
    """GET with one automatic token refresh on 401."""
    r = _session.get(url, params=params, headers=_auth_header(), timeout=TIMEOUT)
    if r.status_code == 401:
        r = _session.get(url, params=params, headers=_auth_header(force=True),
                         timeout=TIMEOUT)
    return r


def _entries(bundle_json):
    return [e.get("resource", e) for e in (bundle_json.get("entry") or [])]


def fetch_patient_ids(n):
    r = _get(f"{BASE_URL}/Patient", {"_count": n})
    r.raise_for_status()
    ids = []
    for res in _entries(r.json()):
        if res.get("id"):
            ids.append(res["id"])
    return ids


def sample_resource(rtype, patient_ids):
    """Return (records, status_note). Tries bare search, then patient-scoped."""
    # Non-patient resources: single bare search.
    if rtype in NON_PATIENT:
        r = _get(f"{BASE_URL}/{rtype}", {"_count": ROWS_PER_RESOURCE})
        if r.status_code != 200:
            return [], f"HTTP {r.status_code}: {r.text[:160]}"
        recs = _entries(r.json())[:ROWS_PER_RESOURCE]
        return recs, f"HTTP 200, {len(recs)} record(s)"

    # Patient-scoped: try bare first (system apps often allow it)...
    recs = []
    bare = _get(f"{BASE_URL}/{rtype}", {"_count": ROWS_PER_RESOURCE})
    note = ""
    if bare.status_code == 200:
        recs = _entries(bare.json())
        note = "bare search"
    # ...then top up by sampling patients until we have enough.
    if len(recs) < ROWS_PER_RESOURCE:
        for pid in patient_ids:
            if len(recs) >= ROWS_PER_RESOURCE:
                break
            pr = _get(f"{BASE_URL}/{rtype}",
                      {"patient": pid, "_count": ROWS_PER_RESOURCE})
            if pr.status_code == 200:
                recs.extend(_entries(pr.json()))
                note = "patient-scoped"
            elif not recs:
                note = f"HTTP {pr.status_code}: {pr.text[:120]}"
    # de-dup by id, cap
    seen, deduped = set(), []
    for res in recs:
        rid = res.get("id")
        if rid in seen:
            continue
        seen.add(rid)
        deduped.append(res)
    deduped = deduped[:ROWS_PER_RESOURCE]
    return deduped, f"{len(deduped)} record(s) ({note})" if deduped else \
        (note or "0 records")


def main():
    print(f"Base URL: {BASE_URL}")
    print("Fetching sample patient ids...")
    patient_ids = fetch_patient_ids(SAMPLE_PATIENTS)
    print(f"  got {len(patient_ids)} patient id(s)")

    summary = []
    resources = {}  # resource_type -> list of raw FHIR records

    # Patient first, from the ids we already pulled.
    pr = _get(f"{BASE_URL}/Patient", {"_count": ROWS_PER_RESOURCE})
    patient_recs = _entries(pr.json()) if pr.status_code == 200 else []
    resources["Patient"] = patient_recs
    summary.append({"resource": "Patient", "http_status": pr.status_code,
                     "record_count": len(patient_recs),
                     "note": f"{len(patient_recs)} record(s)"})

    all_types = PATIENT_SCOPED + NON_PATIENT
    for rtype in all_types:
        print(f"  {rtype} ...", end=" ", flush=True)
        try:
            recs, note = sample_resource(rtype, patient_ids)
        except Exception as e:  # never let one resource kill the run
            recs, note = [], f"ERROR: {e}"
        print(note)
        resources[rtype] = recs
        summary.append({"resource": rtype, "http_status": None,
                         "record_count": len(recs), "note": note})
        time.sleep(0.1)  # be gentle

    summary.sort(key=lambda row: row["resource"])

    output = {
        "base_url": BASE_URL,
        "rows_per_resource": ROWS_PER_RESOURCE,
        "summary": summary,
        "resources": resources,
    }

    print(f"\nWriting {OUT_PATH} ...")
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    populated = sum(1 for row in summary if row["record_count"])
    print(f"Done. {populated} of {len(summary)} resource types returned data.")
    print(f"JSON file: {os.path.abspath(OUT_PATH)}")


if __name__ == "__main__":
    sys.exit(main())
