"""
The single pipeline entry point. run(sel) replaces run_tebra_rpa,
run_notes_only, and run_facesheets_and_zip_backfill. Every mode
(daily / backfill / target) flows through the same passes; only the
WorkSelector differs.
"""

import os
import threading
import time
from dataclasses import replace
from datetime import date, timedelta

from playwright.sync_api import sync_playwright

from .config import (
    DOWNLOAD_DIR, PLAYWRIGHT_HEADLESS, PLAYWRIGHT_LAUNCH_ARGS, PLAYWRIGHT_VIEWPORT,
    DAILY_INGEST_LOOKBACK_DAYS,
)
from .browser import BROWSER_LAUNCH_LOCK
from .selector import WorkSelector
from .db import get_ehr_connection, log_run_event
from .session import (
    login_and_select_practice, discover_practices, PracticeNotFoundError,
    now_cst, cleanup_acc_directory, practice_download_dir, cleanup_practice_download_dir,
    _wait_for_otp_then_resolve,
)
from .passes import (
    pass_appointments, pass_notes, pass_facesheets, pass_charges, pass_patient_match,
)
from .zipbuild import pass_zip
from .config import TABLE_NAME


def _ts() -> str:
    return now_cst().strftime("%Y-%m-%d %H:%M:%S %Z")


def _log(message: str):
    print(f"[PIPELINE] [{_ts()}] {message}", flush=True)


# Confirmed live 2026-09-24: DailyPdfLoaderJob.js fires every practice's
# /run-tebra call as fire-and-forget, all landing within ~tens of ms of each
# other (5 practices in one burst, confirmed via cron_job_executions).
# Each one used to do its OWN full discovery login (fresh browser, fresh
# Tebra sign-in) just to resolve its practice name against the tile list --
# 5 redundant logins to the exact same shared Tebra account (LOGIN_URL/
# EMAIL/PASSWORD in config.py are not per-practice; one login already shows
# every practice's tile), each one a fresh chance to trip Tebra's OTP
# challenge. _DISCOVERY_CACHE_LOCK is held for the WHOLE discovery login
# (not just a cache check), so a request that lands while another is already
# logging in queues behind it and then reuses its result, instead of also
# logging in itself. 60s TTL is comfortably wider than the observed burst
# spread, while still refreshing if /run-tebra is called standalone well
# outside a daily fan-out.
_DISCOVERY_CACHE_LOCK = threading.Lock()
_DISCOVERY_CACHE_TTL_SECONDS = 60
_discovery_cache: dict = {"practices": None, "fetched_at": 0.0}


def _get_discovered_practices() -> list:
    with _DISCOVERY_CACHE_LOCK:
        cached = _discovery_cache["practices"]
        if cached is not None and (time.monotonic() - _discovery_cache["fetched_at"]) < _DISCOVERY_CACHE_TTL_SECONDS:
            return cached

        from .config import LOGIN_URL, EMAIL, PASSWORD

        # BROWSER_LAUNCH_LOCK (ehr/browser.py): only one Chromium instance
        # runs at a time process-wide, so a concurrent per-practice login
        # (session.py's login_and_select_practice, still one per practice)
        # doesn't starve this discovery login's own grid render.
        with BROWSER_LAUNCH_LOCK, sync_playwright() as p:
            browser = p.chromium.launch(
                headless=PLAYWRIGHT_HEADLESS,
                args=PLAYWRIGHT_LAUNCH_ARGS,
            )
            context = browser.new_context(
                no_viewport=(PLAYWRIGHT_VIEWPORT is None), viewport=PLAYWRIGHT_VIEWPORT,
            )
            page = context.new_page()
            page.goto(LOGIN_URL)
            page.fill("#userName", EMAIL)
            page.fill("#password", PASSWORD)
            page.click("#sign-in")
            # Confirmed live 2026-09-24: this discovery login had no OTP
            # handling at all (unlike login_and_select_practice's later
            # per-practice login, which does via _handle_otp) -- so once
            # Tebra started prompting for OTP here too, every practice
            # failed identically, waiting 30s for a "Practice select"
            # heading sitting behind an unhandled OTP modal instead.
            # Confirmed live 2026-10-01: a single _handle_otp() check right
            # after sign-in only covers ~1.2s -- if the modal renders later
            # than that it's missed entirely, same gap as
            # login_and_select_practice had. Poll over a longer window
            # instead (see _wait_for_otp_then_resolve's docstring).
            _wait_for_otp_then_resolve(page)
            # discover_practices() does its own wait for 'Practice select'
            # (falling back to a single-practice dashboard landing if that
            # times out -- see its docstring in session.py) so no separate
            # wait is needed here.
            discovered = discover_practices(page)
            browser.close()

        _discovery_cache["practices"] = discovered
        _discovery_cache["fetched_at"] = time.monotonic()
        return discovered


