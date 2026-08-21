"""Data models: dataclasses, config loaders, and queue/encounter/error types."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

from pf_sync_pkg.constants import DEFAULT_IGNORED_STATUSES, DEFAULT_SEEN_STATUSES


@dataclass
class QueueRecord:
    row_id: str
    practice: str = ""

    appointment_id: str = ""
    appointment_date: str = ""
    appointment_status: str = ""
    appointment_type: str = ""
    provider: str = ""
    service_location: str = ""

    # Identity from the appointment report.
    patient_name: str = ""
    patient_dob: str = ""
    patient_phone: str = ""
    patient_phone_normalized: str = ""

    # Resolved Practice Fusion patient identity.
    patient_id: str = ""
    ehr_patient_guid: str = ""
    patient_match_status: str = "unmatched"
    patient_match_method: str = ""
    patient_match_score: float = 0.0
    patient_match_message: str = ""
    patient_candidates: List[Dict[str, Any]] = field(default_factory=list)

    # Encounter discovered from the authenticated patient Summary/timeline.
    encounter_id: str = ""  # Native encounter ID if a future source provides it.
    encounter_key: str = ""  # Stable derived key from visible encounter content.
    encounter_date: str = ""
    encounter_type: str = ""
    encounter_code: str = ""
    encounter_chief_complaint: str = ""
    encounter_source: str = ""

    # Queue state.
    status: str = "ready"
    status_reason: str = ""
    message: str = ""
    attempt_count: int = 0
    review_count: int = 0
    refresh_count: int = 0

    source_report_name: str = ""
    source_row_json: Dict[str, Any] = field(default_factory=dict)

    created_at: str = ""
    updated_at: str = ""
    first_ready_at: str = ""
    processing_started_at: str = ""
    last_checked_at: str = ""
    processed_at: str = ""

    selected_soap_note_text: str = ""
    # v5.20 (sync-schedules-by-date only): "exact" when the printed SOAP note was
    # dated exactly the appointment date, or "fallback_most_recent_on_or_before:<date>"
    # when no exact-date note existed and select_soap_note_for_date instead picked the
    # most recent note dated on/before the appointment date (never a future-dated note --
    # see chart_ui.select_soap_note_for_date's docstring). Empty for every other command,
    # which never enables this fallback. Kept distinct from selected_soap_note_text so a
    # manifest reviewer can tell an exact match from a fallback pick at a glance.
    soap_note_match_mode: str = ""
    # v5.5: record what was actually printed, so a chart PDF can be audited after the
    # fact without re-driving the browser.
    selected_sections: List[str] = field(default_factory=list)
    notes_selection_mode: str = ""
    pdf_path: str = ""
    metadata_json_path: str = ""
    elapsed_seconds: float = 0.0
    error_message: str = ""
    scrape_run_id: str = ""

    # v5.19: what the Print Chart modal's insurance filter dropdown actually confirmed to be
    # selected for this record (see chart_ui.select_insurance_active_filter), so a printed
    # chart can be audited after the fact without re-driving the browser -- same reasoning
    # as selected_sections above. Empty when Patient insurance was not printed for this
    # record (i.e. every command except full-sync-by-date).
    insurance_filter_selected: str = ""

    @classmethod
    def from_dict(cls, value: Dict[str, Any]) -> "QueueRecord":
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value.get(key) for key in allowed if key in value})


@dataclass
class DetectedEncounter:
    encounter_date: str
    encounter_type: str = ""
    encounter_code: str = ""
    chief_complaint: str = ""
    display_text: str = ""
    encounter_key: str = ""
    source: str = "summary"


class EncounterNotFoundError(LookupError):
    pass


class SoapNoteNotFoundError(LookupError):
    pass


@dataclass
class SyncConfig:
    # Confirmed patient Summary / Print Chart controls.
    print_chart_button_selector: str = "[data-element='btn-print-chart-control-bar']"

    # v5.5: the chart page contains TWO elements carrying
    # data-element="print-patient-content-modal": an outer
    # `carbon-content-modal-component` wrapper that stays hidden, and the real
    # `.content-modal` dialog. Waiting on `.first` locked onto the hidden wrapper and
    # timed out after 30s ("63 x locator resolved to hidden ..."). Ordered
    # most-specific-first, and every wait now scans all matches for a visible one.
    print_modal_ready_selectors: List[str] = field(
        default_factory=lambda: [
            "[data-element='print-patient-content-modal'].content-modal",
            "[data-element='print-patient-content-modal'].modal",
            "div[data-element='print-patient-content-modal']:not(.carbon-content-modal-component)",
            "[data-element='print-patient-content-modal']",
        ]
    )
    print_modal_close_selector: str = "[data-element='concent-model-close-btn']"
    print_modal_cancel_selector: str = "[data-element='btn-print-modal-cancel']"

    # Practice Fusion's NATIVE print-preview overlay (holds the
    # print-encounter-modal-frame iframe) opened by generate_pdf's own click on
    # generate_pdf_button_selector -- a SEPARATE element from
    # print_modal_ready_selectors above (the earlier Print Chart OPTIONS
    # dialog). Confirmed live 2026-08-21: this can still be sitting on top of
    # the page for the NEXT record -- PF is a hash-routed SPA, and navigating
    # to a different chart's Summary (page.goto to a new hash route) does not
    # necessarily force a full document reload -- and its iframe then
    # intercepts pointer events, timing out the next record's own Print Chart
    # button click even though that button is visible/enabled. See
    # chart_ui.dismiss_stray_print_preview_modal.
    native_print_preview_modal_selector: str = "[data-element='print-modal']"

    # "Select: all | none" links inside the modal header.
    print_modal_select_none_selector: str = "[data-element='print-modal-select-none']"
    print_modal_select_all_selector: str = "[data-element='print-modal-select-all']"

    notes_dropdown_selector: str = "[data-element='print-chart-notes-dropdown']"
    notes_dropdown_toggle_selector: str = (
        "[data-element='print-chart-notes-dropdown'] "
        "[data-element='checkbox-dropdown-grouping__toggle']"
    )
    # The group checkbox in the notes row selects/clears every note at once.
    notes_group_checkbox_selector: str = (
        "[data-element='print-chart-notes-dropdown'] input[type='checkbox']"
    )
    # Toggle label text when nothing is selected, used to verify the selection took.
    notes_empty_label_text: str = "No notes selected"

    # The notes menu is tethered outside the modal, so it cannot be scoped to the modal.
    # Tried in order; falls back to a page-wide search filtered by date token.
    note_dropdown_menu_selectors: List[str] = field(
        default_factory=lambda: [
            "[data-element='checkbox-dropdown-grouping__menu']",
            ".checkbox-dropdown-grouping__menu",
            ".tether-element .dropdown-menu",
            ".tether-element",
        ]
    )

    # v5.18: production PDFs are NOTES ONLY. Keep the Facesheet section selectors and
    # selection implementation available for an explicit future/manual override, but do
    # not select demographics, insurance, diagnoses, or any other Facesheet section by
    # default. The Print Chart modal is still cleared before the appointment-date note is
    # selected, which prevents Practice Fusion defaults/stale state from leaking in.
    include_facesheet_sections: bool = False

    # Sections to CHECK when include_facesheet_sections=True. Everything else in the modal
    # is explicitly cleared first, so this list remains the complete intended selection.
    facesheet_checkbox_selectors: List[str] = field(
        default_factory=lambda: [
            "[data-element='chk-patient-demographics'] input[type='checkbox']",
            "[data-element='print-insurance-options'] input[type='checkbox']",
            "[data-element='chk-diagnoses'] input[type='checkbox']",
        ]
    )

    # Every option the Print Chart modal is known to render, so the exact-selection pass
    # can clear the ones that are not wanted and detect any new option PF adds. The notes
    # dropdown is deliberately absent: it is selected separately and per record.
    facesheet_known_option_selectors: List[str] = field(
        default_factory=lambda: [
            "[data-element='chk-patient-demographics'] input[type='checkbox']",
            "[data-element='print-insurance-options'] input[type='checkbox']",
            "[data-element='chk-flowsheet-Vitals'] input[type='checkbox']",
            "[data-element='growth-charts'] input[type='checkbox']",
            "[data-element='chk-diagnoses'] input[type='checkbox']",
            "[data-element='chk-allergies'] input[type='checkbox']",
            "[data-element='chk-medications'] input[type='checkbox']",
            "[data-element='chk-immunizations'] input[type='checkbox']",
            "[data-element='chk-social-history'] input[type='checkbox']",
            "[data-element='chk-past-medical-history'] input[type='checkbox']",
            "[data-element='chk-family-history'] input[type='checkbox']",
            "[data-element='chk-advanced-directives'] input[type='checkbox']",
            "[data-element='print-implantable-devices-checkbox'] input[type='checkbox']",
            "[data-element='chk-health-concerns'] input[type='checkbox']",
            "[data-element='chk-goals'] input[type='checkbox']",
            "[data-element='chk-sia'] input[type='checkbox']",
        ]
    )
    # Fail rather than print a chart whose section selection could not be verified.
    enforce_exact_facesheet_selection: bool = True

    # v5.19: when Patient insurance is included (facesheet_checkbox_selectors), its own
    # filter dropdown has three options: "All insurance", "Active insurance", "Inactive
    # insurance". Left on "All insurance", the printed chart would include every
    # inactive/expired plan on file -- this is the "insurance active check": full-sync-by-date
    # confirms the printed insurance section is filtered down to active coverage only. See
    # cli.build_full_sync_by_date_config, the only caller that turns Patient insurance on by
    # default; process/nightly/refresh/full-sync stay notes-only and never reach this.
    #
    # Confirmed live 2026-08-18: this is NOT the checkbox-dropdown-grouping widget the Notes
    # row uses -- it's PF's plainer "input-dropdown" control (the same one the Vitals
    # flowsheet range picker uses), a <button class="input-dropdown-button"> whose own text
    # reads just "Active" (not "Active insurance") when collapsed, nested directly under
    # print-insurance-options:
    #   <div data-element="print-insurance-options" class="checkbox-row dropdown-print-row">
    #     <div class="check-box ..."><input type="checkbox">...Patient insurance</div>
    #     <div class="input-dropdown dropdown-print-select">
    #       <button class="input-dropdown-button">    Active</button>
    #     </div>
    #   </div>
    # The menu's markup once opened is still unconfirmed -- select_insurance_active_filter
    # checks the collapsed label first and skips opening it at all when that already reads
    # "Active", which is PF's own default for this dropdown.
    insurance_section_data_element: str = "print-insurance-options"
    insurance_filter_toggle_selector: str = (
        "[data-element='print-insurance-options'] .input-dropdown-button"
    )
    insurance_filter_menu_selectors: List[str] = field(
        default_factory=lambda: [
            "[data-element='checkbox-dropdown-grouping__menu']",
            ".ember-basic-dropdown-content",
            ".ember-power-select-dropdown",
            "[role='listbox']",
            ".checkbox-dropdown-grouping__menu",
            ".tether-element .dropdown-menu",
            ".tether-element",
        ]
    )
    insurance_filter_option_text: str = "Active insurance"
    # v5.19 field failure: this defaulted to True (fail rather than print a chart whose
    # insurance filter could not be confirmed) and the toggle selector -- never confirmed
    # against the live DOM -- didn't match on the first real run, failing every record
    # including its SOAP note. Demographics/insurance is an additive check layered on top
    # of the note/facesheet flow, not a precondition for it, so this now defaults to
    # best-effort: log a warning and keep going. Flip to True only after
    # insurance_filter_toggle_selector is confirmed live.
    enforce_insurance_active_filter: bool = False

    # SOAP notes are date-scoped for every appointment/PDF.
    # "date"  - select only notes matching the appointment date (default)
    # "auto"  - legacy alias for "date"
    # "all"   - explicit override only; selects every note
    notes_selection_mode: str = "date"

    note_option_selector: str = "input[type='checkbox']"
    generate_pdf_button_selector: str = "[data-element='btn-print-modal-print']"
    printable_preview_ready_selector: str = "a.print-link[title='Print']"
    pdf_min_bytes: int = 1024

    # Summary "recent encounters" panel -- card-style list.
    encounter_item_selector: str = "[data-element^='encounter-item-']"
    encounter_date_selector: str = ".text-color-link"
    encounter_type_selector: str = "[data-element='encounter-type']"
    encounter_code_selector: str = "[data-element='code-type-and-code-value']"
    encounter_chief_complaint_selector: str = ".chief-complaint"
    encounter_timeline_link_selector: str = "a[href*='/timeline/encounter']"

    # Timeline page -- a REAL <table> (data-table__row/data-table__cell, the
    # same shared PF component the Appointment Report table uses), a
    # completely different shape from the Summary panel above, confirmed live
    # 2026-08-21 from an actual Timeline row:
    #   <tr data-element="data-table-row-N">
    #     <td data-element="encounter-type-source"><p data-element="encounter-type" .../><p data-element="encounter-seen-by" .../></td>
    #     <td data-element="encounter-details"><p data-element="encounter-note-type" .../><p data-element="encounter-cc" .../></td>
    #     <td data-element="encounter-status"><p data-element="encounter-date" .../></td>
    #   </tr>
    # The prior defaults here (`[data-element^='encounter-item-'], li[...]` for
    # the row, and reusing the Summary panel's .text-color-link/etc. selectors
    # for date/type/code/complaint) matched NONE of this: encounter_timeline_
    # item_selector matched zero rows (Timeline lookups always returned
    # nothing), and .text-color-link actually lands on the encounter TYPE cell
    # ("Office Visit"), not the date -- so even if the row selector had
    # matched, parse_date() would have failed on "Office Visit" and every row
    # would still have been skipped. This is why the Timeline fallback never
    # found an encounter Summary didn't already have, for anyone.
    encounter_timeline_item_selector: str = "[data-element^='data-table-row-']"
    encounter_timeline_date_selector: str = "[data-element='encounter-date']"
    encounter_timeline_type_selector: str = "[data-element='encounter-type']"
    # No equivalent code/CPT column observed in a live Timeline row -- left
    # unset (encounter_code just reads blank for timeline-sourced encounters,
    # which reflects what's actually on the page rather than a bug).
    encounter_timeline_code_selector: str = ""
    encounter_timeline_chief_complaint_selector: str = "[data-element='encounter-cc']"
    encounter_soap_title_text: str = "SOAP Note"
    # Both the Summary "recent encounters" panel and the Timeline page can, for
    # a patient with enough visit history, render only a subset of encounter
    # items into the DOM at once -- the same data-table-scroller-style
    # virtualization already confirmed live for the Schedule table and the
    # notes dropdown panel (see patient_scraper.scroll_schedule_day_and_collect
    # and chart_ui.for_each_note_checkbox_scrolled). NOT independently
    # confirmed live for this specific list either way -- added defensively,
    # same posture as the notes dropdown fix, after a real live report of an
    # encounter that exists but wasn't found despite checking both Summary and
    # Timeline. read_encounters_scrolled falls straight through to the old
    # one-shot behavior if no scrollable container matching this is found.
    encounter_list_scroller_selector: str = "[data-element='data-table-scroller']"

    download_filename_template: str = (
        "{patient_id}_{appointment_date}_{encounter_id}.pdf"
    )
    note_date_formats: List[str] = field(
        default_factory=lambda: [
            "%m/%d/%Y",
            "%m/%d/%y",
            "%Y-%m-%d",
            "%b %d, %Y",
            "%B %d, %Y",
        ]
    )
    ignored_statuses: List[str] = field(
        default_factory=lambda: sorted(DEFAULT_IGNORED_STATUSES)
    )
    seen_statuses: List[str] = field(
        default_factory=lambda: sorted(DEFAULT_SEEN_STATUSES)
    )

    @classmethod
    def load(cls, path: str) -> "SyncConfig":
        if not path or not os.path.exists(path):
            return cls()
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                raw = json.load(handle)
        except (json.JSONDecodeError, OSError) as exc:
            # A missing/empty/corrupt override file is not fatal -- this file's
            # entire job is to override the defaults below, and those defaults
            # are already a complete, valid config on their own. Falling back
            # here (instead of raising) keeps a mid-write or truncated file
            # from taking down the whole sync run; see load_checkpoint's and
            # FileQueue._load's identical fallback for the same reasoning.
            print(f"SyncConfig.load: could not parse {path} ({exc}); using defaults")
            return cls()
        allowed = cls.__dataclass_fields__.keys()
        defaults = asdict(cls())

        # v5.5: print_modal_ready_selector became a list. Accept the old singular key.
        sync_aliases = {
            "print_modal_ready_selector": "print_modal_ready_selectors",
            "note_dropdown_menu_selector": "note_dropdown_menu_selectors",
        }
        for old, new in sync_aliases.items():
            if raw.get(old) and not raw.get(new):
                raw[new] = [raw[old]]

        # Never allow stale placeholder values ("", null, or []) to erase
        # confirmed built-in selectors. Only meaningful config values override
        # the dataclass defaults. This also makes older config drafts safe.
        overrides = {}
        unknown: List[str] = []
        healed: List[str] = []
        for key, value in raw.items():
            if key not in allowed:
                if key not in sync_aliases:
                    unknown.append(key)
                continue
            if value is None:
                healed.append(key)
                continue
            if isinstance(value, str) and not value.strip():
                healed.append(key)
                continue
            if isinstance(value, (list, dict)) and not value:
                healed.append(key)
                continue
            overrides[key] = value

        # v5.4: unknown and placeholder keys used to be dropped in silence, so a
        # misspelled key looked identical to a working one and an empty placeholder gave
        # no hint that the built-in default was doing the work instead of the file.
        if unknown:
            print(
                f"WARNING: ignoring unrecognized keys in {path}: {sorted(unknown)}",
                flush=True,
            )
        if healed:
            print(
                f"NOTE: empty/null keys in {path} fell back to built-in defaults: "
                f"{sorted(set(healed))}",
                flush=True,
            )

        merged = {**defaults, **overrides}
        return cls(**merged)


@dataclass
class AppointmentReportConfig:
    # Confirmed selectors supplied by the user.
    reports_menu_selector: str = "a[data-tracking='Reports'][href='#/PF/reporting']"
    appointment_report_link_selector: str = "a[data-element='appointment-report']"
    report_start_date_selector: str = "input[data-element='start-date']"
    report_end_date_selector: str = "input[data-element='end-date']"

    # PF commonly uses run-report-button; text fallbacks make the flow testable if
    # this report uses another stable data-element.
    run_report_button_selectors: List[str] = field(
        default_factory=lambda: [
            "[data-element='run-report-button']",
            "button:has-text('Run report')",
            "button:has-text('Run Report')",
            "button:has-text('Run')",
        ]
    )
    report_ready_selectors: List[str] = field(
        default_factory=lambda: [
            "[data-element='pager-label']",
            "tr[data-element^='data-table-row-']",
            "table tbody tr",
            "[role='row']",
        ]
    )
    export_report_button_selectors: List[str] = field(
        default_factory=lambda: [
            "[data-element*='export']",
            "button:has-text('Export')",
            "a:has-text('Export')",
            "button:has-text('Download')",
            "a:has-text('Download')",
        ]
    )
    csv_export_option_selectors: List[str] = field(
        default_factory=lambda: [
            "[role='menuitem']:has-text('CSV')",
            "li:has-text('CSV')",
            "a:has-text('CSV')",
            "button:has-text('CSV')",
        ]
    )

    # Generic table fallback when PF does not expose a direct CSV download.
    table_selectors: List[str] = field(
        default_factory=lambda: [
            "[data-element*='results-table']",
            "[data-element*='report-table']",
            "table",
            "[role='table']",
        ]
    )
    row_selectors: List[str] = field(
        default_factory=lambda: [
            "tr[data-element^='data-table-row-']",
            "tbody tr",
            "[role='row']",
        ]
    )
    scroller_selectors: List[str] = field(
        default_factory=lambda: [
            "[data-element='data-table-scroller']",
            ".data-table-scroller",
        ]
    )
    next_page_selectors: List[str] = field(
        default_factory=lambda: [
            "button[data-element*='next']:not([disabled])",
            "a[data-element*='next']:not([disabled])",
            "button[aria-label*='Next']:not([disabled])",
            "a[aria-label*='Next']:not([disabled])",
            "button:has-text('Next'):not([disabled])",
            "a:has-text('Next')",
        ]
    )
    no_results_selectors: List[str] = field(
        default_factory=lambda: [
            "text=No results were found",
            "text=No results",
        ]
    )

    @classmethod
    def load(cls, path: str) -> "AppointmentReportConfig":
        if not path or not os.path.exists(path):
            return cls()
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                raw = json.load(handle)
        except (json.JSONDecodeError, OSError) as exc:
            # See SyncConfig.load's identical fallback -- a missing/empty/corrupt
            # override file isn't fatal, the defaults below are already complete.
            print(f"AppointmentReportConfig.load: could not parse {path} ({exc}); using defaults")
            return cls()
        defaults = asdict(cls())
        allowed = cls.__dataclass_fields__.keys()
        # Backward-compatible singular keys from earlier config drafts.
        aliases = {
            "run_report_button_selector": "run_report_button_selectors",
            "report_ready_selector": "report_ready_selectors",
            "export_report_button_selector": "export_report_button_selectors",
            "report_row_selector": "row_selectors",
            "report_next_page_selector": "next_page_selectors",
        }
        for old, new in aliases.items():
            if raw.get(old) and not raw.get(new):
                raw[new] = [raw[old]]
        overrides = {}
        unknown: List[str] = []
        healed: List[str] = []
        for key, value in raw.items():
            if key not in allowed:
                if key not in aliases:
                    unknown.append(key)
                continue
            if value is None:
                healed.append(key)
                continue
            if isinstance(value, str) and not value.strip():
                healed.append(key)
                continue
            if isinstance(value, (list, dict)) and not value:
                healed.append(key)
                continue
            overrides[key] = value

        if unknown:
            print(
                f"WARNING: ignoring unrecognized keys in {path}: {sorted(unknown)}",
                flush=True,
            )
        if healed:
            print(
                f"NOTE: empty/null keys in {path} fell back to built-in defaults: "
                f"{sorted(set(healed))}",
                flush=True,
            )

        merged = {**defaults, **overrides}
        return cls(**merged)


@dataclass
class ScheduleScrapeConfig:
    """Selectors for Practice Fusion's Schedule 'Appointments' (list/agenda) view --
    patient_scraper.scrape_schedule_day and its per-row helpers read every one of
    these from here instead of a hardcoded literal, the same load()-with-config-file
    pattern SyncConfig/AppointmentReportConfig already use for the chart/report
    pages. Confirmed live 2026-08-21 against a real Schedule row's DOM; see
    scrape_schedule_day's own docstring for the exact HTML each came from.

    Two selectors deliberately do NOT key off Ember's per-instance numeric suffix
    (cell-provider-name-N, cell-appointment-type-N, intake-status-select-N-dropdown):
    confirmed live that N does not line up with cell-patient-N's own N even on the
    SAME row, so every one of these is a stable PREFIX match, scoped to the row,
    never an exact suffixed string. Two read off a `title` ATTRIBUTE rather than a
    dedicated data-element (appointment type, status): the status div's own class
    name reads as an Ember-generated hash (e.g. 'item--TBn') that can change
    between builds, so the title attribute is deliberately what's matched instead
    of trying to select the div by that class.
    """

    cell_patient_prefix: str = "[data-element^='cell-patient-']"
    cell_name_selector: str = "a[data-element='cell-name']"
    cell_dob_data_element: str = "cell-dob"
    cell_preferred_phone_data_element: str = "cell-preferred-phone"

    provider_name_prefix: str = "[data-element^='cell-provider-name-']"
    appointment_type_prefix: str = "[data-element^='cell-appointment-type-']"
    intake_status_button_prefix: str = "button[data-element^='intake-status-select-']"
    start_time_selector: str = "[data-element='start-time']"
    start_time_prefix_fallback: str = "[data-element^='start-time']"

    scheduler_tab_selector: str = "[data-element='scheduler-tab-0']"
    scheduler_selected_date_data_element: str = "scheduler-selected-date"
    date_next_selector: str = "[data-element='btn-date-next']"
    date_previous_selector: str = "[data-element='btn-date-previous']"

    # The row DOM (cell-provider-name-N etc.) uses the same data-table__cell /
    # appointments-table__col--sm classes PF's Patient List Report and
    # Appointment Report tables use, and both of THOSE are confirmed to render
    # only a subset of rows into the DOM at once, virtualized inside a
    # `data-table-scroller` -- a busy day's Schedule is very likely the same
    # component under the hood. scroll_schedule_day_and_collect (patient_scraper.py)
    # scrolls this container the same way scroll_report_and_collect already does
    # for the report pages, so a day with more appointments than fit in one
    # viewport isn't silently under-scraped.
    schedule_table_scroller_selector: str = "[data-element='data-table-scroller']"

    @classmethod
    def load(cls, path: str) -> "ScheduleScrapeConfig":
        if not path or not os.path.exists(path):
            return cls()
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                raw = json.load(handle)
        except (json.JSONDecodeError, OSError) as exc:
            # See SyncConfig.load's identical fallback -- a missing/empty/corrupt
            # override file isn't fatal, the defaults below are already complete.
            print(f"ScheduleScrapeConfig.load: could not parse {path} ({exc}); using defaults")
            return cls()
        defaults = asdict(cls())
        allowed = cls.__dataclass_fields__.keys()
        overrides = {}
        unknown = []
        healed = []
        for key, value in raw.items():
            if key not in allowed:
                unknown.append(key)
                continue
            if value is None:
                healed.append(key)
                continue
            if isinstance(value, str) and not value.strip():
                healed.append(key)
                continue
            overrides[key] = value

        if unknown:
            print(
                f"WARNING: ignoring unrecognized keys in {path}: {sorted(unknown)}",
                flush=True,
            )
        if healed:
            print(
                f"NOTE: empty/null keys in {path} fell back to built-in defaults: "
                f"{sorted(set(healed))}",
                flush=True,
            )

        merged = {**defaults, **overrides}
        return cls(**merged)
