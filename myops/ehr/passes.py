"""
Pipeline passes. Each pass consumes work resolved by ehr.query.select_appointments
so scope (daily / backfill / target) is decided in exactly one place.

Ported so far:
  pass_notes  — dashboard notes reader (two-phase, scroll-safe) + charge-badge capture

The Playwright internals are moved verbatim from tebra_rpa.py's
scrape_dashboard_notes (the working two-phase reader); the only changes are:
  - work is selected via select_appointments(cur, sel, 'notes')
  - the dashboard card scan now also records charge_status (the "Charge in
    billing" badge) for every matched appointment, independent of note status
"""

from collections import defaultdict
from datetime import timedelta

from .db import get_ehr_connection
from .query import select_appointments
from .matching import name_key, find_name_match, to_date_obj
from .config import TABLE_NAME, PATIENTS_TABLE, DOWNLOAD_DIR
from .browser import (
    apply_date_filter, wait_for_grid_settled,
    find_row_by_appt_id_with_scroll, click_patient_row, scrape_tebra_patient_id,
    ensure_worklist_filters_checked,
)
import os
import re

CARD_SEL = "[data-testid^='appointment-list-item']"
MAX_SCROLL_PASSES = 80
SIGNED_SENTINEL = "[SIGNED - no note text in drawer]"


def pass_notes(page, sel):
    """
    Navigate the scheduling dashboard for each date that has appointments
    needing notes, read the Finished tab, and:
      - mark signed appointments eligible (appt_note = sentinel)
      - record charge_status ("Charge in billing" etc.) for every matched
        appointment, whether or not its note is signed

    `page` is assumed already logged in and on the target practice.
    """
    conn = get_ehr_connection()
    cur = conn.cursor()
    try:
        rows = select_appointments(cur, sel, "notes")
        if not rows:
            print("[NOTES] No appointments need notes. Skipping.")
            return
        print(f"[NOTES] {len(rows)} appointments need notes")

        # rows columns: id, appt_id, patient_name, dob, appt_status,
        #               appt_date, appt_time, charge_status
        grouped = defaultdict(list)
        for r in rows:
            grouped[to_date_obj(r[5])].append(r)

        _ensure_dashboard_filters(page, sorted(grouped.keys())[0])

        for appt_day, day_rows in sorted(grouped.items()):
            date_str = appt_day.strftime("%Y-%m-%d")
            print(f"[NOTES] Dashboard date: {date_str} ({len(day_rows)} appointments)")

            if not _open_finished_tab(page, date_str):
                continue

            # Build lookup by sorted name key.
            #   value: (db_id, appt_id, patient_name, appt_status)
            needed = {}
            for r in day_rows:
                db_id, appt_id, patient_name, _dob, appt_status = r[0], r[1], r[2], r[3], r[4]
                needed[name_key(patient_name)] = (db_id, appt_id, patient_name, appt_status)

            collected = _collect_cards(page, date_str)  # {name_key: {"signed","charge"}}

            unsigned = _mark_from_cards(cur, conn, collected, needed)

            if needed:
                # `needed` now = matched-but-unsigned + genuinely-absent.
                absent = {k: v for k, v in needed.items() if k not in unsigned}
                if unsigned:
                    print(f"[NOTES] {len(unsigned)} present but NOT signed yet for {date_str} "
                          f"(correct skip):")
                    for k in unsigned:
                        _id, appt_id, pname, _ = needed[k]
                        print(f"  - {appt_id} {pname} (unsigned)")
                if absent:
                    print(f"[NOTES] {len(absent)} not found in Finished tab for {date_str} "
                          f"(no card — cancelled/no-show/name mismatch):")
                    for k, (db_id, appt_id, pname, _) in absent.items():
                        print(f"  - {appt_id} {pname}")
    finally:
        cur.close()
        conn.close()


# --------------------------------------------------------------------------
# internals (moved from scrape_dashboard_notes)
# --------------------------------------------------------------------------

