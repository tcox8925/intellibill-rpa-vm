"""Encounter discovery and Print Chart section/notes selection UI driving."""

import hashlib
import re
import time
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from playwright.sync_api import Locator, Page

from pf_sync_pkg.constants import DEFAULT_TIMEOUT, EHR_BASE_URL, SHORT_TIMEOUT
from pf_sync_pkg.dom_utils import first_visible_locator, require_visible_locator, visible_match
from pf_sync_pkg.models import (
    DetectedEncounter,
    EncounterNotFoundError,
    QueueRecord,
    SoapNoteNotFoundError,
    SyncConfig,
)
from pf_sync_pkg.utils import clean, parse_date, require_date, safe_filename


def patient_summary_url(patient_guid: str) -> str:
    return f"{EHR_BASE_URL}#/PF/charts/patients/{patient_guid}/summary"


def checkbox_state_is(box: Locator, desired: bool) -> bool:
    try:
        return box.is_checked() == desired
    except Exception:
        return False


def checkbox_click_targets(box: Locator) -> List[Tuple[str, Locator]]:
    """Clickable stand-ins for a visually hidden checkbox input, best first."""
    targets: List[Tuple[str, Locator]] = []
    for name, selector in (
        ("sibling label", "xpath=following-sibling::label[1]"),
        ("parent label", "xpath=../label"),
        ("parent container", "xpath=.."),
    ):
        try:
            candidate = box.locator(selector).first
            if candidate.count() and candidate.is_visible():
                targets.append((name, candidate))
        except Exception:
            continue
    return targets


def checkbox_is_interactive(box: Locator) -> bool:
    """True when the box can be driven, whether or not the input itself renders."""
    try:
        if box.is_visible():
            return True
    except Exception:
        pass
    return bool(checkbox_click_targets(box))


def set_checkbox_state(box: Locator, desired: bool, label: str = "checkbox") -> None:
    """Force a styled checkbox into the desired state and verify it took.

    v5.6: Practice Fusion renders `check-box__input` inputs that are visually hidden and
    draws the control on the associated `check-box__label`. Playwright's check()/click()
    require visibility, so every section click failed with "element is not visible" even
    though the input resolved correctly.

    Clicking the label is not universally safe either: the notes row nests the dropdown
    toggle anchor INSIDE its label, so a center-click on that label opens the dropdown
    instead of toggling the group. So the input itself is driven first with force=True,
    then the label near its left edge where the glyph is drawn, then the center, then the
    DOM property with input/change events for Ember to observe. State is re-read after
    every attempt and the first success returns, so nothing is ever toggled twice.
    """
    box.wait_for(state="attached", timeout=DEFAULT_TIMEOUT)
    if checkbox_state_is(box, desired):
        return

    attempts: List[str] = []

    try:
        if desired:
            box.check(force=True, timeout=SHORT_TIMEOUT)
        else:
            box.uncheck(force=True, timeout=SHORT_TIMEOUT)
        if checkbox_state_is(box, desired):
            return
        attempts.append("force check/uncheck on input: state unchanged")
    except Exception as exc:
        attempts.append(f"force check/uncheck on input: {type(exc).__name__}")

    for name, target in checkbox_click_targets(box):
        # The glyph is drawn at the label's left edge; the center may be a nested link.
        for description, position in (("glyph", {"x": 6, "y": 8}), ("center", None)):
            try:
                if position is not None:
                    target.click(position=position, timeout=SHORT_TIMEOUT)
                else:
                    target.click(timeout=SHORT_TIMEOUT)
                if checkbox_state_is(box, desired):
                    return
                attempts.append(f"{name} {description} click: state unchanged")
            except Exception as exc:
                attempts.append(f"{name} {description} click: {type(exc).__name__}")

    try:
        box.evaluate(
            """
            (el, want) => {
                if (el.checked !== want) {
                    el.checked = want;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.dispatchEvent(new Event('click', {bubbles: true}));
                }
            }
            """,
            desired,
        )
        if checkbox_state_is(box, desired):
            return
        attempts.append("DOM property + input/change events: state unchanged")
    except Exception as exc:
        attempts.append(f"DOM property + input/change events: {type(exc).__name__}")

    raise RuntimeError(
        f"CHECKBOX_NOT_SETTABLE: could not set {label} to checked={desired}. "
        "Tried -> " + "; ".join(attempts)
    )


def ensure_checked(locator: Locator, label: str = "checkbox") -> None:
    set_checkbox_state(locator, True, label)


def ensure_unchecked(locator: Locator, label: str = "checkbox") -> None:
    set_checkbox_state(locator, False, label)


def locator_text(locator: Locator) -> str:
    try:
        return clean(locator.inner_text())
    except Exception:
        try:
            return clean(locator.text_content() or "")
        except Exception:
            return ""