def _window(sel):
    """Resolve the [from, to] window used by passes that need explicit dates
    (appointment scrape, patient-match). Daily = the trailing
    DAILY_INGEST_LOOKBACK_DAYS through today (a self-healing re-scrape, not
    just today -- see DAILY_INGEST_LOOKBACK_DAYS in config.py for why); target
    = the date given or today; backfill = the window."""
    if sel.mode == "backfill":
        return sel.start_date, sel.end_date
    if sel.mode == "target" and sel.start_date:
        return sel.start_date, sel.start_date
    today = now_cst().date()
    if sel.mode == "daily":
        return today - timedelta(days=DAILY_INGEST_LOOKBACK_DAYS), today
    return today, today


# Practices are still requested fully in parallel (DailyPdfLoaderJob.js fires
# every practice's /run-tebra call as fire-and-forget instead of
# one-at-a-time). An earlier version of this file serialized every run behind
# one global slot to work around the shared DOWNLOAD_DIR root
# (passes.py/zipbuild.py wrote straight into it by bare filename, with no
# per-practice separation, so one practice finishing and cleaning up
# mid-scrape could delete another practice's just-downloaded file). That
# file-collision issue is fixed properly now: practice_download_dir()
# (ehr/session.py) gives each practice its own subfolder, threaded through
# pass_facesheets/pass_zip below, so concurrent practices never share a path.
#
# But a *separate* problem showed up once that removed the old serialization:
# concurrent Chromium instances (confirmed live 2026-09-17, rotating
# ".MuiDataGrid-virtualScroller" timeouts across different practices run by
# the same DailyPdfLoaderJob.js fan-out -- PrePost+ Tennessee, then
# PrePostPlus Germantown) starve each other of CPU/RAM/`/dev/shm` on the VM.
# BROWSER_LAUNCH_LOCK (ehr/browser.py) below re-serializes only the browser
# lifetime itself (not the whole practice's DB/upload work), so at most one
# Chromium instance runs at a time process-wide while still keeping the
# per-practice download-dir isolation above.
def run(sel: WorkSelector, scrape_patients=None, no_upload=False, skip_appointment_scrape=False,
        execution_id=None):
    if not DOWNLOAD_DIR:
        raise RuntimeError(
            "EHR_DOWNLOAD_DIR is empty. Set EHR_DOWNLOAD_DIR in environment or .env "
            "(repo root .env or myops/.env)."
        )
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    run_start = now_cst()
    run_clock = time.monotonic()
    _log(
        f"Run started mode={sel.mode} practice={sel.practice or 'ALL'} "
        f"entity={sel.entity} sub_entity={sel.sub_entity} ehr={sel.ehr_name}"
    )

    # Patient roster refresh. The scrape itself diffs against ehr_patients and
    # only inserts new / updates changed (SCD via effective_end_date), so this
    # is a no-op where nothing changed — we're only deciding whether to pay the
    # browser-walk cost. Default: on for full-scope daily/backfill runs,
    # off for target and single-practice runs (to avoid all-practice roster walk).
    # It launches its OWN browser, so it must run before we open ours.
    if scrape_patients is None:
        scrape_patients = (sel.mode in ("daily", "backfill")) and not bool(sel.practice)
    if scrape_patients:
        try:
            from .patients import run_patient_insurance_rpa
            _log("Patient roster scrape starting")
            run_patient_insurance_rpa(sel.entity, sel.sub_entity, sel.ehr_name)
            _log("Patient roster scrape done")
        except Exception as e:
            # A patient-scrape failure shouldn't abort the Tebra passes; match
            # will just reconcile against whatever roster exists.
            _log(f"Patient roster scrape failed (continuing): {e!r}")

    # Practices to process this run:
    #  - sel.practice given (e.g. /run-tebra -- payload already names the
    #    practice): no live discovery login needed at all. The per-practice
    #    login below (login_and_select_practice) already logs in fresh for
    #    THIS specific request and independently matches it against Tebra's
    #    live tiles (or a single-practice dashboard landing), with no shared
    #    cache -- so it can't be corrupted by what a DIFFERENT concurrent
    #    /run-tebra call's discovery login happened to land on. Confirmed
    #    live 2026-09-28/29: routing this through the shared, time-cached
    #    _get_discovered_practices() meant that whenever that discovery
    #    login itself landed on a single practice's dashboard (Tebra's
    #    behavior when a login resolves to just one practice) instead of the
    #    tile picker, EVERY other concurrent practice in the same
    #    DailyPdfLoaderJob.js fan-out failed to resolve against that
    #    incomplete 1-item cached list.
    #  - sel.practice is None (true "all practices" daily/backfill run):
    #    still needs live discovery to enumerate the full list.
    if sel.practice:
        practices = [sel.practice]
    else:
        practices = _get_discovered_practices()

    _log(f"Resolved practices count={len(practices)} practices={practices}")

    # A requested practice not found in Tebra (PracticeNotFoundError) is
    # recorded here with its reason instead of as a failure; server.py's
    # _summary_outcome() surfaces skipped_details in error_description.
    skipped = []
    skipped_details = {}
    completed, failed = [], []
    failed_details = {}
    # Per practice: facesheets pulled/zipped, claims created, DCNs.
    practice_results = {}
    for requested_practice in practices:
        _log(f"Practice start name={requested_practice}")
        practice_clock = time.monotonic()
        # Canonical Tebra name, known only once login_and_select_practice
        # below returns it -- until then, fall back to the requested name
        # for logging/error-reporting if login itself fails.
        practice = requested_practice
        practice_dir = None
        # Seconds per phase, saved on the run's summary (practice_results) so
        # a slow run shows where its time went without the VM console log.
        timings = {}
        notes_not_found = []

        # Fresh browser + context PER PRACTICE. Tebra keeps a session logged in
        # to one practice; reusing the browser means the next practice's login
        # bounces straight to the dashboard and #sign-in never appears. A clean
        # browser guarantees the sign-in screen every time.
        # BROWSER_LAUNCH_LOCK held for this practice's whole browser lifetime,
        # so a concurrently-running practice from another /run-tebra call
        # waits its turn instead of rendering at the same time.
        with BROWSER_LAUNCH_LOCK, sync_playwright() as p:
            browser = p.chromium.launch(
                headless=PLAYWRIGHT_HEADLESS,
                args=PLAYWRIGHT_LAUNCH_ARGS,
            )
            context = browser.new_context(
                no_viewport=(PLAYWRIGHT_VIEWPORT is None), viewport=PLAYWRIGHT_VIEWPORT,
            )
            page = context.new_page()
            try:
                phase_clock = time.monotonic()
                # Returns Tebra's own canonical practice text (tile text, or
                # the single-practice dashboard header) -- not necessarily
                # the same string as requested_practice (server.py passes a
                # lowercased/space-stripped payload value), so downstream DB
                # writes/folder naming stay consistent regardless of how the
                # caller spelled it.
                practice = login_and_select_practice(page, requested_practice)
                timings["login"] = round(time.monotonic() - phase_clock, 1)
                _log(f"Practice={practice} login/select done elapsed={timings['login']}s")

                psel = WorkSelector(
                    mode=sel.mode, practice=practice,
                    folder_structure=sel.folder_structure,
                    start_date=sel.start_date, end_date=sel.end_date,
                    appt_id=sel.appt_id, patient_name=sel.patient_name,
                    entity=sel.entity, sub_entity=sel.sub_entity, ehr_name=sel.ehr_name,
                    ungated_repull=sel.ungated_repull,
                )
                from_date, to_date = _window(psel)
                practice_dir = practice_download_dir(sel.entity, sel.sub_entity, practice)

                # skip_appointment_scrape=True bypasses the live Tebra worklist
                # calendar walk (the expensive, per-day UI scrape that
                # MAX_DATE_RANGE_DAYS caps at 7 days in /run-tebra) -- used by
                # /run-tebra-recheck for wide-window notes/facesheets/charges
                # catch-up over rows already in the DB, where no fresh
                # appointment discovery is needed.
                if psel.mode in ("daily", "backfill") and not skip_appointment_scrape:
                    phase_clock = time.monotonic()
                    pass_appointments(page, psel, practice, from_date, to_date)
                    timings["appointments"] = round(time.monotonic() - phase_clock, 1)
                    _log(f"Practice={practice} appointments pass done elapsed={timings['appointments']}s")

                phase_clock = time.monotonic()
                # marks signed + records charge_status; returns the Checked Out
                # appointments whose Finished-tab card wasn't found
                notes_not_found = pass_notes(page, psel) or []
                timings["notes"] = round(time.monotonic() - phase_clock, 1)
                _log(f"Practice={practice} notes pass done elapsed={timings['notes']}s "
                     f"not_found={len(notes_not_found)}")

                phase_clock = time.monotonic()
                pass_facesheets(page, context, psel, download_dir=practice_dir)   # normal + missed-charges re-download
                timings["facesheets"] = round(time.monotonic() - phase_clock, 1)
                _log(f"Practice={practice} facesheets pass done elapsed={timings['facesheets']}s")

                phase_clock = time.monotonic()
                notes_not_found, pull_retries = _retry_unpulled(
                    page, context, psel, practice, practice_dir, notes_not_found,
                )
                if pull_retries:
                    timings["pull_retries"] = round(time.monotonic() - phase_clock, 1)

                phase_clock = time.monotonic()
                pass_charges(page, psel)        # VIEW CHARGE -> charge_data
                timings["charges"] = round(time.monotonic() - phase_clock, 1)
                _log(f"Practice={practice} charges pass done elapsed={timings['charges']}s")

                phase_clock = time.monotonic()
                zip_result = pass_zip(psel, practice, no_upload=no_upload, download_dir=practice_dir,
                                      execution_id=execution_id)  # one ZIP incl. charge_data
                zip_result = zip_result or {}
                # ZIP + upload + the PDF processor's claim creation / wait.
                timings["zip_upload_and_claims"] = round(time.monotonic() - phase_clock, 1)
                practice_results[practice] = {
                    "date_range": {"from": str(from_date), "to": str(to_date)},
                    **_facesheet_claim_report(zip_result),
                    "not_found_in_dashboard": notes_not_found,
                    "reconciliation": _reconcile(psel, notes_not_found, pull_retries, execution_id),
                    "timings_seconds": timings,
                }
                _log(
                    f"Practice={practice} zip/upload pass done elapsed={timings['zip_upload_and_claims']}s "
                    f"attempted={zip_result.get('attempted_count', 0)} "
                    f"uploaded={zip_result.get('uploaded_count', 0)} "
                    f"failed={zip_result.get('failed_count', 0)} "
                    f"container={zip_result.get('container', 'n/a')}"
                )
                if zip_result.get("failed_count", 0) > 0:
                    raise RuntimeError(
                        "ZIP upload failed: "
                        f"{zip_result.get('upload_error') or 'unknown upload error'}"
                    )

                phase_clock = time.monotonic()
                pass_patient_match(psel, practice, from_date, to_date)
                timings["patient_match"] = round(time.monotonic() - phase_clock, 1)
                _log(f"Practice={practice} patient-match pass done elapsed={timings['patient_match']}s")

                completed.append(practice)
                timings["total"] = round(time.monotonic() - practice_clock, 1)
                _log(f"Practice success name={practice} elapsed={timings['total']}s")
            except PracticeNotFoundError as e:
                # Practice isn't available to this login -- nothing to
                # scrape, not a broken run. Skip it with the reason.
                _log(f"Practice skipped name={practice} reason={e}")
                skipped.append(practice)
                skipped_details[practice] = str(e)
            except Exception as e:
                _log(f"Practice failed name={practice} error={e!r}")
                failed.append(practice)
                failed_details[practice] = repr(e)
                # Keep how far it got and how long each finished phase took.
                timings["total"] = round(time.monotonic() - practice_clock, 1)
                practice_results.setdefault(practice, {}).update({
                    "not_found_in_dashboard": notes_not_found,
                    "timings_seconds": timings,
                })
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
                cleanup_practice_download_dir(practice_dir)

    _log_run(sel, run_start)
    # Safety-net sweep of the shared DOWNLOAD_DIR root itself, kept for
    # anything that might still land there directly - normal facesheet/zip
    # traffic no longer does, now that it's all scoped under practice_dir
    # above, so this is expected to be a no-op most of the time.
    cleanup_acc_directory()
    _log(
        f"Run finished completed={completed} failed={failed} skipped={skipped} "
        f"elapsed={time.monotonic() - run_clock:.1f}s"
    )
    return {
        # The dates actually pulled (daily = lookback window through today),
        # not just whatever the request sent.
        "date_range": {"mode": sel.mode, "from": str(_window(sel)[0]), "to": str(_window(sel)[1])},
        "practices": practices,
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "skipped_details": skipped_details,
        "failed_details": failed_details,
        "practice_results": practice_results,
    }


