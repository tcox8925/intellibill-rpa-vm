from fastapi import FastAPI, HTTPException

from tebra.load_patient_coverages import run_load_patient_coverages
from tebra.load_patient_header import run_load_patient_header
from tebra.tebra_api import pull_patient_demographics
from practice_fusion.practice_fusion_full_export import fetch_patient_by_id, run as run_practice_fusion_full_export
from practice_fusion.load_patient_header import (
    run_load_patient_header as run_load_practice_fusion_patient_header,
)
from practice_fusion.load_patient_coverages import (
    run_load_patient_coverages as run_load_practice_fusion_patient_coverages,
)

app = FastAPI()


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


@app.post("/practice-fusion/load-patient-header")
def trigger_load_practice_fusion_patient_header():
    """Loads practice_fusion/fhir_out/Patient.json into patient_header -
    assumes fetch-resource/Patient has already pulled a current snapshot."""
    headers_processed = run_load_practice_fusion_patient_header()
    return {"headers_processed": headers_processed}


@app.get("/practice-fusion/patient/{patient_id}")
def trigger_get_practice_fusion_patient(patient_id: str):
    """FHIR read for a single Practice Fusion patient by id (the same id
    that shows up as source_id in patient_header/patient_coverages) - hits
    {PF_BASE_URL}/Patient/{patient_id} directly, no bundle/paging involved.
    404s from Practice Fusion (unknown/invisible id) come back as a 404 here."""
    try:
        return fetch_patient_by_id(patient_id)
    except RuntimeError as exc:
        message = str(exc)
        if "HTTP 404" in message:
            raise HTTPException(status_code=404, detail=message) from exc
        raise HTTPException(status_code=502, detail=message) from exc


@app.post("/practice-fusion/load-patient-coverages")
def trigger_load_practice_fusion_patient_coverages():
    """Loads practice_fusion/fhir_out/Coverage.json (+ RelatedPerson.json for
    subscriber enrichment) into patient_coverages - assumes patient_header
    has already been loaded for this source (source_id lookups depend on
    it) and fetch-resource/Coverage has pulled a current snapshot."""
    coverages_processed = run_load_practice_fusion_patient_coverages()
    return {"coverage_records_processed": coverages_processed}


@app.post("/practice-fusion/sync")
def trigger_practice_fusion_sync():
    """Full end-to-end Practice Fusion sync, same shape as /tebra/sync:
    pulls a fresh Patient/Coverage/RelatedPerson export, then loads
    patient_header, then patient_coverages."""
    export_status = run_practice_fusion_full_export()
    if export_status != 0:
        raise HTTPException(
            status_code=502,
            detail=(
                "Practice Fusion export finished with errors - see "
                "practice_fusion/fhir_out/_run_log.txt. Aborting before loading "
                "patient_header/patient_coverages against a possibly-stale/partial export."
            ),
        )

    headers_processed = run_load_practice_fusion_patient_header()
    coverages_processed = run_load_practice_fusion_patient_coverages()

    return {
        "export_status": "success",
        "headers_processed": headers_processed,
        "coverage_records_processed": coverages_processed,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