def _scrape_visible_encounters(
    page: Page,
    config: SyncConfig,
    patient_guid: str,
    item_selector: str,
    source: str,
) -> List[DetectedEncounter]:
    """One-shot pass over whatever encounter items are CURRENTLY in the DOM --
    read_encounters (below) is the one that also scrolls to reveal more.

    Summary and Timeline use two DIFFERENT DOM shapes for an encounter row
    (confirmed live 2026-08-21 -- see the Timeline selectors' own comments in
    models.py), so which selector set to read date/type/code/complaint from
    is picked by `source` here rather than always using the Summary ones.
    """
    if source == "timeline":
        date_selector = config.encounter_timeline_date_selector
        type_selector = config.encounter_timeline_type_selector
        code_selector = config.encounter_timeline_code_selector
        complaint_selector = config.encounter_timeline_chief_complaint_selector
    else:
        date_selector = config.encounter_date_selector
        type_selector = config.encounter_type_selector
        code_selector = config.encounter_code_selector
        complaint_selector = config.encounter_chief_complaint_selector

    items = page.locator(item_selector)
    output: List[DetectedEncounter] = []
    for index in range(items.count()):
        item = items.nth(index)
        try:
            title = clean(item.get_attribute("title") or "")
            display = locator_text(item)
        except Exception:
            continue
        soap_marker = clean(config.encounter_soap_title_text).lower()
        if soap_marker and soap_marker not in f"{title} {display}".lower():
            continue
        encounter_date_text = locator_text(item.locator(date_selector).first)
        encounter_date = parse_date(encounter_date_text)
        if encounter_date is None:
            continue
        encounter_type = locator_text(item.locator(type_selector).first)
        encounter_code = (
            locator_text(item.locator(code_selector).first) if code_selector else ""
        )
        complaint = locator_text(item.locator(complaint_selector).first)
        key_source = "|".join(
            [
                patient_guid.lower(),
                encounter_date.isoformat(),
                encounter_type.lower(),
                encounter_code.lower(),
                complaint.lower(),
            ]
        )
        output.append(
            DetectedEncounter(
                encounter_date=encounter_date.isoformat(),
                encounter_type=encounter_type,
                encounter_code=encounter_code,
                chief_complaint=complaint,
                display_text=display,
                encounter_key=hashlib.sha256(key_source.encode("utf-8")).hexdigest(),
                source=source,
            )
        )
    return output


def read_encounters(
    page: Page,
    config: SyncConfig,
    patient_guid: str,
    item_selector: str,
    source: str,
) -> List[DetectedEncounter]:
    """Reads every encounter item, scrolling config.encounter_list_scroller_selector
    first (if it turns out to be scrollable) so a patient with enough visit
    history isn't silently under-enumerated the same way the Schedule table
    and the notes dropdown were confirmed to be before their own fixes -- see
    ScheduleScrapeConfig's docstring and for_each_note_checkbox_scrolled.
    NOT independently confirmed live for this specific list either way --
    added defensively after a real live report of an encounter that exists
    in the chart but was not found despite checking both Summary and Timeline
    (see find_encounter_for_appointment_with_timeline_fallback's caller).

    Falls straight through to a single _scrape_visible_encounters pass if no
    scrollable container is found -- zero behavior change for that case.
    Dedup across scroll steps is by encounter_key (already a hash of
    patient_guid+date+type+code+complaint), so the same encounter staying in
    view across steps is never double-counted, and this never needs its own
    separate identity check the way the Schedule-row/note-checkbox scrolls did.
    """
    try:
        page.locator(item_selector).first.wait_for(state="attached", timeout=10_000)
    except Exception:
        pass

    collected: Dict[str, DetectedEncounter] = {}

    def collect_current() -> None:
        for encounter in _scrape_visible_encounters(page, config, patient_guid, item_selector, source):
            collected[encounter.encounter_key] = encounter

    collect_current()

    scroller = page.query_selector(config.encounter_list_scroller_selector)
    if scroller is None:
        return list(collected.values())
    try:
        scroll_height = scroller.evaluate("el => el.scrollHeight")
        client_height = scroller.evaluate("el => el.clientHeight")
    except Exception:
        return list(collected.values())
    if not scroll_height or not client_height or scroll_height <= client_height + 5:
        return list(collected.values())  # Not actually scrollable.

    try:
        scroller.evaluate("el => { el.scrollTop = 0; }")
        time.sleep(0.15)
    except Exception:
        return list(collected.values())

    stuck = 0
    for _ in range(60):
        try:
            old_top = scroller.evaluate("el => el.scrollTop")
            scroller.evaluate(
                "el => { el.scrollTop = el.scrollTop + Math.max(150, el.clientHeight * 0.6); }"
            )
            time.sleep(0.2)
            new_top = scroller.evaluate("el => el.scrollTop")
            max_top = scroller.evaluate("el => el.scrollHeight - el.clientHeight")
        except Exception:
            break
        collect_current()
        stuck = stuck + 1 if new_top == old_top else 0
        if stuck >= 2 or int(new_top) >= int(max_top) - 5:
            break

    return list(collected.values())


def read_summary_encounters(page: Page, config: SyncConfig, patient_guid: str) -> List[DetectedEncounter]:
    return read_encounters(
        page, config, patient_guid, config.encounter_item_selector, "summary"
    )


def open_all_encounters_timeline(
    page: Page, config: SyncConfig, patient_guid: str
) -> bool:
    link = page.locator(config.encounter_timeline_link_selector).first
    if link.count() == 0:
        return False
    href = clean(link.get_attribute("href") or "")
    if href.startswith("#"):
        page.goto(EHR_BASE_URL + href, wait_until="domcontentloaded")
    else:
        link.click()
    deadline = time.time() + 15
    while time.time() < deadline:
        if "/timeline/encounter" in (page.url or ""):
            return True
        time.sleep(0.2)
    return "/timeline/encounter" in (page.url or "")


def all_patient_encounters(
    page: Page,
    config: SyncConfig,
    patient_guid: str,
    include_timeline: bool = True,
) -> List[DetectedEncounter]:
    encounters = read_summary_encounters(page, config, patient_guid)
    if include_timeline and open_all_encounters_timeline(page, config, patient_guid):
        timeline = read_encounters(
            page,
            config,
            patient_guid,
            config.encounter_timeline_item_selector,
            "timeline",
        )
        by_key = {item.encounter_key: item for item in encounters}
        for item in timeline:
            by_key[item.encounter_key] = item
        encounters = list(by_key.values())
    encounters.sort(
        key=lambda item: parse_date(item.encounter_date) or date.min,
        reverse=True,
    )
    return encounters


