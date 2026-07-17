"""
The single pipeline entry point. run(sel) replaces run_tebra_rpa,
run_notes_only, and run_facesheets_and_zip_backfill. Every mode
(daily / backfill / target) flows through the same passes; only the
WorkSelector differs.
"""

import os
import time
from datetime import date

from playwright.sync_api import sync_playwright

from .config import DOWNLOAD_DIR, PLAYWRIGHT_HEADLESS, PLAYWRIGHT_LAUNCH_ARGS
from .selector import WorkSelector
from .db import get_ehr_connection, log_run_event
from .session import (
    login_and_select_practice, discover_practices, resolve_practice_name,
    now_cst, cleanup_acc_directory,
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


def _window(sel):
    """Resolve the [from, to] window used by passes that need explicit dates
    (appointment scrape, patient-match). Daily = today; target = the date given
    or today; backfill = the window."""
    if sel.mode == "backfill":
        return sel.start_date, sel.end_date
    if sel.mode == "target" and sel.start_date:
        return sel.start_date, sel.start_date
    today = now_cst().date()
    return today, today


def run(sel: WorkSelector, scrape_patients=None, no_upload=False):
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

    from .config import LOGIN_URL, EMAIL, PASSWORD

    # First, discover practices with a short-lived browser so a normalized API
    # payload can be resolved back to the canonical Tebra practice name.
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=PLAYWRIGHT_HEADLESS,
            args=PLAYWRIGHT_LAUNCH_ARGS,
        )
        context = browser.new_context(no_viewport=True)
        page = context.new_page()
        page.goto(LOGIN_URL)
        page.fill("#userName", EMAIL)
        page.fill("#password", PASSWORD)
        page.click("#sign-in")
        page.wait_for_selector("h3:has-text('Practice select')", timeout=30_000)
        discovered = discover_practices(page)
        browser.close()

    if sel.practice:
        practices = [resolve_practice_name(sel.practice, discovered)]
    else:
        practices = discovered

    _log(f"Resolved practices count={len(practices)} practices={practices}")

    completed, failed = [], []
    failed_details = {}
    for practice in practices:
        _log(f"Practice start name={practice}")
        practice_clock = time.monotonic()
        psel = WorkSelector(
            mode=sel.mode, practice=practice,
            start_date=sel.start_date, end_date=sel.end_date,
            appt_id=sel.appt_id, patient_name=sel.patient_name,
            entity=sel.entity, sub_entity=sel.sub_entity, ehr_name=sel.ehr_name,
        )
        from_date, to_date = _window(psel)

        # Fresh browser + context PER PRACTICE. Tebra keeps a session logged in
        # to one practice; reusing the browser means the next practice's login
        # bounces straight to the dashboard and #sign-in never appears. A clean
        # browser guarantees the sign-in screen every time.
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=PLAYWRIGHT_HEADLESS,
                args=PLAYWRIGHT_LAUNCH_ARGS,
            )
            context = browser.new_context(no_viewport=True)
            page = context.new_page()
            try:
                phase_clock = time.monotonic()
                login_and_select_practice(page, practice)
                _log(f"Practice={practice} login/select done elapsed={time.monotonic() - phase_clock:.1f}s")

                if psel.mode in ("daily", "backfill"):
                    phase_clock = time.monotonic()
                    pass_appointments(page, psel, practice, from_date, to_date)
                    _log(f"Practice={practice} appointments pass done elapsed={time.monotonic() - phase_clock:.1f}s")

                phase_clock = time.monotonic()
                pass_notes(page, psel)          # marks signed + records charge_status
                _log(f"Practice={practice} notes pass done elapsed={time.monotonic() - phase_clock:.1f}s")

                phase_clock = time.monotonic()
                pass_facesheets(page, context, psel)   # normal + missed-charges re-download
                _log(f"Practice={practice} facesheets pass done elapsed={time.monotonic() - phase_clock:.1f}s")

                phase_clock = time.monotonic()
                pass_charges(page, psel)        # VIEW CHARGE -> charge_data
                _log(f"Practice={practice} charges pass done elapsed={time.monotonic() - phase_clock:.1f}s")

                phase_clock = time.monotonic()
                pass_zip(psel, practice, no_upload=no_upload)  # one ZIP incl. charge_data
                _log(f"Practice={practice} zip/upload pass done elapsed={time.monotonic() - phase_clock:.1f}s")

                phase_clock = time.monotonic()
                pass_patient_match(psel, practice, from_date, to_date)
                _log(f"Practice={practice} patient-match pass done elapsed={time.monotonic() - phase_clock:.1f}s")

                completed.append(practice)
                _log(f"Practice success name={practice} elapsed={time.monotonic() - practice_clock:.1f}s")
            except Exception as e:
                _log(f"Practice failed name={practice} error={e!r}")
                failed.append(practice)
                failed_details[practice] = repr(e)
            finally:
                try:
                    browser.close()
                except Exception:
                    pass

    _log_run(sel, run_start)
    cleanup_acc_directory()
    _log(
        f"Run finished completed={completed} failed={failed} "
        f"elapsed={time.monotonic() - run_clock:.1f}s"
    )
    return {
        "practices": practices,
        "completed": completed,
        "failed": failed,
        "failed_details": failed_details,
    }


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
