from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from typing import Optional, List, Dict, Any, Union
from fastapi.responses import JSONResponse
from run_npi_scrape import run_npi_scrape
from utils import log_utils  # logs go to raw.ops_pch_logs
from caqh_lookup import validate_caqh_id, run_caqh_lookup
from utils import upload_utils  # ✅ added for CAQH upload
from utils.db_utils import set_db_source  # ✅ per-job Postgres target ("myops" | "rcm")
from NIPR.nipr_crawler import run_scraper
from NIPR.nipr_crawler_parser import run_full_nipr_and_update
from NIPR.ai_nipr_scraper import run_ai_nipr_scrape
from Service_Interruption.service_interruption_state_runner import run_service_interruption_engine
from ops_nipr import run_ops_nipr
import traceback

# --- Thread lanes ---
SINGLE_EXECUTOR = ThreadPoolExecutor(max_workers=1)
BULK_EXECUTOR = ThreadPoolExecutor(max_workers=5)
NIPR_EXECUTOR = ThreadPoolExecutor(max_workers=3)

app = FastAPI()


# --- Models ---
class ScrapeRequest(BaseModel):
    npi: str
    txn_id: str
    module: str = "ALL"
    dryrun: bool = False
    company_id: Optional[str] = None
    carrier_id: Optional[str] = None
    file_path: Optional[str] = None
    process_type: Optional[str] = None
    caqh_id: Optional[str] = None
    source: Optional[str] = "myops"  # ✅ Postgres write target: "myops" (default) | "rcm"


# --- Worker ---
def run_nipr_background(npn: str):
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"[NIPR] Attempt {attempt}/{max_attempts} for NPN {npn}")
            result = run_scraper(npn)
            print(f"[NIPR] Completed for {npn}: {result}")
            return
        except Exception as e:
            print(f"[NIPR ERROR] Attempt {attempt} for {npn}: {e}")
            if attempt < max_attempts:
                import time
                time.sleep(3)
    print(f"[NIPR] All {max_attempts} attempts failed for NPN {npn}")

def run_nipr_full_background(npn: str):
    try:
        print(f"[NIPR-FULL] Background job started for NPN {npn}")
        result = run_full_nipr_and_update(npn)
        print(f"[NIPR-FULL] Completed for {npn}: {result}")
    except Exception as e:
        print(f"[NIPR-FULL ERROR] {npn}: {e}")


def _run_one(payload: Dict[str, Any]):
    # ✅ Pin the Postgres target for THIS job before any DB call (incl. log_start).
    # Runs on the executor's worker thread; thread-local keeps concurrent bulk
    # jobs isolated from each other.
    active_source = set_db_source(payload.get("source"))

    txn_id = payload["txn_id"]
    module = payload.get("module", "").upper()
    process_type = "[CAQH_LOOKUP]" if module == "CAQH" else payload.get("process_type") or "NPI Scrape"

    try:
        print(f"[INFO] txn {txn_id} writing to Postgres source='{active_source}'")
        log_utils.log_start(
            txn_id=txn_id,
            script_name="run_npi_scrape",
            process_type=process_type,
            file_path=payload.get("file_path"),
            company_id=payload.get("company_id"),
            carrier_id=payload.get("carrier_id"),
        )

        # --- CAQH Validation & Full Pull ---
        if module == "CAQH":
            caqh_id = payload.get("caqh_id")
            npi = payload.get("npi")

            # Step 1: Quick validation
            validation = validate_caqh_id(caqh_id)

            if not validation["valid"]:
                msg = validation["status"]
                log_utils.log_end(txn_id=txn_id, success=False, error=msg, is_caqh=True)
                return

            #294 case — stop, do NOT pull
            if validation.get("blocking"):
                msg = validation["status"]
                log_utils.log_end(txn_id=txn_id, success=False, error=msg, is_caqh=True)
                return
                

            # Step 2: Full CAQH lookup (includes NPI verification)
            result = run_caqh_lookup(npi=npi, caqh_id=caqh_id, txn_id_provider=txn_id)

            if not result.get("success"):
                msg = result.get("status", "CAQH lookup failed")
                print(f"[ERROR] {msg}")
                log_utils.log_end(txn_id=txn_id, success=False, error=msg, is_caqh=True)
                return

            # ✅ Step 3: Upload parsed CAQH tables to SQL
            print(f"[CAQH] Lookup successful for CAQH {caqh_id} (NPI {npi}) — uploading tables...")
            try:
                upload_utils.upload_caqh_results(txn_id, result)
                print(f"[CAQH] ✅ Upload completed for NPI {npi}, CAQH {caqh_id}")
                log_utils.log_end(txn_id=txn_id, success=True, is_caqh=True)
            except Exception as e:
                print(f"[ERROR] CAQH upload failed: {e}")
                log_utils.log_end(txn_id=txn_id, success=False, error=str(e), is_caqh=True)

            return  # ✅ done, no NPI scrape after CAQH

        # --- Other modules (NPI / TMB / OIG / etc.) ---
        result = run_npi_scrape(
            txn_id=txn_id,
            npi=payload["npi"],
            module=module,
            caqh_id=payload.get("caqh_id"),
            dry_run=payload.get("dryrun", False),
        )

        log_utils.log_end(txn_id=txn_id, success=True)
        print(f"[INFO] Logging completed for {txn_id} ({process_type})")

    except Exception as e:
        log_utils.log_end(txn_id=txn_id, success=False, error=f"{type(e).__name__}: {e}")
        print(f"[ERROR] {type(e).__name__}: {e}")


