"""
Tebra-only patient sync API.

Same Tebra endpoints as app.py's combined (Tebra + Practice Fusion) app, but
isolated so Tebra sync can run/restart independently of Practice Fusion.

Standalone: `python -m uvicorn app_tebra:app --reload --port 8010`, docs at
http://localhost:8010/docs.

Production: mounted at /patient-sync by the repo-root server.py (the process
myops.service actually runs on port 8010 alongside the Tebra RPA and
Practice Fusion sync APIs) -- there its docs resolve at
http://<host>:8010/patient-sync/docs, same pattern as pf-sync's
/pf-sync/docs.
"""
from fastapi import FastAPI

from tebra.load_patient_coverages import run_load_patient_coverages
from tebra.load_patient_header import run_load_patient_header
from tebra.tebra_api import pull_patient_demographics

app = FastAPI(title="Tebra Patient Sync")


@app.post("/tebra/pull-demographics")
def trigger_pull():
    patients = pull_patient_demographics()
    patient_records = patients.get("PatientData") or []
    return {"patients_pulled": len(patient_records)}


@app.post("/tebra/sync")
def trigger_sync():
    """Pulls demographics, then loads patient_header, then patient_coverages -
    the full end-to-end Tebra sync."""
    patients = pull_patient_demographics()
    patient_records = patients.get("PatientData") or []

    headers_processed = run_load_patient_header()
    coverages_processed = run_load_patient_coverages()

    return {
        "patients_pulled": len(patient_records),
        "headers_processed": headers_processed,
        "coverage_records_processed": coverages_processed,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8010)