def find_encounter_for_appointment(
    page: Page,
    config: SyncConfig,
    patient_guid: str,
    appointment_date: str,
) -> DetectedEncounter:
    """Find the appointment-date encounter from the patient Summary only.

    Nightly appointment processing must never navigate to Timeline as a fallback.
    Practice Fusion can leave the automation waiting on the Timeline route until a
    manual return to Summary. If the encounter is absent from Summary, the queue row
    is left ready/review for a later poll and no PDF is generated.
    """
    requested_date = require_date(appointment_date, "appointment date")
    matches = [
        item
        for item in read_summary_encounters(page, config, patient_guid)
        if parse_date(item.encounter_date) == requested_date
    ]
    if not matches:
        raise EncounterNotFoundError(
            "ENCOUNTER_NOT_FOUND_FOR_APPOINTMENT_DATE_AFTER_SUMMARY_CHECK: "
            f"appointment_date={appointment_date}"
        )
    return matches[0]


def find_encounter_for_appointment_with_timeline_fallback(
    page: Page,
    config: SyncConfig,
    patient_guid: str,
    appointment_date: str,
) -> DetectedEncounter:
    """Find the appointment-date encounter from Summary, falling back to Timeline.

    Used by full-sync-by-date where encounters may exist on Timeline but not yet
    synced to Summary. Checks Summary first (fast), then navigates to Timeline
    (slower) if Summary has no match.

    Unlike nightly (which never navigates to Timeline to avoid PF hangs), full-sync-by-date
    is explicitly date-scoped and tolerates the Timeline navigation overhead.
    """
    requested_date = require_date(appointment_date, "appointment date")

    # Try Summary first
    summary_matches = [
        item
        for item in read_summary_encounters(page, config, patient_guid)
        if parse_date(item.encounter_date) == requested_date
    ]
    if summary_matches:
        return summary_matches[0]

    # Fallback to Timeline if Summary is empty
    try:
        if open_all_encounters_timeline(page, config, patient_guid):
            timeline_encounters = read_encounters(
                page,
                config,
                patient_guid,
                config.encounter_timeline_item_selector,
                "timeline",
            )
            timeline_matches = [
                item
                for item in timeline_encounters
                if parse_date(item.encounter_date) == requested_date
            ]
            if timeline_matches:
                return timeline_matches[0]
    except Exception as exc:
        print(f"  [timeline fallback] Error opening Timeline: {exc}", flush=True)

    # Neither Summary nor Timeline had a match
    raise EncounterNotFoundError(
        "ENCOUNTER_NOT_FOUND_AFTER_SUMMARY_AND_TIMELINE_CHECK: "
        f"appointment_date={appointment_date}"
    )


def dismiss_stray_print_preview_modal(page: Page, config: SyncConfig) -> None:
    """Dismiss Practice Fusion's NATIVE print-preview overlay if a prior
    record's generate_pdf left it behind.

    Confirmed live 2026-08-21: PF is a hash-routed SPA, and navigating to a
    different chart's Summary page (page.goto to a new hash route) does not
    necessarily force a full document reload -- so this overlay (holding the
    print-encounter-modal-frame iframe) can still be sitting on top of the page
    for the NEXT record. Its iframe then intercepts pointer events, which is
    why the NEXT record's own Print Chart button click can time out even
    though that button itself reports visible/enabled -- the click lands on
    the stray iframe instead. Best-effort, like close_print_chart: Escape
    closes PF's own print dialogs; if it's somehow still there afterward, the
    caller's own click/timeout is the backstop, same as everywhere else in
    this file that dismisses leftover UI best-effort rather than hard-failing
    on it.
    """
    try:
        modal = page.query_selector(config.native_print_preview_modal_selector)
        if modal is None or not modal.is_visible():
            return
    except Exception:
        return
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
    except Exception:
        pass


def open_print_chart(page: Page, config: SyncConfig) -> Locator:
    """Click Print Chart and return the visible modal container."""
    # A prior record's native print-preview overlay can still be sitting on
    # top of the page -- see dismiss_stray_print_preview_modal's docstring --
    # and would otherwise intercept this exact click.
    dismiss_stray_print_preview_modal(page, config)
    button = page.locator(config.print_chart_button_selector).first
    button.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
    button.click()
    return require_visible_locator(
        page,
        config.print_modal_ready_selectors,
        DEFAULT_TIMEOUT,
        "Print Chart modal",
    )


def close_print_chart(page: Page, config: SyncConfig) -> None:
    """Dismiss the Print Chart modal if it is still open.

    Best effort by design -- navigation is still the primary reset. Tries Cancel before
    the header X, since Cancel is the documented dismissal control.
    """
    for selector in (config.print_modal_cancel_selector, config.print_modal_close_selector):
        if not clean(selector):
            continue
        try:
            control = visible_match(page, selector)
            if control is None:
                continue
            control.click()
            for _ in range(10):
                if first_visible_locator(page, config.print_modal_ready_selectors, 200) is None:
                    return
                time.sleep(0.2)
        except Exception:
            continue


def modal_checkbox_state(modal: Locator, config: SyncConfig) -> Dict[str, bool]:
    """Map every option checkbox in the modal to its checked state.

    Keyed by the owning data-element so mismatches can be reported in PF's own
    vocabulary rather than as opaque selectors. The notes group checkbox is excluded:
    notes are selected separately and per record.
    """
    state: Dict[str, bool] = {}
    try:
        boxes = modal.locator("input[type='checkbox']")
        count = boxes.count()
    except Exception:
        return state
    for index in range(count):
        box = boxes.nth(index)
        try:
            owner = box.evaluate(
                """
                el => {
                    const host = el.closest('[data-element]');
                    return host ? (host.getAttribute('data-element') || '') : '';
                }
                """
            )
            owner = clean(owner)
            if not owner:
                continue
            # Notes are selected separately and per record.
            if "notes-dropdown" in owner:
                continue
            state[owner] = bool(box.is_checked())
        except Exception:
            continue
    return state