def _ensure_dashboard_filters(page, first_day):
    """Check all filter groups once so every appointment is visible."""
    first_date = first_day.strftime("%Y-%m-%d")
    page.goto(f"https://app.kareo.com/v2/#/scheduling/dashboard/day/{first_date}")
    page.wait_for_load_state("domcontentloaded")
    try:
        page.wait_for_selector("button[role='tab']", timeout=10_000)
    except Exception:
        pass
    page.wait_for_timeout(500)

    for group_name in ["Providers", "Staff", "Rooms", "Service Locations"]:
        group = page.locator(f"[data-testid='{group_name}-checkbox-group']")
        if group.count() == 0:
            continue
        parent_cb = group.locator("input[type='checkbox']").first
        if parent_cb.count() and not parent_cb.is_checked():
            parent_cb.click(force=True)
            print(f"[NOTES] Checked filter: {group_name}")
            page.wait_for_timeout(200)


def _dismiss_stray_overlays(page):
    """
    Close any leftover header patient-search dropdown (Tebra's own always-
    mounted @tebra/navigation-ui widget, outside our control). goto() calls
    in this flow only change the URL fragment, so the single-spa shell never
    remounts and a stray open overlay from earlier in this page's session
    can sit on top of the page and intercept clicks (e.g. the "Finished" tab
    button underneath it) for the full default timeout. Same idea as
    browser.py's _close_filters_if_open, ported to this separate Dashboard
    flow which had no equivalent guard.
    """
    try:
        overlay = page.locator("[data-testid^='patient-search-option-label']")
        if overlay.count():
            page.keyboard.press("Escape")
            page.mouse.click(5, 5)  # neutral point, outside the search box
            overlay.first.wait_for(state="hidden", timeout=3_000)
    except Exception:
        pass


def _open_finished_tab(page, date_str):
    """Navigate to the date's dashboard and click the Finished tab."""
    page.goto("https://app.kareo.com/v2/#/scheduling/dashboard")
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(300)

    page.goto(f"https://app.kareo.com/v2/#/scheduling/dashboard/day/{date_str}")
    page.wait_for_load_state("domcontentloaded")
    try:
        page.wait_for_selector("button[role='tab']", timeout=10_000)
    except Exception:
        print(f"[NOTES] Dashboard didn't load for {date_str}, skipping")
        return False
    page.wait_for_timeout(500)

    finished_tab = page.locator("button[role='tab']:has-text('Finished')")
    if finished_tab.count() == 0:
        print(f"[NOTES] No 'Finished' tab for {date_str}, skipping")
        return False
    _dismiss_stray_overlays(page)
    try:
        finished_tab.first.click(timeout=5_000)
    except Exception:
        # One more shot: dismiss again (the overlay can re-render) and force it.
        _dismiss_stray_overlays(page)
        finished_tab.first.click(force=True)

    try:
        page.wait_for_selector(CARD_SEL, timeout=8_000)
    except Exception:
        print(f"[NOTES] No cards loaded in Finished tab for {date_str}, skipping")
        return False
    page.wait_for_timeout(300)
    return True


def _collect_cards(page, date_str):
    """
    Phase 1 — scroll the whole Finished list WITHOUT clicking (clicking opens a
    drawer and resets scroll, stranding lower cards). Collect each card's
    signed flag and charge badge. Returns {name_key: {"signed": bool,
    "charge": str|None}}.
    """
    collected = {}
    stable = 0
    for _ in range(MAX_SCROLL_PASSES):
        cards = page.locator(CARD_SEL)
        n = cards.count()
        new_this_pass = 0
        for i in range(n):
            try:
                card = cards.nth(i)
                card_text = card.inner_text().strip()
                link = card.locator("a[data-testid='patient-link']")
                card_patient = (
                    link.first.inner_text().strip() if link.count() else card_text
                )
                key = name_key(card_patient)
                is_new = key not in collected
                if not is_new and collected[key]["signed"]:
                    # Already confirmed signed on an earlier pass — no need to
                    # re-read (the signed badge doesn't un-sign itself).
                    continue
                collected[key] = {
                    "signed": "Note Signed" in card_text,
                    "charge": _charge_badge(card_text),
                }
                if is_new:
                    new_this_pass += 1
            except Exception as e:
                print(f"[NOTES ERROR] collect card: {e}")

        stable = stable + 1 if new_this_pass == 0 else 0
        if stable >= 3:
            break

        try:
            cards = page.locator(CARD_SEL)
            cnt = cards.count()
            if cnt:
                cards.nth(cnt - 1).scroll_into_view_if_needed(timeout=3_000)
        except Exception:
            pass
        page.wait_for_timeout(300)

    print(f"[NOTES] {date_str}: read {len(collected)} finished cards")
    return collected


