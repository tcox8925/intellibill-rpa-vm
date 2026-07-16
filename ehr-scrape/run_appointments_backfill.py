"""
run_appointments_backfill.py
============================
Backfill flow for Tebra — ALL practices discovered from UI.

Policy:
  - Facesheets only download for appointments with signed notes.
  - Appointments without signed notes stay process_status=NULL and get
    picked up on future daily runs when the note appears.
  - ONE ZIP per practice covering the full window.
  - PDFs deduped by facesheet_id; JSON has one record per signed-note
    appointment; multiple appointments can reference the same pdf_file.

Pass structure:
  1. ensure_appointments_schema (idempotent column adds)
  2. Discover all practices from the Tebra UI
  3. Patient roster scrape (all practices, one pass)
  4. Per practice:
     a. Chunked appointments-only scrape (Tebra caps reporting ~2 months)
     b. Final notes pass (full range)
     c. Final facesheets + ZIP + patient-match pass (full range)
  5. Parent BACKFILL row logged to wpo.ops_pch_logs

Any single-practice failure is logged and skipped; the rest continue.

Usage:
    python run_appointments_backfill.py
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import argparse
import traceback

from tebra_rpa import (
    run_tebra_rpa,
    run_notes_only,
    run_facesheets_and_zip_backfill,
    ensure_appointments_schema,
    discover_tebra_practices,
    log_run_to_pch,
    _normalize_text,
)
from ehr_patients import run_patient_insurance_rpa

# -- CONFIG (hardcoded per requirements) --
START_DATE = datetime(2025, 1, 1)
END_DATE   = datetime(2026, 4, 20)
ENTITY     = "270681372"
SUB_ENTITY = "270681372001"
EHR_NAME   = "Tebra"
CHUNK_DAYS = 60  # Tebra reporting cap is ~2 months per filter

CST = ZoneInfo("America/Chicago")


def generate_chunks(start, end, days):
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=days - 1), end)
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _log_backfill_run(start_dt, end_dt, has_error, error_message, success_message):
    """Write parent BACKFILL row to wpo.ops_pch_logs on pch. Never raises."""
    log_run_to_pch(
        script_name="OPS_EMR_RPA",
        process_type="BACKFILL",
        status="Error" if has_error else "Success",
        error=error_message if has_error else None,
        company_id=ENTITY,
        started_at=start_dt,
        ended_at=end_dt,
    )


def run_practice_backfill(practice_name, chunks):
    """
    Run the 3-step backfill for one practice.
    Returns (completed_chunks: list, failed_chunks: list).
    Raises on catastrophic failures only (not per-chunk).
    """
    completed, failed = [], []

    # ---- Step A: chunked appointments-only scrape ----
    total = len(chunks)
    for idx, (cs, ce) in enumerate(chunks, 1):
        label = f"[{idx}/{total}] {cs.date()} -> {ce.date()}"
        print(f"{label}  Scraping appointments for {practice_name} ...")
        try:
            run_tebra_rpa(
                start_date=cs,
                end_date=ce,
                practice_name=practice_name,
                entity=ENTITY,
                sub_entity=SUB_ENTITY,
                ehr_name=EHR_NAME,
                skip_notes=True,
                skip_facesheets=True,
            )
            completed.append(label)
            print(f"{label}  Done\n")
        except Exception as e:
            failed.append((label, repr(e)))
            print(f"{label}  FAILED: {repr(e)}")
            traceback.print_exc()
            print()

    # ---- Step B: final notes pass ----
    print(f"[NOTES] Final pass for {practice_name}: "
          f"{START_DATE.date()} -> {END_DATE.date()}")
    run_notes_only(
        start_date=START_DATE,
        end_date=END_DATE,
        practice_name=practice_name,
        entity=ENTITY,
        sub_entity=SUB_ENTITY,
        ehr_name=EHR_NAME,
    )
    print("[NOTES] Done\n")

    # ---- Step C: facesheets + ZIP + patient-match ----
    print(f"[FS+ZIP] Final pass for {practice_name}: "
          f"{START_DATE.date()} -> {END_DATE.date()}")
    run_facesheets_and_zip_backfill(
        start_date=START_DATE,
        end_date=END_DATE,
        practice_name=practice_name,
        entity=ENTITY,
        sub_entity=SUB_ENTITY,
        ehr_name=EHR_NAME,
    )
    print("[FS+ZIP] Done\n")

    return completed, failed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Tebra appointments/facesheets backfill"
    )
    parser.add_argument(
        "--practice",
        help="Only backfill practices whose name matches this (case- and "
             "space-insensitive substring, e.g. 'PrePost+Tennessee'). "
             "Default: all discovered practices.",
    )
    parser.add_argument(
        "--start", help=f"Start date YYYY-MM-DD (default: {START_DATE.date()})"
    )
    parser.add_argument(
        "--end", help=f"End date YYYY-MM-DD (default: {END_DATE.date()})"
    )
    parser.add_argument(
        "--skip-patients", action="store_true",
        help="Skip the all-practice patient roster pass (useful for a "
             "scoped, facesheet-only backfill).",
    )
    args = parser.parse_args()

    if args.start:
        START_DATE = datetime.strptime(args.start, "%Y-%m-%d")
    if args.end:
        END_DATE = datetime.strptime(args.end, "%Y-%m-%d")
    if END_DATE < START_DATE:
        raise SystemExit(f"end date {END_DATE.date()} is before start {START_DATE.date()}")

    run_start = datetime.now(CST)

    # ---- Schema migration ----
    ensure_appointments_schema()

    chunks = list(generate_chunks(START_DATE, END_DATE, CHUNK_DAYS))
    print(f"[BACKFILL] {START_DATE.date()} -> {END_DATE.date()}  "
          f"|  {len(chunks)} chunks per practice")

    # ---- Discover practices ----
    print("=" * 50)
    print("[BACKFILL] Discovering practices from Tebra UI")
    try:
        practices = discover_tebra_practices()
        print(f"[BACKFILL] Found {len(practices)} practices")
        for pr in practices:
            print(f"  - {pr}")
    except Exception as e:
        print(f"[BACKFILL] FAILED to discover practices: {repr(e)}")
        traceback.print_exc()
        _log_backfill_run(
            start_dt=run_start,
            end_dt=datetime.now(CST),
            has_error=True,
            error_message=f"Practice discovery failed: {repr(e)[:400]}",
            success_message=None,
        )
        raise

    if not practices:
        print("[BACKFILL] No practices found — exiting.")
        _log_backfill_run(
            start_dt=run_start,
            end_dt=datetime.now(CST),
            has_error=True,
            error_message="No practices found in Tebra UI",
            success_message=None,
        )
        raise SystemExit(1)

    # ---- Optional practice filter ----
    if args.practice:
        want = _normalize_text(args.practice)
        filtered = [p for p in practices
                    if want in _normalize_text(p) or _normalize_text(p) in want]
        if not filtered:
            print(f"[BACKFILL] No discovered practice matches '{args.practice}'.")
            print(f"[BACKFILL] Available: {practices}")
            _log_backfill_run(
                start_dt=run_start,
                end_dt=datetime.now(CST),
                has_error=True,
                error_message=f"No practice matched filter '{args.practice}'",
                success_message=None,
            )
            raise SystemExit(1)
        print(f"[BACKFILL] Practice filter '{args.practice}' -> {filtered}")
        practices = filtered

    # ---- Pass 1: patient roster (all practices, one pass) ----
    patients_error = None
    if args.skip_patients:
        print("=" * 50)
        print("[PATIENTS] Skipped (--skip-patients)")
    else:
        print("=" * 50)
        print("[PATIENTS] Starting roster scrape across all practices")
        try:
            run_patient_insurance_rpa(
                entity=ENTITY,
                sub_entity=SUB_ENTITY,
                ehr_name=EHR_NAME,
            )
            print("[PATIENTS] Done\n")
        except Exception as e:
            patients_error = repr(e)
            print(f"[PATIENTS] FAILED: {patients_error}")
            traceback.print_exc()
            print()

    # ---- Pass 2: per-practice backfill ----
    practice_completed = []
    practice_failed = []  # list of (practice_name, error_repr)
    per_practice_chunk_summary = {}  # practice_name -> (completed, failed)

    for pr in practices:
        print("=" * 50)
        print(f"[BACKFILL] Starting practice: {pr}")
        try:
            completed_chunks, failed_chunks = run_practice_backfill(pr, chunks)
            per_practice_chunk_summary[pr] = (completed_chunks, failed_chunks)
            practice_completed.append(pr)
            print(f"[BACKFILL] Finished practice: {pr} "
                  f"(chunks ok={len(completed_chunks)}, failed={len(failed_chunks)})\n")
        except Exception as e:
            err = repr(e)
            practice_failed.append((pr, err))
            print(f"[BACKFILL] Practice {pr} FAILED: {err}")
            traceback.print_exc()
            print()

    # ---- Summary ----
    print("=" * 50)
    print(f"Patients step: {'FAILED' if patients_error else 'ok'}")
    print(f"Practices completed: {len(practice_completed)}/{len(practices)}")
    if practice_failed:
        print(f"Practices failed:    {len(practice_failed)}")
        for pr, err in practice_failed:
            print(f"  {pr}  --  {err}")
    for pr, (c, f) in per_practice_chunk_summary.items():
        if f:
            print(f"[{pr}] {len(f)} chunk(s) failed:")
            for l, err in f:
                print(f"    {l}  --  {err}")
    print("=" * 50)

    # ---- Parent BACKFILL log row ----
    run_end = datetime.now(CST)
    has_error = bool(patients_error) or bool(practice_failed) or any(
        f for (_, f) in per_practice_chunk_summary.values()
    )

    summary_bits = []
    if patients_error:
        summary_bits.append(f"patients: {patients_error[:200]}")
    if practice_failed:
        names = [pr for pr, _ in practice_failed]
        summary_bits.append(f"practices failed: {names}")
    chunk_fail_total = sum(len(f) for (_, f) in per_practice_chunk_summary.values())
    if chunk_fail_total:
        summary_bits.append(f"{chunk_fail_total} chunk(s) failed across practices")
    error_message = " | ".join(summary_bits) if summary_bits else None

    success_message = (
        f"completed={len(practice_completed)}/{len(practices)} practice(s)"
        if not has_error else None
    )

    _log_backfill_run(
        start_dt=run_start,
        end_dt=run_end,
        has_error=has_error,
        error_message=(error_message or "")[:1000] or None,
        success_message=success_message,
    )