def clear_all_facesheet_sections(page: Page, config: SyncConfig, modal: Locator) -> bool:
    """Clear every section using the modal's own 'none' link, falling back to unchecking."""
    link = visible_match(page, config.print_modal_select_none_selector)
    if link is not None:
        try:
            link.click()
            time.sleep(0.3)
            if not any(modal_checkbox_state(modal, config).values()):
                return True
        except Exception:
            pass
    # Fallback: uncheck whatever is currently checked.
    cleared = True
    for owner, checked in modal_checkbox_state(modal, config).items():
        if not checked:
            continue
        try:
            box = modal.locator(f"[data-element='{owner}'] input[type='checkbox']").first
            ensure_unchecked(box, owner)
        except Exception:
            cleared = False
    return cleared


def select_facesheet_sections(page: Page, config: SyncConfig, modal: Optional[Locator] = None) -> List[str]:
    """Select exactly the configured sections and nothing else.

    v5.5: this used to only CHECK the configured selectors, leaving anything PF had
    pre-selected -- or anything a previous record left behind -- silently included in the
    printed chart. The modal's own "Select: none" link now clears the form first, the
    configured set is checked, and the resulting state is verified against the intended
    set. An option PF adds in a future release therefore defaults to OFF and surfaces as a
    named mismatch instead of quietly appearing in patient charts.
    """
    if not config.facesheet_checkbox_selectors:
        raise RuntimeError("No Facesheet checkbox selectors are configured.")
    if modal is None:
        modal = require_visible_locator(
            page, config.print_modal_ready_selectors, SHORT_TIMEOUT, "Print Chart modal"
        )

    if not clear_all_facesheet_sections(page, config, modal):
        print("  WARNING: could not confirm all Print Chart sections were cleared.", flush=True)

    selected: List[str] = []
    for selector in config.facesheet_checkbox_selectors:
        if not clean(selector):
            continue
        checkbox = modal.locator(selector).first
        if checkbox.count() == 0:
            raise RuntimeError(f"Facesheet checkbox not found in the modal: {selector}")
        # Diagnostic timing added 2026-08-25: set_checkbox_state (called via
        # ensure_checked below) tries up to 5 fallback tiers, each with its
        # own SHORT_TIMEOUT=5s budget -- a checkbox that misses the first,
        # fast tier can silently cost several seconds before printing even
        # starts. This makes that visible per-checkbox instead of only as a
        # single opaque total.
        started = time.perf_counter()
        ensure_checked(checkbox, selector)
        elapsed = time.perf_counter() - started
        flag = " <-- SLOW" if elapsed > 1.0 else ""
        print(f"  [facesheet-checkbox] {selector} checked in {elapsed:.3f}s{flag}", flush=True)
        selected.append(selector)

    intended = set()
    for selector in config.facesheet_checkbox_selectors:
        match = re.search(r"data-element='([^']+)'", selector)
        if match:
            intended.add(match.group(1))

    state = modal_checkbox_state(modal, config)
    missing = sorted(owner for owner in intended if not state.get(owner, False))
    unexpected = sorted(owner for owner, checked in state.items() if checked and owner not in intended)
    if missing or unexpected:
        message = (
            "Print Chart section selection does not match the configured set. "
            f"expected_checked={sorted(intended)} missing={missing} unexpected_checked={unexpected}"
        )
        if config.enforce_exact_facesheet_selection:
            raise RuntimeError("FACESHEET_SELECTION_MISMATCH: " + message)
        print(f"  WARNING: {message}", flush=True)

    # Patient insurance carries its own filter dropdown (All insurance / Active insurance /
    # Inactive insurance) on the same row as its checkbox -- this is the "insurance active
    # check": left at PF's default ("All insurance"), the printed chart would include every
    # inactive/expired plan on file. Only relevant once the row itself is checked.
    if config.insurance_section_data_element in intended:
        select_insurance_active_filter(page, config)

    return selected


def insurance_filter_toggle_label(page: Page, config: SyncConfig) -> str:
    toggle = visible_match(page, config.insurance_filter_toggle_selector)
    if toggle is None:
        return ""
    return locator_text(toggle)


_INSURANCE_FILTER_LABELS = ("all insurance", "active insurance", "inactive insurance")


def find_insurance_filter_toggle_by_text(page: Page) -> Optional[Locator]:
    """Fall back to a page-wide scan for the insurance filter toggle by its own label text.

    v5.19 field failure: the toggle selector (scoped under print-insurance-options, mirroring
    how the Notes row nests its toggle under its own data-element) was never confirmed against
    the live DOM and did not find anything on the first real run, failing the whole record --
    see select_insurance_active_filter's enforce_insurance_active_filter=False default, added
    the same day, for why that no longer takes the note down with it. This is the second line
    of defense: the toggle's own rendered text is always one of "All/Active/Inactive insurance"
    regardless of where PF nests it in the DOM, so match on that instead of a guessed selector.
    """
    try:
        candidates = page.locator("[data-element='checkbox-dropdown-grouping__toggle']")
        count = candidates.count()
    except Exception:
        return None
    for index in range(count):
        candidate = candidates.nth(index)
        try:
            if not candidate.is_visible():
                continue
            text = clean(candidate.inner_text()).lower()
        except Exception:
            continue
        if any(label in text for label in _INSURANCE_FILTER_LABELS):
            return candidate
    return None