def _charge_badge(card_text):
    """Extract the charge badge text from a card, or None."""
    for label in ("Charge in billing", "Charge not started"):
        if label in card_text:
            return label
    return None


def _mark_from_cards(cur, conn, collected, needed):
    """
    Phase 2 — for each collected card matched to a needed appointment:
      - always record charge_status (charge is independent of note status)
      - if the note is signed, set appt_note (sentinel) and remove from `needed`
      - if matched but unsigned, record it separately so the caller can report
        "present but unsigned" distinctly from "no card found"
    Returns the set of matched-but-unsigned name keys.
    """
    unsigned = set()
    for card_key, info in collected.items():
        if not needed:
            break
        matched_key = find_name_match(card_key, needed)
        if not matched_key:
            continue

        db_id, appt_id, patient_name, appt_status = needed[matched_key]

        # Charge status — recorded regardless of note state (covers the
        # note-not-started-but-charge-in-billing case).
        if info["charge"] is not None:
            try:
                cur.execute(
                    f"UPDATE {TABLE_NAME} SET charge_status=%s, updated_date=now() WHERE id=%s",
                    (info["charge"], db_id),
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"[NOTES ERROR] charge_status {appt_id}: {e}")

        if not info["signed"]:
            # Present but the note isn't signed yet — a correct skip, not a miss.
            unsigned.add(matched_key)
            continue

        try:
            if appt_status != "Checked Out":
                cur.execute(
                    f"""
                    UPDATE {TABLE_NAME}
                    SET appt_note=%s, appt_status='Checked Out',
                        retry_flag=0, retry_reason=NULL, updated_date=now()
                    WHERE id=%s
                    """,
                    (SIGNED_SENTINEL, db_id),
                )
            else:
                cur.execute(
                    f"UPDATE {TABLE_NAME} SET appt_note=%s, updated_date=now() WHERE id=%s",
                    (SIGNED_SENTINEL, db_id),
                )
            conn.commit()
            print(f"[NOTES] Marked {appt_id} ({patient_name}) eligible via signed-badge")
            del needed[matched_key]
        except Exception as e:
            conn.rollback()
            print(f"[NOTES ERROR] mark {appt_id}: {e}")

    return unsigned


# =========================================================
# pass_facesheets — unified facesheet download
# =========================================================
# Collapses run_facesheets (daily 2A/2B/2C) + run_facesheets_backfill into one
# per-patient-deduped pass driven by select_appointments. Runs TWO selections:
#   1. "facesheets"     — signed + unprocessed (blank/Error)     -> download
#   2. "missed_charges" — Tebra-flagged re-download              -> download
# Per-patient dedup: one download marks all of a patient's appointments.
# Per-patient failure recovery: one bad patient never aborts the practice.

def pass_facesheets(page, context, sel):
    conn = get_ehr_connection()
    cur = conn.cursor()
    try:
        # ---- normal signed+unprocessed selection ----
        rows = select_appointments(cur, sel, "facesheets")
        print(f"[FS] {len(rows)} signed-note appointment rows need facesheets")
        _process_by_patient(page, context, cur, conn, rows, "FS", keep_retry=False)

        # ---- Missed Charges re-download selection ----
        mc_rows = select_appointments(cur, sel, "missed_charges")
        print(f"[FS] {len(mc_rows)} 'Missed Charges' rows to re-download")
        # keep_retry=True: a re-download must NOT clear the Missed-Charges flag
        # unless this missed-charges pass itself handled it — we clear it
        # explicitly on success below.
        _process_by_patient(page, context, cur, conn, mc_rows, "MISSED_CHARGES",
                             keep_retry=True, clear_on_success=True)
    finally:
        cur.close()
        conn.close()