# --- Health check ---
@app.get("/healthz")
def healthz():
    return {"ok": True}


# --- /trigger ---
@app.post("/trigger", include_in_schema=True)
def trigger_single_or_bulk(body: Union[Dict[str, Any], List[Dict[str, Any]]] = Body(...)):
    # --- Bulk trigger (list of jobs) ---
    if isinstance(body, list):
        jobs = body
        if not jobs:
            raise HTTPException(400, "Empty job list")
        # No top-level source for a bare list; each job may carry its own.
        _enqueue_jobs(jobs=jobs, module="ALL", dryrun=False, source="myops")
        return {"status": "submitted", "lane": "bulk", "count": len(jobs), "concurrency_cap": 5}

    # --- Bulk trigger with "jobs" key ---
    if isinstance(body, dict) and "jobs" in body:
        jobs = body["jobs"]
        if not isinstance(jobs, list) or not jobs:
            raise HTTPException(400, "jobs[] must be a non-empty array")
        module = body.get("module", "ALL")
        dryrun = bool(body.get("dryrun", False))
        source = body.get("source", "myops")  # top-level default; per-job overrides
        _enqueue_jobs(jobs=jobs, module=module, dryrun=dryrun, source=source)
        return {"status": "submitted", "lane": "bulk", "count": len(jobs), "concurrency_cap": 5}

    # --- Bulk trigger with txn_ids + npis arrays ---
    if isinstance(body, dict) and "txn_ids" in body and "npis" in body:
        txn_ids = body["txn_ids"]
        npis = body["npis"]
        if len(txn_ids) != len(npis):
            raise HTTPException(400, "txn_ids and npis must have the same length")
        module = body.get("module", "ALL")
        dryrun = bool(body.get("dryrun", False))
        source = body.get("source", "myops")
        jobs = [{"txn_id": t, "npi": n} for t, n in zip(txn_ids, npis)]
        _enqueue_jobs(jobs=jobs, module=module, dryrun=dryrun, source=source)
        return {"status": "submitted", "lane": "bulk", "count": len(jobs), "concurrency_cap": 5}

    # --- Single job ---
    try:
        req = ScrapeRequest(**body)
    except Exception as e:
        raise HTTPException(400, f"Invalid body: {e}")

    # ✅ Real-time CAQH validation + background pull
    if req.module.upper() == "CAQH":
        caqh_id = req.caqh_id
        npi = req.npi

        # Step 1: Basic ID validation
        validation = validate_caqh_id(caqh_id)

        if not validation["valid"]:
            return JSONResponse(
                status_code=200,
                content={
                    "status": validation["status"],
                    "lane": "single",
                    "npi": npi,
                    "caqh_id": caqh_id
                }
            )

        #294 — valid but blocked
        if validation.get("blocking"):
            return JSONResponse(
                status_code=200,
                content={
                    "status": validation["status"],
                    "lane": "single",
                    "npi": npi,
                    "caqh_id": caqh_id
                }
            )


        # Step 2: Deep CAQH lookup & NPI verification
        result = run_caqh_lookup(npi=npi, caqh_id=caqh_id, txn_id_provider=req.txn_id)

        if not result.get("success"):
            msg = result.get("status", "CAQH lookup failed")
            caqh_npi = result.get("caqh_npi")
            return JSONResponse(
                status_code=200,
                content={
                    "status": msg,
                    "lane": "single",
                    "npi": npi,
                    "caqh_id": caqh_id,
                    "caqh_npi": caqh_npi
                }
            )

        # ✅ Valid CAQH and NPI match → trigger background CAQH pull
        payload = {
            "txn_id": req.txn_id,
            "npi": req.npi,
            "module": "CAQH",
            "dryrun": req.dryrun,
            "company_id": req.company_id,
            "carrier_id": req.carrier_id,
            "file_path": req.file_path,
            "process_type": req.process_type or "CAQH Lookup",
            "caqh_id": req.caqh_id,
            "source": req.source,
        }
        SINGLE_EXECUTOR.submit(_run_one, payload)

        return JSONResponse(
            status_code=200,
            content={
                "status": "Valid CAQH and NPI match — pull initiated",
                "lane": "single",
                "npi": npi,
                "caqh_id": caqh_id
            }
        )

    # --- Normal NPI Scrape Trigger ---
    payload = {
        "txn_id": req.txn_id,
        "npi": req.npi,
        "module": req.module,
        "dryrun": req.dryrun,
        "company_id": req.company_id,
        "carrier_id": req.carrier_id,
        "file_path": req.file_path,
        "process_type": req.process_type or "NPI Scrape",
        "caqh_id": req.caqh_id,
        "source": req.source,
    }
    SINGLE_EXECUTOR.submit(_run_one, payload)

    return {
        "status": "submitted",
        "lane": "single",
        "npi": req.npi,
        "txn_id": req.txn_id,
        "module": req.module
    }