def _select_insurance_active_filter_unsafe(page: Page, config: SyncConfig) -> bool:
    """Do the actual dropdown-driving work. Any exception here (including a Playwright
    TimeoutError from a click) is caught by the public wrapper below -- see its docstring
    for why this must never be allowed to fail the SOAP note PDF by default.
    """
    toggle = first_visible_locator(page, [config.insurance_filter_toggle_selector], SHORT_TIMEOUT)
    if toggle is None:
        # Give PF a moment to render the dropdown after the checkbox click, then fall back to
        # a text-based scan in case the configured selector's assumed nesting is wrong.
        time.sleep(0.5)
        toggle = first_visible_locator(page, [config.insurance_filter_toggle_selector], SHORT_TIMEOUT)
    if toggle is None:
        toggle = find_insurance_filter_toggle_by_text(page)
    if toggle is None:
        raise RuntimeError(
            "INSURANCE_FILTER_TOGGLE_NOT_FOUND: Insurance filter dropdown not found; "
            "Patient insurance may not be checked."
        )

    option_text = clean(config.insurance_filter_option_text).lower()
    option_key = option_text.split()[0] if option_text else ""

    # PF's own default for this dropdown, confirmed live, is already "Active" -- skip
    # opening it at all when the collapsed label already reads that way, since the menu's
    # own markup (once opened) is still unconfirmed.
    current_label = locator_text(toggle).lower()
    if option_key and option_key in current_label:
        return True

    toggle.click()
    time.sleep(0.4)

    applied = False
    for selector in config.insurance_filter_menu_selectors:
        panel = visible_match(page, selector)
        if panel is None:
            continue
        options = panel.locator("li, [role='option'], a, button")
        for index in range(options.count()):
            option = options.nth(index)
            try:
                text = clean(option.inner_text()).lower()
            except Exception:
                continue
            if text == option_text or (text and option_text in text):
                option.click()
                time.sleep(0.4)
                applied = True
                break
        if applied:
            break

    label = insurance_filter_toggle_label(page, config).lower()
    confirmed = bool(label) and option_key in label
    if not confirmed:
        # TEMPORARY diagnostic (remove once insurance_filter_menu_selectors is confirmed
        # live): the toggle wasn't already on "Active" and none of the guessed menu
        # selectors matched an open panel -- dump what's actually visible so the real
        # selector can be found instead of another guess.
        try:
            debug = page.evaluate(
                """
                () => {
                    const sels = ['.ember-basic-dropdown-content', '.ember-power-select-dropdown',
                                  "[role='listbox']", '.tether-element', '.dropdown-menu',
                                  '.input-dropdown-menu'];
                    const found = [];
                    for (const sel of sels) {
                        document.querySelectorAll(sel).forEach(el => {
                            if (el.offsetParent !== null) found.push(el.outerHTML.slice(0, 1500));
                        });
                    }
                    return found;
                }
                """
            )
            print(f"  DEBUG insurance menu candidates: {debug}", flush=True)
        except Exception as exc:
            print(f"  DEBUG menu dump failed: {type(exc).__name__}: {exc}", flush=True)
        raise RuntimeError(
            "INSURANCE_FILTER_NOT_APPLIED: Could not confirm the Patient insurance filter "
            f"reads {config.insurance_filter_option_text!r}; toggle currently reads {label!r}."
        )
    return confirmed


def select_insurance_active_filter(page: Page, config: SyncConfig) -> bool:
    """Set the Patient insurance row's filter dropdown to "Active insurance".

    Confirmed live 2026-08-18: this is PF's plain input-dropdown control (a
    <button class="input-dropdown-button">), not the checkbox-dropdown-grouping widget the
    Notes row uses -- see insurance_filter_toggle_selector's docstring in models.py for the
    real markup. Its collapsed label is checked first and PF's own default for it is already
    "Active", so most calls never need to open it at all.

    Best-effort by design: this check layers on top of the note/facesheet flow, not a
    precondition for it. enforce_insurance_active_filter defaults to False specifically so
    NOTHING this function can raise -- a selector miss, a Playwright timeout mid-click,
    anything -- can fail the SOAP note PDF the record exists to produce; a live run hit
    exactly that (INSURANCE_FILTER_TOGGLE_NOT_FOUND failing the whole record) before this
    default and the try/except below existed.
    """
    if not clean(config.insurance_filter_option_text):
        return False
    try:
        return _select_insurance_active_filter_unsafe(page, config)
    except Exception as exc:
        if config.enforce_insurance_active_filter:
            raise
        print(f"  WARNING: insurance filter not applied ({type(exc).__name__}: {exc})", flush=True)
        return False


def prepare_print_chart_sections(
    page: Page, config: SyncConfig, modal: Optional[Locator] = None
) -> List[str]:
    """Prepare non-note Print Chart sections before selecting the SOAP note.

    v5.18 production behavior is notes-only: clear every non-note Facesheet section and
    verify none remain selected.  The prior Facesheet selection code is intentionally
    retained and can still be enabled explicitly with include_facesheet_sections=True.
    """
    if config.include_facesheet_sections:
        return select_facesheet_sections(page, config, modal)

    if modal is None:
        modal = require_visible_locator(
            page, config.print_modal_ready_selectors, SHORT_TIMEOUT, "Print Chart modal"
        )

    if not clear_all_facesheet_sections(page, config, modal):
        raise RuntimeError(
            "NOTES_ONLY_SELECTION_CLEAR_FAILED: could not clear all non-note Print Chart sections."
        )

    state = modal_checkbox_state(modal, config)
    unexpected = sorted(owner for owner, checked in state.items() if checked)
    if unexpected:
        raise RuntimeError(
            "NOTES_ONLY_SELECTION_MISMATCH: non-note Print Chart sections remain checked: "
            + ", ".join(unexpected)
        )
    return []


def note_date_tokens(appointment_date: str, formats: Iterable[str]) -> List[str]:
    """Build the date strings to look for in Print Chart note labels.

    v5.4: the previous de-zeroing used token.replace("/0", "/"), which can never touch a
    leading-zero month because there is no preceding slash. "07/27/2026" produced
    "07/27/2026" and "07/27/26" but never "7/27/2026", so a PF note label rendered
    without a leading zero raised SoapNoteNotFoundError.
    """
    parsed = require_date(appointment_date, "appointment date")
    tokens: List[str] = []
    for fmt in formats:
        token = parsed.strftime(fmt)
        variants = {
            token,
            token.replace("/0", "/").replace(" 0", " "),
            # Strip a leading zero on the first component as well.
            re.sub(r"^0(?=\d)", "", token),
            re.sub(r"^0(?=\d)", "", token).replace("/0", "/").replace(" 0", " "),
        }
        tokens.extend(variants)
    return sorted({clean(token) for token in tokens if clean(token)}, key=len, reverse=True)


