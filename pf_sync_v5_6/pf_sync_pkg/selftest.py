"""Local synthetic self-test: no Practice Fusion/browser required."""

import json
import tempfile
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict

from pf_sync_pkg import chart_ui
from pf_sync_pkg.chart_ui import (
    checkbox_is_interactive,
    find_encounter_for_appointment,
    format_pdf_name,
    note_date_tokens,
    resolve_notes_mode,
    set_checkbox_state,
)
from pf_sync_pkg.identity import identity_score, name_token_containment
from pf_sync_pkg.ingest import ingest_appointments, map_appointment_row, validate_appointment_report_mapping
from pf_sync_pkg.matching import match_patients, resolve_patient_manually
from pf_sync_pkg.models import AppointmentReportConfig, EncounterNotFoundError, QueueRecord, SyncConfig
from pf_sync_pkg.pdf_pipeline import validate_patient_ready, write_appointments_metadata_json
from pf_sync_pkg.refresh import resolve_refresh_patient_template
from pf_sync_pkg.store import atomic_write_json, load_store, store_rows
from pf_sync_pkg.tabular import write_csv
from pf_sync_pkg.utils import is_ignored_status, normalize_header, parse_date


class _FakeCheckbox:
    """Minimal Locator stand-in for testing styled-checkbox handling without a browser."""

    def __init__(self, box: Dict[str, Any], kind: str = "input") -> None:
        self.box = box
        self.kind = kind

    def count(self) -> int:
        return 1

    def wait_for(self, **kwargs) -> None:
        return None

    def is_checked(self) -> bool:
        return bool(self.box["checked"])

    def is_visible(self) -> bool:
        if self.kind == "input":
            return bool(self.box["input_visible"])
        return bool(self.box.get(self.kind + "_visible", False))

    def check(self, force: bool = False, timeout: Any = None) -> None:
        if not self.box["force_works"]:
            raise RuntimeError("element is not visible")
        self.box["checked"] = True

    def uncheck(self, force: bool = False, timeout: Any = None) -> None:
        if not self.box["force_works"]:
            raise RuntimeError("element is not visible")
        self.box["checked"] = False

    def click(self, position: Any = None, timeout: Any = None) -> None:
        self.box["clicks"].append((self.kind, "glyph" if position else "center"))
        if self.kind != "sibling label":
            raise RuntimeError("not clickable")
        if position is None and self.box.get("center_hits_link"):
            self.box["dropdown_opened"] = True
            return
        self.box["checked"] = not self.box["checked"]

    def locator(self, selector: str) -> "_FakeCheckbox":
        kinds = {
            "xpath=following-sibling::label[1]": "sibling label",
            "xpath=../label": "parent label",
            "xpath=..": "parent container",
        }
        return _FakeCheckbox(self.box, kinds[selector])

    @property
    def first(self) -> "_FakeCheckbox":
        return self

    def evaluate(self, script: str, arg: Any = None) -> None:
        if not self.box["js_works"]:
            raise RuntimeError("evaluate blocked")
        self.box["checked"] = arg


def _fake_checkbox_box(**overrides: Any) -> Dict[str, Any]:
    state: Dict[str, Any] = {
        "checked": False,
        "input_visible": False,
        "force_works": False,
        "js_works": False,
        "sibling label_visible": False,
        "clicks": [],
        "dropdown_opened": False,
    }
    state.update(overrides)
    return state