# Extra scrape/pull rounds for signed appointments the first pass didn't
# pull. Each round only touches those appointments (normal "not yet
# Processed" gate, never a re-download of pulled ones) and re-reads the
# Dashboard only when cards were missing, so a clean run costs nothing.
PULL_RETRY_ROUNDS = 2


def _signed_appointments(sel):
    """Every signed, visit-happened appointment in scope (the facesheets
    pass's own eligibility, minus the process_status gate), with its pull
    state: (appt_id, patient_name, appt_date, pulled, reason)."""
    from .query import scope_clause, _NOT_NO_VISIT
    where, params = scope_clause(sel)
    where += ["appt_note IS NOT NULL", _NOT_NO_VISIT]
    conn = get_ehr_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT appt_id, patient_name, appt_date, process_status,
                   tebra_facesheet_id, process_error_stage, process_error_message
            FROM {TABLE_NAME}
            WHERE {' AND '.join(where)}
            ORDER BY appt_date, appt_id
            """,
            params,
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()

    out = []
    for appt_id, patient_name, appt_date, status, facesheet_id, stage, message in rows:
        pulled = status == "Processed" and facesheet_id is not None
        if pulled:
            reason = None
        elif status == "Error":
            reason = f"{stage or 'pull'}: {message or 'error, no message recorded'}"
        else:
            reason = "facesheet not downloaded"
        out.append((str(appt_id), patient_name, str(appt_date), pulled, reason))
    return out


def _retry_unpulled(page, context, sel, practice, download_dir, notes_not_found):
    """Up to PULL_RETRY_ROUNDS more notes/facesheets rounds for whatever is
    still unpulled; stops early once a round recovers nothing. Returns
    (notes_not_found, rounds_run). Bounded runs only (backfill / target):
    daily mode has no date bound, so this would retry the whole backlog."""
    if sel.mode == "daily":
        return notes_not_found, 0
    retry_sel = replace(sel, ungated_repull=False)
    rounds = 0
    while rounds < PULL_RETRY_ROUNDS:
        unpulled = [a for a in _signed_appointments(sel) if not a[3]]
        missing_before = len(unpulled) + len(notes_not_found)
        if not missing_before:
            break
        rounds += 1
        _log(f"Practice={practice} retry {rounds}/{PULL_RETRY_ROUNDS}: "
             f"{len(unpulled)} signed not pulled, {len(notes_not_found)} not found on Dashboard")
        if notes_not_found:
            notes_not_found = pass_notes(page, retry_sel) or []
        pass_facesheets(page, context, retry_sel, download_dir=download_dir)
        missing_after = len([a for a in _signed_appointments(sel) if not a[3]]) + len(notes_not_found)
        _log(f"Practice={practice} retry {rounds} done: missing {missing_before} -> {missing_after}")
        if missing_after >= missing_before:
            break
    return notes_not_found, rounds


def _reconcile(sel, notes_not_found, pull_retries, execution_id):
    """Scraped -> pulled -> main claims for this run, with the reason for
    every appointment that fell short. Scraped = signed appointments plus
    Checked Out ones whose Dashboard card was never found (so never signed
    here). Claims are main claims only: an appointment counts once it has
    one, whichever run created it (a re-pull's duplicates included).
    None for daily mode, whose unbounded scope isn't one run's work."""
    if sel.mode == "daily":
        return None
    signed = _signed_appointments(sel)
    pulled = [a for a in signed if a[3]]
    not_pulled = [
        {"appt_id": a[0], "patient_name": a[1], "appt_date": a[2], "reason": a[4]}
        for a in signed if not a[3]
    ] + [
        {"appt_id": str(a["appt_id"]), "patient_name": a["patient_name"], "appt_date": a["appt_date"],
         "reason": "not found on the Dashboard Finished tab"}
        for a in notes_not_found
    ]

    result = {
        "scraped": len(signed) + len(notes_not_found),
        "pulled": len(pulled),
        "main_claims": None,
        "main_claims_created_this_run": None,
        "not_pulled": not_pulled,
        "no_main_claim": [],
        "pull_retries": pull_retries,
    }
    if not pulled:
        result["main_claims"] = 0
        return result

    try:
        from .pdf_processor import _lookup_claims, count_execution_main_claims
        claims = _lookup_claims([a[0] for a in pulled])
        result["main_claims"] = sum(1 for a in pulled if (claims.get(a[0]) or {}).get("claim_id"))
        for appt_id, patient_name, appt_date, _pulled, _reason in pulled:
            row = claims.get(appt_id)
            if row and row["claim_id"]:
                continue
            if row and row["failure_reason"]:
                reason = row["failure_reason"]
            elif row and row["processing_status"] in ("pending", "processing"):
                reason = f"claim creation still {row['processing_status']}"
            elif row:
                reason = "no claim linked to this facesheet yet"
            else:
                reason = "facesheet not uploaded for claim creation"
            result["no_main_claim"].append(
                {"appt_id": appt_id, "patient_name": patient_name, "appt_date": appt_date, "reason": reason}
            )
        if execution_id:
            result["main_claims_created_this_run"] = count_execution_main_claims(execution_id)
    except Exception as e:
        result["claims_lookup_error"] = repr(e)
    return result


def _facesheet_claim_report(zip_result):
    """Facesheets pulled -> processed -> claims (with DCNs) for one practice,
    shaped for the cron_job_executions response."""
    claims = zip_result.get("claims")
    report = {
        "facesheets_pulled": zip_result.get("facesheets_zipped", 0),
        "missing_pdfs": zip_result.get("missing_pdfs", 0),
        "zip_name": zip_result.get("zip_name"),
        "zip_uploaded": bool(zip_result.get("uploaded_count")),
    }
    if not claims:
        report["claims_note"] = (
            "No facesheets to process" if not zip_result.get("zip_name")
            else "Processor result for this ZIP not found (already handled by another run, or processor failed)"
        )
        return report
    files = claims.get("files") or []
    report.update({
        "processed": claims.get("uploaded_now", 0) + claims.get("duplicates", 0),
        "duplicates": claims.get("duplicates", 0),
        "claims_created": claims.get("claims_confirmed", 0),
        "claims_not_created": claims.get("unconfirmed", 0),
        "exhausted": claims.get("exhausted", 0),
        "zip_moved_to_processed": claims.get("moved_to_processed"),
        "zip_note": claims.get("note"),
        # claim_id / dcn straight from "EDI_Tebra".claim_header
        "claims": [
            {"appt_id": f["appt_id"], "appt_date": f.get("appt_date"), "file": f["file"],
             "claim_id": f["claim_id"], "dcn": f["dcn"], "claim_status": f.get("claim_status"),
             "duplicate": f["status"] == "duplicate"}
            for f in files if f.get("claim_confirmed")
        ],
        "not_created": [
            {"appt_id": f["appt_id"], "appt_date": f.get("appt_date"), "file": f["file"],
             "status": f["status"], "error": f.get("error")}
            for f in files if not f.get("claim_confirmed")
        ],
        "visit_dates": sorted({str(f["appt_date"]) for f in files if f.get("appt_date")}),
    })
    return report


def _log_run(sel, run_start):
    """One run-log row; status=Error if any row in scope is process_status=Error."""
    has_error = False
    try:
        conn = get_ehr_connection()
        cur = conn.cursor()
        from .query import scope_clause
        where, params = scope_clause(sel)
        where.append("process_status = 'Error'")
        cur.execute(
            f"SELECT 1 FROM {TABLE_NAME} WHERE {' AND '.join(where)} LIMIT 1",
            tuple(params),
        )
        has_error = cur.fetchone() is not None
        cur.close()
        conn.close()
    except Exception as e:
        _log(f"Run summary error-check failed: {e}")

    label = sel.practice or "ALL"
    log_run_event(
        script_name="OPS_EMR_RPA",
        process_type=f"RCM - {label} ({sel.mode})",
        status="Error" if has_error else "Success",
        error="One or more records failed" if has_error else None,
        company_id=sel.entity,
        started_at=run_start,
        ended_at=now_cst(),
    )