def checkbox_display_text(checkbox: Locator) -> str:
    try:
        return clean(
            checkbox.evaluate(
                """
                el => {
                    const id = el.id || '';
                    let label = null;
                    if (id && window.CSS && CSS.escape) {
                        label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                    }
                    const container = label || el.closest(
                        'label, li, [role="option"], .checkbox-row, .check-box'
                    );
                    return container ? (container.innerText || container.textContent || '') : '';
                }
                """
            )
        )
    except Exception:
        return ""


def notes_toggle_label(page: Page, config: SyncConfig) -> str:
    toggle = visible_match(page, config.notes_dropdown_toggle_selector)
    if toggle is None:
        return ""
    return locator_text(toggle)


def notes_selection_is_empty(page: Page, config: SyncConfig) -> bool:
    """True when the notes toggle still reads as an empty selection."""
    label = notes_toggle_label(page, config).lower()
    empty_text = clean(config.notes_empty_label_text).lower()
    return bool(empty_text) and empty_text in label


def open_notes_dropdown(page: Page, config: SyncConfig) -> None:
    toggle = require_visible_locator(
        page, [config.notes_dropdown_toggle_selector], DEFAULT_TIMEOUT, "Notes dropdown toggle"
    )
    toggle.click()
    time.sleep(0.5)


def note_option_checkboxes(page: Page, config: SyncConfig) -> List[Locator]:
    """Visible note checkboxes from the tethered dropdown panel.

    The panel is tethered outside the modal, so it cannot be scoped to the modal
    container. Scoping to a known panel selector is preferred; the page-wide fallback is
    the pre-v5.5 behavior and relies on the date-token filter to exclude the modal's own
    section checkboxes.
    """
    containers: List[Any] = []
    for selector in config.note_dropdown_menu_selectors:
        panel = visible_match(page, selector)
        if panel is not None:
            containers.append(panel)
            break
    if not containers:
        containers.append(page)

    option_selector = config.note_option_selector or "input[type='checkbox']"
    found: List[Locator] = []
    for container in containers:
        try:
            boxes = container.locator(option_selector)
            count = boxes.count()
        except Exception:
            continue
        for index in range(count):
            box = boxes.nth(index)
            # v5.6: filter on whether the box is DRIVABLE, not whether the input renders.
            # Note options use the same hidden-input/visible-label pattern as the modal
            # sections, so an is_visible() filter discarded every note.
            if checkbox_is_interactive(box):
                found.append(box)
    return found


def _note_dropdown_panel(page: Page, config: SyncConfig) -> Optional[Locator]:
    """The tethered notes dropdown panel element, if one of
    config.note_dropdown_menu_selectors currently matches -- same lookup
    note_option_checkboxes does, kept in sync with it deliberately (not
    reused directly since that function returns checkboxes, not the panel
    itself)."""
    for selector in config.note_dropdown_menu_selectors:
        panel = visible_match(page, selector)
        if panel is not None:
            return panel
    return None


def for_each_note_checkbox_scrolled(
    page: Page,
    config: SyncConfig,
    visit,
    max_scrolls: int = 60,
) -> None:
    """Calls visit(checkbox, text) for every note checkbox reachable in the
    tethered notes dropdown, scrolling that panel first if it turns out to be
    scrollable -- a patient with a long note history could in principle have
    this panel virtualized the same way the Schedule/Report tables were
    confirmed to be (see scroll_schedule_day_and_collect / scroll_report_and_
    collect), silently under-enumerating note_option_checkboxes' one-shot
    count() the same way those were under-scraping before their own fixes.

    NOT independently confirmed live either way for this specific dropdown --
    added defensively rather than after catching a real miss, unlike the
    other two. Skips scrolling entirely (falls straight through to a single
    visit_current() pass, zero behavior change) if the panel isn't found or
    its scrollHeight doesn't actually exceed its clientHeight.

    Re-queries note_option_checkboxes() fresh at every scroll step and acts
    immediately via `visit`, rather than collecting Locators from an earlier
    step and using them later -- a Locator for a checkbox that scrolled out of
    a virtualized panel could go stale/detached, the same reason scroll_report_
    and_collect extracts plain values per step instead of holding handles.
    """
    def visit_current() -> None:
        for checkbox in note_option_checkboxes(page, config):
            text = checkbox_display_text(checkbox)
            if text:
                visit(checkbox, text)

    # Diagnostic logging added 2026-08-25: this function's own docstring above
    # admits the scroll-reveal path was never independently confirmed live --
    # these print()s turn "is the scroll actually working" from a guess into
    # something visible in every run's console output. Zero behavior change,
    # logging only.
    initial_count = len(note_option_checkboxes(page, config))
    print(f"  [notes-scroll] initial checkbox count={initial_count}", flush=True)
    visit_current()

    panel = _note_dropdown_panel(page, config)
    if panel is None:
        print("  [notes-scroll] no scrollable panel found (_note_dropdown_panel returned "
              "None) -- relying on the single un-scrolled pass above only", flush=True)
        return
    try:
        scroll_height = panel.evaluate("el => el.scrollHeight")
        client_height = panel.evaluate("el => el.clientHeight")
    except Exception as exc:
        print(f"  [notes-scroll] could not read panel scrollHeight/clientHeight: "
              f"{type(exc).__name__}: {exc}", flush=True)
        return
    if not scroll_height or not client_height or scroll_height <= client_height + 5:
        print(f"  [notes-scroll] panel not scrollable (scrollHeight={scroll_height}, "
              f"clientHeight={client_height}) -- nothing more to reveal", flush=True)
        return  # Not actually scrollable -- nothing more to reveal.

    print(f"  [notes-scroll] panel IS scrollable (scrollHeight={scroll_height}, "
          f"clientHeight={client_height}) -- scrolling to reveal more notes...", flush=True)
    try:
        panel.evaluate("el => { el.scrollTop = 0; }")
        time.sleep(0.15)
    except Exception as exc:
        print(f"  [notes-scroll] failed to reset scrollTop to 0: {type(exc).__name__}: {exc}", flush=True)
        return

    stuck = 0
    steps = 0
    for _ in range(max_scrolls):
        try:
            old_top = panel.evaluate("el => el.scrollTop")
            panel.evaluate(
                "el => { el.scrollTop = el.scrollTop + Math.max(150, el.clientHeight * 0.6); }"
            )
            time.sleep(0.2)
            new_top = panel.evaluate("el => el.scrollTop")
            max_top = panel.evaluate("el => el.scrollHeight - el.clientHeight")
        except Exception as exc:
            print(f"  [notes-scroll] scroll step {steps + 1} failed: {type(exc).__name__}: {exc}", flush=True)
            break
        steps += 1
        before = len(note_option_checkboxes(page, config))
        visit_current()
        after = len(note_option_checkboxes(page, config))
        print(f"  [notes-scroll] step {steps}: scrollTop {old_top}->{new_top} (max={max_top}), "
              f"checkbox count {before}->{after}", flush=True)
        stuck = stuck + 1 if new_top == old_top else 0
        if stuck >= 2 or int(new_top) >= int(max_top) - 5:
            print(f"  [notes-scroll] stopping after {steps} step(s) (stuck={stuck}, "
                  f"final checkbox count={after})", flush=True)
            break