def run_checkbox_self_test() -> None:
    """Styled-checkbox handling: PF hides the input and draws the control on the label."""
    # force=True on the hidden input succeeds without touching the label.
    box = _fake_checkbox_box(force_works=True)
    set_checkbox_state(_FakeCheckbox(box), True, "demographics")
    assert box["checked"] and not box["clicks"]

    # force fails: fall back to the label, clicking the glyph at its left edge.
    box = _fake_checkbox_box(**{"sibling label_visible": True})
    set_checkbox_state(_FakeCheckbox(box), True, "diagnoses")
    assert box["checked"] and box["clicks"] == [("sibling label", "glyph")]

    # Notes row: its label wraps the dropdown anchor, so a center click would open the
    # dropdown instead of toggling. The glyph position must be tried first.
    box = _fake_checkbox_box(center_hits_link=True, **{"sibling label_visible": True})
    set_checkbox_state(_FakeCheckbox(box), True, "notes group checkbox")
    assert box["checked"] and not box["dropdown_opened"]

    # No usable click target: DOM property plus input/change events.
    box = _fake_checkbox_box(js_works=True)
    set_checkbox_state(_FakeCheckbox(box), True, "goals")
    assert box["checked"]

    # Already in the desired state: never toggle.
    box = _fake_checkbox_box(checked=True, force_works=True)
    set_checkbox_state(_FakeCheckbox(box), True, "demographics")
    assert box["checked"] and not box["clicks"]

    # Unchecking works the same way.
    box = _fake_checkbox_box(checked=True, force_works=True)
    set_checkbox_state(_FakeCheckbox(box), False, "allergies")
    assert box["checked"] is False

    # Nothing works: raise, naming the control and every strategy tried.
    box = _fake_checkbox_box()
    try:
        set_checkbox_state(_FakeCheckbox(box), True, "chk-sia")
        raise AssertionError("set_checkbox_state should have raised.")
    except RuntimeError as exc:
        assert "CHECKBOX_NOT_SETTABLE" in str(exc)
        assert "chk-sia" in str(exc)

    # Interactivity must not depend on the input itself rendering.
    assert checkbox_is_interactive(_FakeCheckbox(_fake_checkbox_box(**{"sibling label_visible": True})))
    assert checkbox_is_interactive(_FakeCheckbox(_fake_checkbox_box(input_visible=True)))
    assert not checkbox_is_interactive(_FakeCheckbox(_fake_checkbox_box()))


