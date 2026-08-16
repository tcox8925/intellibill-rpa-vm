"""Encounter discovery and Print Chart section/notes selection UI driving."""

import hashlib
import re
import time
from datetime import date
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


def read_encounters(
    page: Page,
    config: SyncConfig,
    patient_guid: str,
    item_selector: str,
    source: str,
) -> List[DetectedEncounter]:
    try:
        page.locator(item_selector).first.wait_for(state="attached", timeout=10_000)
    except Exception:
        pass
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
        encounter_date_text = locator_text(item.locator(config.encounter_date_selector).first)
        encounter_date = parse_date(encounter_date_text)
        if encounter_date is None:
            continue
        encounter_type = locator_text(item.locator(config.encounter_type_selector).first)
        encounter_code = locator_text(item.locator(config.encounter_code_selector).first)
        complaint = locator_text(
            item.locator(config.encounter_chief_complaint_selector).first
        )
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


def open_print_chart(page: Page, config: SyncConfig) -> Locator:
    """Click Print Chart and return the visible modal container."""
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
        ensure_checked(checkbox, selector)
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
    return selected


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

    # Fallback: enumerate the dropdown and check everything in it.
    open_notes_dropdown(page, config)
    boxes = note_option_checkboxes(page, config)
    labels: List[str] = []
    for box in boxes:
        text = checkbox_display_text(box)
        if not text:
            continue
        try:
            ensure_checked(box, f"note option {text[:60]!r}")
            labels.append(clean(text))
        except Exception:
            continue
    if not labels:
        raise SoapNoteNotFoundError(
            "SOAP_NOTE_NOT_FOUND_ANY: the notes dropdown exposed no selectable notes."
        )
    if notes_selection_is_empty(page, config):
        raise SoapNoteNotFoundError(
            "SOAP_NOTE_SELECTION_NOT_APPLIED: notes were clicked but the toggle still "
            f"reads {notes_toggle_label(page, config)!r}."
        )
    unique: List[str] = []
    for label in labels:
        if label not in unique:
            unique.append(label)
    return " | ".join(unique)


def select_soap_note_for_date(page: Page, config: SyncConfig, appointment_date: str) -> str:
    open_notes_dropdown(page, config)
    tokens = note_date_tokens(appointment_date, config.note_date_formats)
    matches: List[Tuple[Locator, str]] = []
    for checkbox in note_option_checkboxes(page, config):
        text = checkbox_display_text(checkbox)
        if text and any(token.lower() in text.lower() for token in tokens):
            matches.append((checkbox, text))
    if not matches:
        raise SoapNoteNotFoundError(
            f"SOAP_NOTE_NOT_FOUND_FOR_DATE: appointment_date={appointment_date}; tried={tokens}"
        )
    selected = []
    seen = set()
    for checkbox, text in matches:
        ensure_checked(checkbox, f"note option {text[:60]!r}")
        normalized = clean(text)
        if normalized and normalized not in seen:
            selected.append(normalized)
            seen.add(normalized)
    if notes_selection_is_empty(page, config):
        raise SoapNoteNotFoundError(
            "SOAP_NOTE_SELECTION_NOT_APPLIED: matching notes were clicked but the toggle "
            f"still reads {notes_toggle_label(page, config)!r}."
        )
    return " | ".join(selected)


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
) -> Tuple[str, str]:
    mode = resolve_notes_mode(record, config, all_rows)
    if mode == "all":
        print("  notes: explicit all-notes override enabled", flush=True)
        return mode, select_all_notes(page, config)
    print(
        f"  notes: selecting SOAP notes dated {record.appointment_date}",
        flush=True,
    )
    return mode, select_soap_note_for_date(page, config, record.appointment_date)


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