def select_all_notes(page: Page, config: SyncConfig) -> str:
    """Check every note via the notes row's group checkbox.

    The notes row is an Ember select-all-checkbox-dropdown: the checkbox beside the
    "No notes selected" toggle applies to the whole group, so one click selects every
    note without enumerating the tethered panel. Verified by reading the toggle label,
    and falls back to checking each option individually if the group click does not take.
    """
    # v5.6: the group checkbox is a visually hidden input, so a visibility-based lookup
    # could never find it and this path always fell through to enumeration.
    group = page.locator(config.notes_group_checkbox_selector).first
    try:
        if group.count():
            ensure_checked(group, "notes group checkbox")
            time.sleep(0.4)
            if not notes_selection_is_empty(page, config):
                return clean(notes_toggle_label(page, config)) or "all notes"
    except Exception as exc:
        print(f"  notes group checkbox unavailable ({type(exc).__name__}); enumerating instead", flush=True)

    # Fallback: enumerate the dropdown (scrolling it if it turns out to be
    # scrollable -- see for_each_note_checkbox_scrolled) and check everything.
    open_notes_dropdown(page, config)
    labels: List[str] = []
    seen_labels = set()

    def check_one(box: Locator, text: str) -> None:
        normalized = clean(text)
        if not normalized or normalized in seen_labels:
            return
        try:
            ensure_checked(box, f"note option {text[:60]!r}")
            labels.append(normalized)
            seen_labels.add(normalized)
        except Exception:
            pass

    for_each_note_checkbox_scrolled(page, config, check_one)
    if not labels:
        raise SoapNoteNotFoundError(
            "SOAP_NOTE_NOT_FOUND_ANY: the notes dropdown exposed no selectable notes."
        )
    if notes_selection_is_empty(page, config):
        raise SoapNoteNotFoundError(
            "SOAP_NOTE_SELECTION_NOT_APPLIED: notes were clicked but the toggle still "
            f"reads {notes_toggle_label(page, config)!r}."
        )
    # Already deduped by check_one's seen_labels check above.
    return " | ".join(labels)


def clear_all_notes(page: Page, config: SyncConfig) -> None:
    """Clears every currently-checked note before selecting this record's own."""
    open_notes_dropdown(page, config)
    if notes_selection_is_empty(page, config):
        return  # Already clear -- nothing to do.

    group = page.locator(config.notes_group_checkbox_selector).first
    try:
        if group.count() and group.is_checked():
            ensure_unchecked(group, "notes group checkbox")
            time.sleep(0.4)
            if notes_selection_is_empty(page, config):
                return
    except Exception:
        pass

    # Fallback: the group checkbox can read indeterminate rather than fully
    # checked -- enumerate and uncheck individually instead, scrolling the
    # panel (if scrollable) so a long note history isn't under-enumerated the
    # same way note_option_checkboxes' one-shot count() could be -- see
    # for_each_note_checkbox_scrolled.
    def uncheck_if_checked(checkbox: Locator, text: str) -> None:
        del text
        try:
            if checkbox.is_checked():
                ensure_unchecked(checkbox, "note option")
        except Exception:
            pass

    for_each_note_checkbox_scrolled(page, config, uncheck_if_checked)


def parse_note_label_date(text: str, formats: Iterable[str]) -> Optional[date]:
    """Best-effort date extraction from a note checkbox's display text, e.g.
    '07/08/26 (SOAP Note)' -> date(2026, 7, 8). Used only by the most-recent
    fallback below to rank candidate notes -- note_date_tokens (above) does the
    reverse job (date -> text tokens to search for) for the normal exact-match
    path and is not reused here since it builds search tokens, not a parseable
    single date string."""
    candidates = re.findall(
        r"\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}",
        text,
    )
    for candidate in candidates:
        for fmt in formats:
            try:
                return datetime.strptime(clean(candidate).rstrip(","), fmt).date()
            except ValueError:
                continue
    return None