def run_self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        appointments = root / "appointments.csv"
        patients = root / "patients.csv"
        # store.py's queue backend is Postgres now, namespaced by the queue file's
        # basename (not its full path) -- see store._queue_key. A fixed "queue.json"
        # here used to be safely isolated back when the queue was a JSON file at this
        # unique tempdir path; now it collides with every other self-test run's rows
        # under that same basename, so a fresh run finds its own prior rows already
        # there and reports them as "updated" instead of "inserted". A uuid-suffixed
        # name keeps this run's queue_key unique in the shared table, same as any two
        # real callers already stay isolated by using distinct queue_json basenames
        # (e.g. pf_appointment_queue.json vs pf_appointment_queue_tomorrow_test.json).
        queue = root / f"selftest_queue_{uuid.uuid4().hex}.json"

        # v5.4: this fixture used to invent headers (Appointment ID, PATIENT, DATE/TIME,
        # APPT. TYPE, SEEN BY PROVIDER) that no Practice Fusion export actually emits, then
        # printed "Practice Fusion DATE/TIME/APPT./provider column mapping: PASS". The test
        # asserted the exact thing that was broken and stayed green while real ingest failed.
        # The header set below is copied verbatim from a real PF CSV export.
        pf_export_headers = [
            "AppointmentTime", "Patient", "DOB", "MobilePhone", "HomePhone",
            "OfficePhone", "AppointmentType", "AppointmentStatus", "SeenBy",
            "Copay", "Eligibility", "Facility",
        ]

        def pf_row(
            time_value: str, name: str, dob: str, mobile: str,
            appointment_type: str, status: str, provider: str,
        ) -> Dict[str, str]:
            values = {
                "AppointmentTime": time_value,
                "Patient": name,
                "DOB": dob,
                "MobilePhone": mobile,
                "HomePhone": "",
                "OfficePhone": "",
                "AppointmentType": appointment_type,
                "AppointmentStatus": status,
                "SeenBy": provider,
                "Copay": "$35",
                "Eligibility": "Not available.",
                "Facility": "Self Test Clinic",
            }
            return {header: values[header] for header in pf_export_headers}

        write_csv(
            str(appointments),
            [
                pf_row(
                    "07/29/2026 09:15 AM", "Jane A Doe", "01/02/1980",
                    "(555) 111-2222", "Office Visit", "Checked out", "Test Provider M.D",
                ),
                pf_row(
                    "07/29/2026 10:00 AM", "John Smith", "04/05/1975",
                    "555-333-4444", "Follow-Up Visit", "Completed", "Test Provider M.D",
                ),
                pf_row(
                    "07/29/2026 10:30 AM", "Alex Lee", "03/03/1990",
                    "", "Lab Results", "Seen", "Test Provider M.D",
                ),
                pf_row(
                    "07/29/2026 11:00 AM", "Cancel Person", "02/02/1982",
                    "", "Wellness Exam", "Cancelled by patient", "Test Provider M.D",
                ),
                # Subset-name case: the report drops a middle name the chart carries, and
                # the DOB matches exactly. This must auto-resolve, not escalate.
                pf_row(
                    "07/29/2026 11:30 AM", "Maria Reyes Gomez", "06/07/1984",
                    "555-777-8888", "New Patient Visit", "Seen", "Test Provider M.D",
                ),
                # No PRN in the registry for this patient; GUID alone must be sufficient.
                pf_row(
                    "07/29/2026 01:00 PM", "Noel Prentiss", "09/09/1991",
                    "555-999-0000", "Sick", "Seen", "Test Provider M.D",
                ),
            ],
        )
        write_csv(
            str(patients),
            [
                {
                    "patient_id": "P1",
                    "ehr_patient_guid": "11111111-1111-1111-1111-111111111111",
                    "patient_name": "Jane Doe",
                    "dob": "01/02/1980",
                    "mobile_phone": "5551112222",
                    "status": "Active",
                    "raw_patient_json": "x" * 300_000,
                },
                {
                    # Same identity as Jane but explicitly inactive; it must be removed
                    # before matching so it cannot create a false ambiguity.
                    "patient_id": "P1-OLD",
                    "ehr_patient_guid": "11111111-1111-1111-1111-111111111199",
                    "patient_name": "Jane Doe",
                    "dob": "01/02/1980",
                    "mobile_phone": "5551112222",
                    "status": "Inactive",
                },
                {
                    "patient_id": "P2",
                    "ehr_patient_guid": "22222222-2222-2222-2222-222222222222",
                    "patient_name": "John Smith",
                    "dob": "04/05/1975",
                    "mobile_phone": "5553334444",
                },
                {
                    "patient_id": "P3",
                    "ehr_patient_guid": "33333333-3333-3333-3333-333333333333",
                    "patient_name": "Alex Lee",
                    "dob": "03/03/1990",
                    "mobile_phone": "5550001111",
                },
                {
                    "patient_id": "P4",
                    "ehr_patient_guid": "44444444-4444-4444-4444-444444444444",
                    "patient_name": "Alex Lee",
                    "dob": "03/03/1990",
                    "mobile_phone": "5550002222",
                },
                {
                    "patient_id": "P5",
                    "ehr_patient_guid": "55555555-5555-5555-5555-555555555555",
                    # Chart carries a middle name the appointment report omits.
                    "patient_name": "Maria Del Carmen Reyes Gomez",
                    "dob": "06/07/1984",
                    "mobile_phone": "5557778888",
                },
                {
                    # No PRN, as with 631 rows in the real PF export.
                    "patient_id": "",
                    "ehr_patient_guid": "66666666-6666-6666-6666-666666666666",
                    "patient_name": "Noel Prentiss",
                    "dob": "09/09/1991",
                    "mobile_phone": "5559990000",
                },
            ],
        )
        # Header normalization must handle the camelCase export form directly.
        assert normalize_header("AppointmentTime") == "appointment time"
        assert normalize_header("AppointmentStatus") == "appointment status"
        assert normalize_header("MobilePhone") == "mobile phone"
        assert normalize_header("SeenBy") == "seen by"
        assert normalize_header("DOB") == "dob"
        # The spaced/punctuated forms must still normalize to the same tokens.
        assert normalize_header("APPT. STATUS") == "appt status"
        assert normalize_header("Appointment Date") == "appointment date"

        config = SyncConfig()
        ingest_counts = ingest_appointments(
            str(appointments), str(queue), "Self Test Practice", config=config
        )
        assert ingest_counts["inserted"] == 6, ingest_counts

        def row_for(name: str) -> QueueRecord:
            rows_now = store_rows(load_store(str(queue)))
            return next(row for row in rows_now if row.patient_name == name)

        # Every field the PF export carries must survive mapping, not just date and name.
        pf_export_row = row_for("Jane A Doe")
        assert pf_export_row.appointment_date == "07/29/2026 09:15 AM"
        assert parse_date(pf_export_row.appointment_date) == date(2026, 7, 29)
        assert pf_export_row.appointment_type == "Office Visit"
        assert pf_export_row.appointment_status == "Checked out"
        assert pf_export_row.provider == "Test Provider M.D"
        assert pf_export_row.service_location == "Self Test Clinic"
        assert pf_export_row.patient_phone_normalized == "5551112222"

        # v5.16: one batch JSON manifest contains every appointment/PDF produced
        # during the same processing run. No per-PDF sidecars are created.
        metadata_pdf_1 = root / "P1_2026-07-29_test.pdf"
        metadata_pdf_1.write_bytes(b"%PDF-1.4 self-test")
        pf_export_row.pdf_path = str(metadata_pdf_1)
        pf_export_row.ehr_patient_guid = "11111111-1111-1111-1111-111111111111"

        second_metadata_row = row_for("John Smith")
        metadata_pdf_2 = root / "P2_2026-07-29_test.pdf"
        metadata_pdf_2.write_bytes(b"%PDF-1.4 self-test")
        second_metadata_row.pdf_path = str(metadata_pdf_2)
        second_metadata_row.ehr_patient_guid = "22222222-2222-2222-2222-222222222222"

        metadata_path = write_appointments_metadata_json(
            [pf_export_row, second_metadata_row],
            str(root),
            "self-test-run",
        )
        metadata_payload = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
        assert list(metadata_payload.keys()) == ["appointments"]
        assert len(metadata_payload["appointments"]) == 2

        metadata_row = metadata_payload["appointments"][0]
        assert metadata_row == {
            "pdf_file": metadata_pdf_1.name,
            "appt_date": "2026-07-29",
            "appt_time": "9:15 AM",
            "patient_name": "Jane A Doe",
            "dob": "1980-01-02",
            "provider_name": "Test Provider M.D",
            "service_location": "Self Test Clinic",
            "patient_id": "11111111-1111-1111-1111-111111111111",
            "soap_note_match_mode": "",
        }, metadata_row
        # An appointment_id field was tried and removed the same day (2026-08-11) --
        # confirmed live PF never supplies one for this account anywhere reachable.
        # "appt_id"/"facesheet_id" stay banned as Tebra-only, never-synthesized
        # fields per this function's own docstring.
        assert "appointment_id" not in metadata_row
        assert "appt_id" not in metadata_row
        assert "facesheet_id" not in metadata_row
        assert pf_export_row.metadata_json_path == metadata_path
        assert second_metadata_row.metadata_json_path == metadata_path
        assert not metadata_pdf_1.with_suffix(".json").exists()
        assert not metadata_pdf_2.with_suffix(".json").exists()

        # A blank appointment_status must be a hard mapping failure, because an empty
        # status silently disables the ignored gate.
        unmapped = [{"SomeTime": "07/29/2026", "Who": "X", "Born": "01/01/1980"}]
        try:
            validate_appointment_report_mapping(
                unmapped, [map_appointment_row(row) for row in unmapped]
            )
            raise AssertionError("Unmapped report headers should have raised ValueError.")
        except ValueError as exc:
            assert "appointment status" in str(exc), str(exc)

        # The ignored gate must read the config, not a module-level constant.
        assert is_ignored_status("Cancelled by patient", config)
        narrowed = SyncConfig(ignored_statuses=["deleted"])
        assert not is_ignored_status("Cancelled by patient", narrowed)

        match_counts = match_patients(str(queue), str(patients))
        assert row_for("Cancel Person").status == "ignored"

        # Explicitly inactive patient charts are excluded before scoring.
        jane = row_for("Jane A Doe")
        assert jane.patient_match_status == "matched", jane.patient_match_message
        assert jane.patient_id == "P1"
        assert jane.ehr_patient_guid == "11111111-1111-1111-1111-111111111111"

        # Subset name plus exact DOB must auto-resolve rather than escalate.
        subset = row_for("Maria Reyes Gomez")
        assert subset.patient_match_status == "matched", subset.patient_match_message
        assert subset.ehr_patient_guid == "55555555-5555-5555-5555-555555555555"

        # A chart with no PRN must be usable end to end on the GUID alone.
        no_prn = row_for("Noel Prentiss")
        assert no_prn.patient_match_status == "matched", no_prn.patient_match_message
        assert no_prn.ehr_patient_guid == "66666666-6666-6666-6666-666666666666"
        assert not no_prn.patient_id
        validate_patient_ready(no_prn)
        assert format_pdf_name(no_prn, config).startswith("66666666-")

        # Genuine ambiguity (two charts, same name, same DOB) must still reach a human.
        assert row_for("Alex Lee").status == "needs_attention", match_counts

        resolve_patient_manually(
            str(queue),
            patient_id="P3",
            ehr_patient_guid="33333333-3333-3333-3333-333333333333",
            row_id=row_for("Alex Lee").row_id,
        )
        assert row_for("Alex Lee").patient_id == "P3"
        assert row_for("Alex Lee").status == "ready"

        # Manual resolution by GUID alone must be accepted.
        resolve_patient_manually(
            str(queue),
            patient_id="",
            ehr_patient_guid="66666666-6666-6666-6666-666666666666",
            row_id=row_for("Noel Prentiss").row_id,
        )
        assert row_for("Noel Prentiss").status == "ready"

        # Refresh can target the Practice Fusion chart GUID directly, independent of PRN.
        refresh_store = load_store(str(queue))
        refresh_rows = store_rows(refresh_store)
        guid_template = resolve_refresh_patient_template(
            refresh_store,
            refresh_rows,
            ehr_patient_guid="33333333-3333-3333-3333-333333333333",
        )
        assert guid_template.patient_id == "P3"
        assert guid_template.ehr_patient_guid == "33333333-3333-3333-3333-333333333333"
        direct_guid = resolve_refresh_patient_template(
            {"patient_mappings": []},
            [],
            ehr_patient_guid="77777777-7777-7777-7777-777777777777",
        )
        assert direct_guid.patient_id == ""
        assert direct_guid.ehr_patient_guid == "77777777-7777-7777-7777-777777777777"
        assert direct_guid.patient_match_method == "direct_guid"

        # Nightly appointment checks are Summary-only. A missing encounter must not
        # invoke Timeline navigation or any Page methods beyond the Summary reader.
        original_summary_reader = chart_ui.read_summary_encounters
        try:
            chart_ui.read_summary_encounters = lambda page, cfg, guid: []
            try:
                find_encounter_for_appointment(
                    object(), config, "33333333-3333-3333-3333-333333333333", "07/31/2026 10:45 AM"
                )
                raise AssertionError("Missing Summary encounter should have raised.")
            except EncounterNotFoundError as exc:
                assert "AFTER_SUMMARY_CHECK" in str(exc), str(exc)
                assert "TIMELINE" not in str(exc), str(exc)
        finally:
            chart_ui.read_summary_encounters = original_summary_reader

        # Note-date tokens must include the leading-zero-stripped month.
        tokens = note_date_tokens("07/27/2026 08:00 AM", config.note_date_formats)
        assert "07/27/2026" in tokens
        assert "7/27/2026" in tokens, tokens

        # Containment scoring: a dropped middle name or second surname must score high,
        # but a single shared given name must not. "Peyton Peyton" is a real malformed
        # registry row; scoring it 1.0 against "Peyton Hicks" attached the wrong chart.
        assert name_token_containment("Marlene Revilla Gomez", "Marlene Del Carmen Revilla Gomez") == 1.0
        assert name_token_containment("Elizabeth Vazquez Martinez", "Elizabeth Vazquez") == 1.0
        assert name_token_containment("Peyton Hicks", "Peyton Peyton") == 0.0
        assert name_token_containment("Ruben Tejada", "Ruben Alvarez") == 0.0
        assert identity_score("Peyton Hicks", "Peyton Peyton") < 0.85

        # v5.18: SOAP notes are always appointment-date scoped during normal processing.
        # The legacy "auto" value must also resolve to date so old config files cannot
        # accidentally select the patient's entire note history on the first PDF.
        notes_cfg = SyncConfig()
        assert notes_cfg.notes_selection_mode == "date"
        first = QueueRecord(
            row_id="r1", ehr_patient_guid="guid-a", appointment_date="07/29/2026 09:15 AM"
        )
        later = QueueRecord(
            row_id="r2", ehr_patient_guid="guid-a", appointment_date="08/05/2026 09:15 AM"
        )
        assert resolve_notes_mode(first, notes_cfg, [first, later]) == "date"
        first.status = "processed"
        first.pdf_path = "C:\\charts\\guid-a.pdf"
        assert resolve_notes_mode(first, notes_cfg, [first, later]) == "date"
        assert resolve_notes_mode(later, notes_cfg, [first, later]) == "date"
        other = QueueRecord(row_id="r3", ehr_patient_guid="guid-b", appointment_date="08/05/2026")
        assert resolve_notes_mode(other, notes_cfg, [first, later, other]) == "date"
        # Legacy auto is now date-scoped; explicit all remains available only as an override.
        assert resolve_notes_mode(first, SyncConfig(notes_selection_mode="auto"), [first]) == "date"
        assert resolve_notes_mode(later, SyncConfig(notes_selection_mode="all"), [first, later]) == "all"
        assert resolve_notes_mode(first, SyncConfig(notes_selection_mode="date"), [first]) == "date"

        # The modal-ready selector list must prefer the real dialog over the hidden
        # carbon-content-modal-component wrapper that shares its data-element.
        assert notes_cfg.print_modal_ready_selectors[0].endswith(".content-modal")
        assert any(
            "not(.carbon-content-modal-component)" in selector
            for selector in notes_cfg.print_modal_ready_selectors
        )
        # v5.18: production Print Chart output is notes-only. The prior Facesheet selector
        # configuration remains present (disabled, not deleted) for explicit override.
        assert notes_cfg.include_facesheet_sections is False
        assert notes_cfg.facesheet_checkbox_selectors, "Facesheet selector code/config was removed"

        # Every retained configured section must appear in the known-option manifest, or
        # an explicit include_facesheet_sections=True override could not be verified.
        for selector in notes_cfg.facesheet_checkbox_selectors:
            assert selector in notes_cfg.facesheet_known_option_selectors, selector

        # v5.19: full-sync-by-date builds its own config fresh per call (notes +
        # demographics + active insurance) without ever touching the on-disk default
        # every other command reads as notes-only (asserted above).
        import argparse as _argparse

        from pf_sync_pkg.cli import build_full_sync_by_date_config  # lazy: avoids cli<->selftest cycle

        full_sync_cfg = build_full_sync_by_date_config(
            _argparse.Namespace(config_json=str(root / "does_not_exist.json"))
        )
        assert full_sync_cfg.include_facesheet_sections is True
        assert any(
            "chk-patient-demographics" in selector for selector in full_sync_cfg.facesheet_checkbox_selectors
        )
        assert any(
            "print-insurance-options" in selector for selector in full_sync_cfg.facesheet_checkbox_selectors
        )
        assert not any(
            "chk-diagnoses" in selector for selector in full_sync_cfg.facesheet_checkbox_selectors
        )
        assert full_sync_cfg.insurance_section_data_element == "print-insurance-options"
        assert full_sync_cfg.insurance_filter_option_text == "Active insurance"
        # Best-effort by default: a selector miss on this must never fail the SOAP note PDF
        # (see select_insurance_active_filter's docstring for the live run this fixes).
        assert full_sync_cfg.enforce_insurance_active_filter is False
        # Confirmed live 2026-08-18: the insurance filter is PF's plain input-dropdown
        # control (a <button class="input-dropdown-button">), not the notes-style
        # checkbox-dropdown-grouping widget originally guessed.
        assert "input-dropdown-button" in full_sync_cfg.insurance_filter_toggle_selector
        assert full_sync_cfg.insurance_section_data_element in full_sync_cfg.insurance_filter_toggle_selector
        # Building full-sync-by-date's config must never mutate the on-disk default.
        assert SyncConfig().include_facesheet_sections is False

        # The old singular print_modal_ready_selector key must still load.
        legacy = root / "legacy_config.json"
        atomic_write_json(
            str(legacy),
            {"print_modal_ready_selector": "[data-element='legacy-modal']"},
        )
        legacy_cfg = SyncConfig.load(str(legacy))
        assert legacy_cfg.print_modal_ready_selectors == ["[data-element='legacy-modal']"]

        run_checkbox_self_test()

        print("SELF-TEST PASSED")
        print("  Practice Fusion camelCase export header mapping: PASS")
        print("  all required columns mapped (date/status/type/provider): PASS")
        print("  unmapped-column failure raises before queueing: PASS")
        print("  ignored-status gate reads config in ingest and process: PASS")
        print("  ingest/upsert: PASS")
        print("  DOB-first matching incl. dropped middle name/second surname: PASS")
        print("  inactive patient registry rows excluded before matching: PASS")
        print("  GUID-only patients (no PRN) resolve and name PDFs: PASS")
        print("  refresh accepts Practice Fusion chart GUID directly: PASS")
        print("  phone/ambiguity handling: PASS")
        print("  ignored cancellation handling: PASS")
        print("  manual resolution by PRN or GUID + persistent mapping: PASS")
        print("  nightly appointment encounter lookup stays on Summary: PASS")
        print("  SOAP note date tokens incl. no-leading-zero month: PASS")
        print("  print modal selector prefers visible dialog over hidden wrapper: PASS")
        print("  exact facesheet selection manifest is self-consistent: PASS")
        print("  notes mode: all notes on first chart, date-scoped thereafter: PASS")
        print("  legacy singular print_modal_ready_selector still loads: PASS")
        print("  hidden styled checkboxes driven via label/glyph/property ladder: PASS")
        print("  large CSV/JSON field handling: PASS")
    return 0