def _reset_to_grid(page):
    """Return to the appointments worklist grid (All Appointments view)."""
    page.goto("https://app.kareo.com/v2/#/worklist/appointments")
    wait_for_grid_settled(page)
    page.locator("[data-testid='tree-option-All Appointments']").click()
    wait_for_grid_settled(page)
    ensure_worklist_filters_checked(page)


def _process_by_patient(page, context, cur, conn, rows, phase,
                        keep_retry, clear_on_success=False):
    """
    Group `rows` by patient, download each patient's facesheet once, and mark
    every one of that patient's appointment rows Processed. `rows` columns:
    id, appt_id, patient_name, dob, appt_status, appt_date, appt_time, charge_status.
    """
    if not rows:
        return

    by_patient = defaultdict(list)
    for r in rows:
        db_id, appt_id, patient_name, appt_date = r[0], r[1], r[2], r[5]
        by_patient[patient_name].append((db_id, appt_id, appt_date))

    print(f"[{phase}] {len(by_patient)} unique patients to process")
    _reset_to_grid(page)

    for idx, (patient_name, appts) in enumerate(by_patient.items(), 1):
        appts.sort(key=lambda x: x[2])
        _primary_db_id, primary_appt_id, primary_appt_date = appts[0]
        all_db_ids = [a[0] for a in appts]
        appt_day = to_date_obj(primary_appt_date)

        print(f"[{phase}] [{idx}/{len(by_patient)}] {patient_name} "
              f"({len(all_db_ids)} appts, using {primary_appt_id} on {appt_day})")

        # One in-run retry: a single stray patient (stale page from the prior
        # patient's cleanup, a slow render, etc.) shouldn't cost a whole
        # extra daily sweep. Force a hard reset to the grid before retrying
        # so the retry isn't fighting the same stale state that caused the
        # first failure.
        last_err = None
        for attempt in (1, 2):
            try:
                if attempt == 2:
                    print(f"[{phase}] [{idx}] {patient_name} retrying "
                          f"after reset (attempt {attempt})")
                    _reset_to_grid(page)
                # Confirmed live against Tebra (2026-09-02): a single-day
                # (start==end) Worklist date filter can silently drop a real,
                # in-range, signed appointment from the grid -- reproduced
                # for TORCHON, CAMILLE (appt 1832, 2026-07-29): a fresh
                # single-day 7/29-7/29 filter rendered only 1 row and never
                # showed her, even though she's clearly there in Tebra's own
                # Dashboard view and in a wider Worklist range (7/25-7/31).
                # Padding the end date forward by one day reliably surfaced
                # her row in the same live test; padding the start date
                # backward instead did NOT. This is a Tebra-side filter bug,
                # not a checkbox/provider-visibility issue -- the earlier
                # ensure_worklist_filters_checked theory didn't hold up under
                # live testing. find_row_by_appt_id_with_scroll still matches
                # by exact appt_id, so the extra day's rows are harmless.
                apply_date_filter(page, appt_day, appt_day + timedelta(days=1))
                ok = _download_and_mark(
                    page, context, cur, conn,
                    primary_appt_id, patient_name, all_db_ids, phase, keep_retry,
                )
                if ok and clear_on_success:
                    _clear_retry(cur, conn, all_db_ids)
                last_err = None
                break
            except Exception as e:
                last_err = e
                print(f"[{phase}] [{idx}] {patient_name} failed "
                      f"(attempt {attempt}): {e!r}")
                try:
                    conn.rollback()
                except Exception:
                    pass

        if last_err is not None:
            print(f"[{phase}] [{idx}] {patient_name} failed after retry, "
                  f"recovering: {last_err!r}")
            try:
                cur.execute(
                    f"""
                    UPDATE {TABLE_NAME}
                    SET process_status='Error', process_error_stage=%s,
                        process_error_message=%s, updated_date=now()
                    WHERE id = ANY(%s)
                    """,
                    (phase, str(last_err)[:500], all_db_ids),
                )
                conn.commit()
            except Exception:
                conn.rollback()
            try:
                _reset_to_grid(page)
            except Exception as re_:
                print(f"[{phase}] page recovery failed: {re_!r}")