def select_soap_note_for_date(
    page: Page,
    config: SyncConfig,
    appointment_date: str,
    allow_most_recent_fallback: bool = False,
) -> Tuple[str, str]:
    """Returns (selected_note_text, match_mode). match_mode is "exact" for a
    real appointment-date match, or "fallback_most_recent_on_or_before:<date>"
    when allow_most_recent_fallback picked a substitute -- see that param's
    docstring below."""
    # clear_all_notes already opens the dropdown itself -- open_notes_dropdown
    # is a plain toggle-click, so calling it again here would CLOSE what
    # clear_all_notes just opened instead of opening it.
    clear_all_notes(page, config)
    tokens = note_date_tokens(appointment_date, config.note_date_formats)
    selected: List[str] = []
    seen: set = set()

    def check_if_matches(checkbox: Locator, text: str) -> None:
        if not any(token.lower() in text.lower() for token in tokens):
            return
        try:
            ensure_checked(checkbox, f"note option {text[:60]!r}")
        except Exception:
            return
        normalized = clean(text)
        if normalized and normalized not in seen:
            selected.append(normalized)
            seen.add(normalized)

    # Scrolls the tethered panel (if scrollable) so this record's own date
    # isn't missed just because it wasn't rendered yet without scrolling --
    # see for_each_note_checkbox_scrolled.
    for_each_note_checkbox_scrolled(page, config, check_if_matches)
    if selected:
        if notes_selection_is_empty(page, config):
            raise SoapNoteNotFoundError(
                "SOAP_NOTE_SELECTION_NOT_APPLIED: matching notes were clicked but the "
                f"toggle still reads {notes_toggle_label(page, config)!r}."
            )
        return " | ".join(selected), "exact"

    if not allow_most_recent_fallback:
        raise SoapNoteNotFoundError(
            f"SOAP_NOTE_NOT_FOUND_FOR_DATE: appointment_date={appointment_date}; tried={tokens}"
        )

    # No exact-date note exists. sync-schedules-by-date opts into this fallback
    # (2026-08-21, explicit user decision) so a patient who was actually seen
    # per the Schedule but whose SOAP note landed under an earlier date (e.g.
    # documented late, or dated to a prior encounter) still gets a facesheet
    # instead of sitting in review forever. NEVER picks a note dated after the
    # appointment -- that would attach a future encounter to this visit, which
    # is exactly the "different-date facesheet" mistake this endpoint's exact-
    # date guarantee exists to prevent. Only reaches here after the exact-match
    # pass above found nothing.
    requested_date = require_date(appointment_date, "appointment date")
    candidates: List[Tuple[date, str, Locator]] = []

    def collect_candidate(checkbox: Locator, text: str) -> None:
        note_date = parse_note_label_date(text, config.note_date_formats)
        if note_date is not None and note_date <= requested_date:
            candidates.append((note_date, clean(text), checkbox))

    for_each_note_checkbox_scrolled(page, config, collect_candidate)
    if not candidates:
        raise SoapNoteNotFoundError(
            "SOAP_NOTE_NOT_FOUND_FOR_DATE_OR_EARLIER: "
            f"appointment_date={appointment_date}; no note dated on/before it exists"
        )
    candidates.sort(key=lambda item: item[0])
    best_date, best_text, best_checkbox = candidates[-1]
    ensure_checked(best_checkbox, f"note option {best_text[:60]!r}")
    if notes_selection_is_empty(page, config):
        raise SoapNoteNotFoundError(
            "SOAP_NOTE_SELECTION_NOT_APPLIED: fallback note was clicked but the toggle "
            f"still reads {notes_toggle_label(page, config)!r}."
        )
    return best_text, f"fallback_most_recent_on_or_before:{best_date.isoformat()}"


def resolve_notes_mode(
    record: QueueRecord,
    config: SyncConfig,
    all_rows: Sequence[QueueRecord],
) -> str:
    """Resolve SOAP-note selection mode.

    Normal processing is always appointment-date scoped.  The legacy ``auto`` value is
    intentionally treated as ``date`` so older config files cannot revert to the previous
    first-chart behavior that selected the patient's entire note history.  ``all`` remains
    available only as an explicit override for manual/debug use.
    """
    del record, all_rows  # kept in the signature for backward compatibility with callers
    mode = clean(config.notes_selection_mode).lower() or "date"
    if mode == "all":
        return "all"
    if mode in {"date", "auto"}:
        return "date"
    print(
        f"  WARNING: unknown notes_selection_mode {mode!r}; treating it as 'date'.",
        flush=True,
    )
    return "date"


def select_notes_for_record(
    page: Page,
    config: SyncConfig,
    record: QueueRecord,
    all_rows: Sequence[QueueRecord],
    allow_most_recent_note_fallback: bool = False,
) -> Tuple[str, str]:
    mode = resolve_notes_mode(record, config, all_rows)
    if mode == "all":
        print("  notes: explicit all-notes override enabled", flush=True)
        record.soap_note_match_mode = ""
        return mode, select_all_notes(page, config)
    print(
        f"  notes: selecting SOAP notes dated {record.appointment_date}",
        flush=True,
    )
    note_text, match_mode = select_soap_note_for_date(
        page, config, record.appointment_date, allow_most_recent_note_fallback
    )
    record.soap_note_match_mode = match_mode
    if match_mode != "exact":
        print(f"  notes: no exact-date note; used fallback -> {match_mode}", flush=True)
    return mode, note_text


def format_pdf_name(record: QueueRecord, config: SyncConfig) -> str:
    values = {
        "patient_id": record.patient_id or record.ehr_patient_guid,
        "appointment_date": (
            parse_date(record.appointment_date).isoformat()
            if parse_date(record.appointment_date) else record.appointment_date
        ),
        "encounter_id": record.encounter_id or record.encounter_key or "encounter",
        "appointment_id": record.appointment_id or "appointment",
    }
    try:
        name = config.download_filename_template.format(**values)
    except Exception:
        name = f"{values['patient_id']}_{values['appointment_date']}_{values['encounter_id']}.pdf"
    name = safe_filename(name)
    return name if name.lower().endswith(".pdf") else name + ".pdf"