# --- Helper for bulk jobs ---
def _enqueue_jobs(*, jobs: List[Dict[str, Any]], module: str, dryrun: bool, source: str = "myops"):
    for j in jobs:
        if not isinstance(j, dict) or not j.get("txn_id") or not j.get("npi"):
            raise HTTPException(400, "Each job must include txn_id and npi")
        payload = {
            "txn_id": str(j["txn_id"]),
            "npi": str(j["npi"]),
            "module": j.get("module", module),
            "dryrun": bool(j.get("dryrun", dryrun)),
            "company_id": j.get("company_id"),
            "carrier_id": j.get("carrier_id"),
            "file_path": j.get("file_path"),
            "process_type": j.get("process_type") or "NPI Bulk Child",
            "caqh_id": j.get("caqh_id"),
            "source": j.get("source", source),  # per-job overrides top-level default
        }
        BULK_EXECUTOR.submit(_run_one, payload)

@app.post("/nipr")
def nipr_endpoint(body: Dict[str, str]):
    npn = body.get("npn")
    if not npn:
        raise HTTPException(status_code=400, detail="npn is required")

    # Submit job to background thread
    NIPR_EXECUTOR.submit(run_nipr_background, npn)

    # Respond immediately
    return {
    "success": True,
    "npn": npn,
    "message": f"NIPR lookup initiated for NPN {npn}. Results will update shortly."
    }

@app.post("/nipr_full")
def nipr_full_endpoint(body: Dict[str, str]):
    """
    Full NIPR endpoint:
      - runs nipr_crawler_parser.run_full_nipr_and_update
      - downloads detail report
      - parses + uploads PDF to blob
      - updates Zoho Contact (addresses, DOB, state flags)
      - upserts Zoho Licenses (active + inactive)
    """
    npn = body.get("npn")
    if not npn:
        raise HTTPException(status_code=400, detail="npn is required")

    # Submit job to background thread
    NIPR_EXECUTOR.submit(run_nipr_full_background, npn)

    # Respond immediately
    return {
        "success": True,
        "npn": npn,
        "message": f"Full NIPR detail + Zoho update initiated for NPN {npn}."
    }

@app.post("/ops_nipr")
def ops_nipr_endpoint(body: Dict[str, Any] = Body(...)):
    """
    Smart NIPR pipeline (runs synchronously, returns the actual result):
      - If data in DB and < 1 year old -> already_present
      - If PDF in blob and < 1 year old -> reuses PDF, parses, upserts
      - Else -> scrapes NIPR, uploads PDF, parses, upserts
    """
    npn = body.get("npn")

    if not npn:
        raise HTTPException(status_code=400, detail="npn is required")

    try:
        result = run_ops_nipr(npn)
        return result
    except Exception as e:
        print(f"[OPS_NIPR ERROR] {npn}: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "npn": npn, "error": str(e)},
        )

@app.post("/ai_nipr_scrape")
def ai_nipr_scrape_endpoint(body: Dict[str, str]):
    """
    Authenticated NIPR license lookup (no CAPTCHA).
    Returns full scraped data or NO_RESULTS.
    """

    license_number = body.get("license_number")
    state = body.get("state")
    applicant_type = body.get("applicant_type", "AGENT")

    if not license_number or not state:
        raise HTTPException(
            status_code=400,
            detail="license_number and state are required"
        )

    try:
        result = run_ai_nipr_scrape(
            license_number=license_number,
            state=state,
            applicant_type=applicant_type
        )

        return {
            "success": True,
            "result": result
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e)
            }
        )
        
@app.post("/service_interruptions/run")
def run_service_interruptions_endpoint():
    try:
        run_service_interruption_engine()
        return {
            "success": True,
            "message": "Service interruption state evaluation completed."
        }
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": f"{type(e).__name__}: {str(e)}",
                "trace": traceback.format_exc()
            }
        )