def _download_and_mark(page, context, cur, conn,
                       appt_id, patient_name, all_db_ids, phase, keep_retry):
    """
    Open one appointment for the patient, download the facesheet PDF once to
    local disk ({facesheet_id}_{last_name}.pdf), and mark every db_id Processed.
    Returns True on success, False on handled failure.
    """
    fs = None
    opened_new_tab = False
    try:
        row = find_row_by_appt_id_with_scroll(page, appt_id)
        if not row:
            raise RuntimeError("No matching row found in grid")

        fs, opened_new_tab = click_patient_row(page, row)

        m = re.search(r"/Facesheet/(\d+)", fs.url)
        if not m:
            raise RuntimeError("Facesheet ID not found in URL")
        facesheet_id = m.group(1)

        tebra_patient_id = scrape_tebra_patient_id(fs)

        pdf_url = f"https://app.kareo.com/patients/print/{facesheet_id}.pdf"
        last_name = patient_name.split(",")[0].strip().replace(" ", "_")
        pdf_path = os.path.join(DOWNLOAD_DIR, f"{facesheet_id}_{last_name}.pdf")

        resp = context.request.get(pdf_url)
        if resp.status != 200:
            raise RuntimeError(f"PDF download failed: HTTP {resp.status}")
        with open(pdf_path, "wb") as f:
            f.write(resp.body())

        # Mark all of this patient's appointment rows Processed. keep_retry
        # leaves retry_flag/retry_reason untouched (Missed-Charges rows are
        # cleared explicitly by the caller on success); otherwise clear them.
        if keep_retry:
            cur.execute(
                f"""
                UPDATE {TABLE_NAME}
                SET tebra_facesheet_id = COALESCE(%s, tebra_facesheet_id),
                    patient_id = COALESCE(%s, patient_id),
                    process_status = 'Processed',
                    process_error_stage = NULL, process_error_message = NULL,
                    updated_date = now()
                WHERE id = ANY(%s)
                """,
                (facesheet_id, tebra_patient_id, all_db_ids),
            )
        else:
            cur.execute(
                f"""
                UPDATE {TABLE_NAME}
                SET tebra_facesheet_id = COALESCE(%s, tebra_facesheet_id),
                    patient_id = COALESCE(%s, patient_id),
                    process_status = 'Processed',
                    process_error_stage = NULL, process_error_message = NULL,
                    retry_flag = 0, retry_reason = NULL,
                    updated_date = now()
                WHERE id = ANY(%s)
                """,
                (facesheet_id, tebra_patient_id, all_db_ids),
            )
        conn.commit()
        return True

    except Exception as e:
        print(f"[ERROR-{phase}] {appt_id} {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        cur.execute(
            f"""
            UPDATE {TABLE_NAME}
            SET process_status='Error', process_error_stage=%s,
                process_error_message=%s, updated_date=now()
            WHERE id = ANY(%s)
            """,
            (phase, str(e)[:500], all_db_ids),
        )
        conn.commit()
        return False

    finally:
        try:
            if opened_new_tab and fs is not None:
                fs.close()
            else:
                page.go_back()
                wait_for_grid_settled(page)
        except Exception as cleanup_err:
            # Don't swallow this: if go_back()/settle fails, `page` is left
            # stranded on the Facesheet route with no "Table filters" button,
            # which cascades into the *next* patient's apply_date_filter
            # failing with a confusing 30s locator timeout. Log it and force
            # a real reset back to the grid so the next patient isn't
            # silently dropped too.
            print(f"[{phase}] cleanup after {patient_name} failed, "
                  f"forcing grid reset: {cleanup_err!r}")
            try:
                _reset_to_grid(page)
            except Exception as reset_err:
                print(f"[{phase}] grid reset after cleanup failure also "
                      f"failed: {reset_err!r}")


def _clear_retry(cur, conn, db_ids):
    cur.execute(
        f"UPDATE {TABLE_NAME} SET retry_flag=0, retry_reason=NULL, updated_date=now() "
        f"WHERE id = ANY(%s)",
        (db_ids,),
    )
    conn.commit()


# =========================================================
# pass_appointments — scrape worklist + flag Missed Charges
# =========================================================
import json  # noqa: E402
from .db import upsert_appointment, set_missed_charges  # noqa: E402
from .browser import scrape_virtual_grid, cell  # noqa: E402


def pass_appointments(page, sel, practice_name, from_date, to_date):
    """
    Scrape the appointments worklist for [from_date, to_date] into the DB, then
    flag Tebra's 'Missed Charges' view rows for facesheet re-download.
    Only used for daily/backfill (target/one-off operates on existing rows).
    `page` is assumed logged in and on the practice.
    """
    from .session import goto_worklist  # local import avoids cycle
    conn = get_ehr_connection()
    cur = conn.cursor()
    try:
        goto_worklist(page)
        # Without this, appointments whose Provider/Staff/Room/Service
        # Location checkbox isn't checked by default are entirely invisible
        # to the grid scrape below -- not blank, just never seen at all. See
        # ensure_worklist_filters_checked's docstring.
        ensure_worklist_filters_checked(page)
        apply_date_filter(page, from_date, to_date)

        def extract(row):
            status = cell(row, "APPOINTMENT_STATUS")
            return {
                "appt_id": cell(row, "APPOINTMENT_ID"),
                "appt_date": cell(row, "START_DATE"),
                "appt_time": cell(row, "START_TIME"),
                "patient_name": cell(row, "PATIENT_NAME"),
                "dob": cell(row, "PATIENT_DOB"),
                "home_phone": cell(row, "PATIENT_HOME_PHOME"),
                "mobile_phone": cell(row, "PATIENT_MOBILE_PHONE"),
                "provider_name": cell(row, "PROVIDER_NAME"),
                "service_location": cell(row, "SERVICE_LOCATION_NAME"),
                "appt_reason": cell(row, "APPOINTMENT_REASON"),
                "appt_status": status,
                "retry_flag": 0 if status == "Checked Out" else 1,
                "retry_reason": None,
                "entity": sel.entity,
                "sub_entity": sel.sub_entity,
                "practice": practice_name,
                "ehr_name": sel.ehr_name,
            }

        appts = scrape_virtual_grid(page, extract)
        print(f"[APPTS] scraped={len(appts)}")
        for rec in appts.values():
            upsert_appointment(cur, rec)
        conn.commit()

        # Missed Charges flagging (Tebra's own view).
        page.locator("[data-testid='tree-option-Missed Charges']").click()
        wait_for_grid_settled(page)
        apply_date_filter(page, from_date, to_date)
        missed = scrape_virtual_grid(page, lambda r: {"appt_id": cell(r, "APPOINTMENT_ID")})
        set_missed_charges(cur, list(missed.keys()), sel.entity, sel.sub_entity, sel.ehr_name)
        conn.commit()
    finally:
        cur.close()
        conn.close()


# =========================================================
# pass_charges — VIEW CHARGE scrape into charge_data
# =========================================================
from .charges import open_charge_capture, scrape_charge_capture  # noqa: E402


def pass_charges(page, sel):
    """
    For appointments with charge_status='Charge in billing' and no charge_data
    yet, open the drawer -> Financial -> VIEW CHARGE, scrape dx + procedure
    codes into charge_data (jsonb). Runs every mode.
    `page` is assumed logged in and on the practice.
    """
    conn = get_ehr_connection()
    cur = conn.cursor()
    try:
        rows = select_appointments(cur, sel, "charges")
        if not rows:
            print("[CHARGES] No charge-in-billing appointments to scrape. Skipping.")
            return
        print(f"[CHARGES] {len(rows)} appointments need charge capture")

        grouped = defaultdict(list)
        for r in rows:
            grouped[to_date_obj(r[5])].append(r)

        # Select all filter groups (Providers/Staff/Rooms/Service Locations)
        # once, so every card is visible in the Finished tab — same guard as
        # pass_notes. Without this, charge cards can be hidden and silently
        # missed on a charges-only run.
        _ensure_dashboard_filters(page, sorted(grouped.keys())[0])

        for appt_day, day_rows in sorted(grouped.items()):
            date_str = appt_day.strftime("%Y-%m-%d")
            if not _open_finished_tab(page, date_str):
                continue

            # Match cards by name to the day's needed charge rows.
            needed = {}
            for r in day_rows:
                needed[name_key(r[2])] = (r[0], r[1], r[2])  # db_id, appt_id, name

            # Find each needed patient's card, open drawer, scrape charge.
            cards = page.locator(CARD_SEL)
            # Build a name_key -> card index map by scanning once.
            index_by_key = {}
            for i in range(cards.count()):
                try:
                    card = cards.nth(i)
                    link = card.locator("a[data-testid='patient-link']")
                    txt = link.first.inner_text().strip() if link.count() else card.inner_text().strip()
                    index_by_key[name_key(txt)] = i
                except Exception:
                    continue

            for card_key, (db_id, appt_id, pname) in list(needed.items()):
                match = find_name_match(card_key, index_by_key) if index_by_key else None
                if match is None:
                    continue
                try:
                    cards = page.locator(CARD_SEL)
                    cards.nth(index_by_key[match]).click()
                    page.wait_for_timeout(400)
                    if open_charge_capture(page):
                        data = scrape_charge_capture(page)
                        if data:
                            cur.execute(
                                f"UPDATE {TABLE_NAME} SET charge_data=%s, updated_date=now() WHERE id=%s",
                                (json.dumps(data), db_id),
                            )
                            conn.commit()
                            print(f"[CHARGES] Captured charge for {appt_id} ({pname})")
                        page.go_back()
                        page.wait_for_timeout(500)
                    # Re-open the finished tab for the next patient (drawer/nav reset).
                    _open_finished_tab(page, date_str)
                except Exception as e:
                    print(f"[CHARGES ERROR] {appt_id}: {e}")
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    _open_finished_tab(page, date_str)
    finally:
        cur.close()
        conn.close()


# =========================================================
# pass_patient_match — reconcile patient_id against ehr_patients
# =========================================================

def pass_patient_match(sel, practice_name, from_date, to_date):
    """Flag patient_match for the practice/window against ehr.ehr_patients."""
    conn = get_ehr_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            UPDATE {TABLE_NAME} a
            SET patient_match = (
                    CASE
                        WHEN a.patient_id IS NULL THEN NULL
                        WHEN EXISTS (
                            SELECT 1 FROM {PATIENTS_TABLE} p
                            WHERE p.ehr_name = a.ehr_name
                              AND p.entity = a.entity
                              AND p.sub_entity = a.sub_entity
                              AND p.patient_id = a.patient_id
                              AND p.effective_end_date IS NULL
                        ) THEN TRUE
                        ELSE FALSE
                    END
                ),
                updated_date = now()
            WHERE a.entity=%s AND a.sub_entity=%s AND a.ehr_name=%s
              AND a.practice=%s AND a.appt_date BETWEEN %s AND %s
            """,
            (sel.entity, sel.sub_entity, sel.ehr_name, practice_name, from_date, to_date),
        )
        conn.commit()
        cur.execute(
            f"""
            SELECT COUNT(*) FILTER (WHERE patient_match IS TRUE),
                   COUNT(*) FILTER (WHERE patient_match IS FALSE),
                   COUNT(*) FILTER (WHERE patient_match IS NULL)
            FROM {TABLE_NAME}
            WHERE entity=%s AND sub_entity=%s AND ehr_name=%s
              AND practice=%s AND appt_date BETWEEN %s AND %s
            """,
            (sel.entity, sel.sub_entity, sel.ehr_name, practice_name, from_date, to_date),
        )
        matched, unmatched, pending = cur.fetchone()
        print(f"[PATIENT-MATCH] matched={matched} unmatched={unmatched} pending={pending}")
    finally:
        cur.close()
        conn.close()
