"""Appointment report Playwright pull: date entry, export/scrape, pagination."""

import json
import re
import shutil
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.sync_api import Page

from pf_sync_pkg.constants import DEFAULT_TIMEOUT
from pf_sync_pkg.dom_utils import first_visible_locator
from pf_sync_pkg.models import AppointmentReportConfig
from pf_sync_pkg.tabular import read_tabular_rows, write_csv
from pf_sync_pkg.utils import clean


def fill_date_input(page: Page, selector: str, value: date) -> None:
    locator = page.locator(selector).first
    locator.wait_for(state="visible", timeout=30_000)
    formatted = value.strftime("%m/%d/%Y")
    try:
        locator.click()
        locator.fill(formatted)
        locator.press("Tab")
    except Exception:
        locator.evaluate(
            """
            (el, value) => {
                el.value = value;
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.blur();
            }
            """,
            formatted,
        )


def wait_report_ready(page: Page, config: AppointmentReportConfig) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:
        pass
    if first_visible_locator(page, config.no_results_selectors, timeout_ms=2_000):
        return
    ready = first_visible_locator(page, config.report_ready_selectors, timeout_ms=20_000)
    if ready is None:
        raise RuntimeError(
            "The appointment report did not expose a recognized result element after Run."
        )


def copy_download_to_csv(download, output_csv: str) -> Tuple[int, List[Dict[str, str]]]:
    target = Path(output_csv)
    target.parent.mkdir(parents=True, exist_ok=True)
    suggested = clean(download.suggested_filename)
    temporary = target.with_name(target.stem + "_download" + Path(suggested).suffix)
    download.save_as(str(temporary))
    if temporary.suffix.lower() == ".csv":
        if temporary.resolve() != target.resolve():
            shutil.move(str(temporary), str(target))
    else:
        rows = read_tabular_rows(str(temporary))
        write_csv(str(target), rows)
        temporary.unlink(missing_ok=True)
    # Already read back to confirm the write and compute the count -- return those
    # same rows so callers with an in-memory pipeline (run_facesheet_pull_by_date)
    # don't need a second, independent read of this file.
    rows_data = read_tabular_rows(str(target))
    return len(rows_data), rows_data


def try_export_report(
    page: Page,
    config: AppointmentReportConfig,
    output_csv: str,
) -> Optional[Tuple[int, List[Dict[str, str]]]]:
    export_button = first_visible_locator(
        page, config.export_report_button_selectors, timeout_ms=5_000
    )
    if export_button is None:
        return None

    try:
        with page.expect_download(timeout=7_000) as info:
            export_button.click()
        return copy_download_to_csv(info.value, output_csv)
    except Exception:
        # Some Export buttons open a format menu first.
        csv_option = first_visible_locator(
            page, config.csv_export_option_selectors, timeout_ms=3_000
        )
        if csv_option is None:
            return None
        try:
            with page.expect_download(timeout=10_000) as info:
                csv_option.click()
            return copy_download_to_csv(info.value, output_csv)
        except Exception:
            return None


def row_cells(row) -> List[str]:
    cell_selectors = "td, [role='cell'], [data-element^='data-table-cell-']"
    cells = row.locator(cell_selectors)
    values = []
    for index in range(cells.count()):
        try:
            values.append(clean(cells.nth(index).inner_text()))
        except Exception:
            values.append("")
    if values:
        return values
    try:
        text = clean(row.inner_text())
        return [text] if text else []
    except Exception:
        return []


def report_headers(page: Page, config: Optional[AppointmentReportConfig] = None) -> List[str]:
    """Read the report's column headers.

    v5.4: this previously collected every visible `th` on the whole page, so any other
    table PF happened to render (a filter panel, a summary strip) could prepend or
    interleave headers and silently shift every column in the scraped CSV. The scrape now
    searches inside config.table_selectors first -- a field that was defined but never
    referenced -- and only falls back to a page-wide search if no table matches.
    """
    header_selectors = [
        "[data-element^='data-table-header-']",
        "[role='columnheader']",
        "th",
    ]
    roots: List[Any] = []
    if config is not None:
        for table_selector in config.table_selectors:
            if not clean(table_selector):
                continue
            try:
                table = page.locator(table_selector).first
                if table.count():
                    roots.append(table)
            except Exception:
                continue
    roots.append(page)

    for root in roots:
        for selector in header_selectors:
            try:
                locator = root.locator(selector)
                count = locator.count()
            except Exception:
                continue
            headers = []
            for index in range(count):
                try:
                    if locator.nth(index).is_visible():
                        text = clean(locator.nth(index).inner_text())
                        if text:
                            headers.append(text)
                except Exception:
                    pass
            if headers:
                return headers
    return []


def collect_visible_report_rows(page: Page, config: AppointmentReportConfig) -> List[List[str]]:
    for selector in config.row_selectors:
        locator = page.locator(selector)
        rows = []
        for index in range(locator.count()):
            row = locator.nth(index)
            try:
                if not row.is_visible():
                    continue
            except Exception:
                continue
            cells = row_cells(row)
            if cells and any(cells):
                rows.append(cells)
        if rows:
            return rows
    return []


def _report_pager_state(page: Page) -> Tuple[Optional[int], Optional[int], Optional[int], str]:
    """Return (start, end, total, raw_text) for labels like
    '101 - 104 of 104 Appointments'. PF does not always expose a stable
    data-element on this report, so fall back to visible page text.
    """
    candidates = [
        "[data-element='pager-label']",
        ".pager-label",
        "[class*='pager']",
        "[class*='pagination']",
    ]
    texts: List[str] = []
    for selector in candidates:
        try:
            loc = page.locator(selector)
            for index in range(min(loc.count(), 20)):
                item = loc.nth(index)
                if item.is_visible():
                    value = clean(item.inner_text())
                    if value:
                        texts.append(value)
        except Exception:
            pass
    if not texts:
        try:
            texts.append(clean(page.locator("body").inner_text()))
        except Exception:
            pass

    pattern = re.compile(
        r"(?P<start>[0-9,]+)\s*[-–—]\s*(?P<end>[0-9,]+)\s+of\s+(?P<total>[0-9,]+)",
        flags=re.I,
    )
    for text in texts:
        match = pattern.search(text)
        if match:
            to_int = lambda value: int(value.replace(",", ""))
            return (
                to_int(match.group("start")),
                to_int(match.group("end")),
                to_int(match.group("total")),
                match.group(0),
            )
    return None, None, None, ""


def scrape_report_to_csv(
    page: Page, config: AppointmentReportConfig, output_csv: str
) -> Tuple[int, List[Dict[str, str]]]:
    collected: Dict[str, List[str]] = {}
    headers = report_headers(page, config)
    page_guard = 0

    while page_guard < 300:
        page_guard += 1
        scroller = first_visible_locator(page, config.scroller_selectors, timeout_ms=1_000)
        if scroller is not None:
            try:
                scroller.evaluate("el => { el.scrollTop = 0; }")
            except Exception:
                pass
            stuck = 0
            previous_size = -1
            for _ in range(200):
                for cells in collect_visible_report_rows(page, config):
                    key = json.dumps(cells, ensure_ascii=False)
                    collected[key] = cells
                if len(collected) == previous_size:
                    stuck += 1
                else:
                    stuck = 0
                    previous_size = len(collected)
                try:
                    before = scroller.evaluate("el => el.scrollTop")
                    maximum = scroller.evaluate("el => el.scrollHeight - el.clientHeight")
                    scroller.evaluate(
                        "el => { el.scrollTop += Math.max(300, el.clientHeight * 0.7); }"
                    )
                    time.sleep(0.25)
                    after = scroller.evaluate("el => el.scrollTop")
                    if (after >= maximum - 5 or after == before) and stuck >= 2:
                        break
                except Exception:
                    break
        else:
            for cells in collect_visible_report_rows(page, config):
                collected[json.dumps(cells, ensure_ascii=False)] = cells

        shown_start, shown_end, total, pager_text = _report_pager_state(page)
        if shown_end is not None and total is not None:
            print(
                f"Appointment report page {shown_start}-{shown_end} of {total}; "
                f"collected {len(collected)} rows.",
                flush=True,
            )
            if shown_end >= total:
                break

        next_button = first_visible_locator(page, config.next_page_selectors, timeout_ms=1_500)
        if next_button is None:
            break
        try:
            if not next_button.is_enabled():
                break
        except Exception:
            pass

        previous_state = (shown_start, shown_end, total, pager_text)
        try:
            next_button.click()
        except Exception:
            break

        # Stop immediately if clicking the right-arrow does not advance the pager.
        advanced = False
        deadline = time.time() + 6
        while time.time() < deadline:
            time.sleep(0.25)
            current_state = _report_pager_state(page)
            if current_state[:3] != previous_state[:3] and current_state[1] is not None:
                advanced = True
                break
        if not advanced:
            print(
                "Appointment report pager did not advance; treating the current page as the last page.",
                flush=True,
            )
            break

    rows = list(collected.values())

    # Rows are deduplicated by full cell content because the virtual scroller re-serves
    # the same visible rows on every scroll step. Two genuinely identical appointments
    # (same patient, same slot, same provider) therefore collapse into one, and the PF
    # export carries no appointment ID to tell them apart. Surface the discrepancy rather
    # than letting the row silently disappear.
    _, _, reported_total, _ = _report_pager_state(page)
    if reported_total is not None and len(rows) < reported_total:
        print(
            f"WARNING: the report pager reported {reported_total} appointments but "
            f"{len(rows)} distinct rows were collected. Identical rows are collapsed by "
            "content because the export has no appointment ID; verify against PF before "
            "relying on this file.",
            flush=True,
        )

    width = max([len(headers), *(len(row) for row in rows)], default=0)
    if width == 0:
        write_csv(output_csv, [])
        return 0, []
    if len(headers) < width:
        headers = headers + [f"column_{index + 1}" for index in range(len(headers), width)]
    headers = [header or f"column_{index + 1}" for index, header in enumerate(headers[:width])]
    dictionaries = [
        {headers[index]: row[index] if index < len(row) else "" for index in range(width)}
        for row in rows
    ]
    write_csv(output_csv, dictionaries)
    return len(dictionaries), dictionaries


def save_report_diagnostics(page: Page, output_csv: str) -> Tuple[str, str]:
    target = Path(output_csv)
    html_path = str(target.with_suffix(".diagnostic.html"))
    png_path = str(target.with_suffix(".diagnostic.png"))
    try:
        Path(html_path).write_text(page.content(), encoding="utf-8")
    except Exception:
        html_path = ""
    try:
        page.screenshot(path=png_path, full_page=True)
    except Exception:
        png_path = ""
    return html_path, png_path


def pull_appointment_report_on_page(
    page: Page,
    config: AppointmentReportConfig,
    start_date: date,
    end_date: date,
    output_csv: str,
    include_rows_data: bool = False,
) -> Dict[str, Any]:
    """include_rows_data: when True, the returned dict also carries the parsed report
    rows under "rows_data" (in memory already either way -- see copy_download_to_csv/
    scrape_report_to_csv). Defaults to False so every existing caller's returned dict
    -- and its json.dumps(...) logging -- is completely unchanged; only
    run_facesheet_pull_by_date opts in, to feed those rows straight into
    ingest_appointment_rows without a second file read."""
    if start_date > end_date:
        raise ValueError("start_date cannot be after end_date")

    try:
        reports = page.locator(config.reports_menu_selector).first
        reports.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        reports.click()
        report_link = page.locator(config.appointment_report_link_selector).first
        report_link.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
        report_link.click()

        page.locator(config.report_start_date_selector).first.wait_for(
            state="visible", timeout=DEFAULT_TIMEOUT
        )
        fill_date_input(page, config.report_start_date_selector, start_date)
        fill_date_input(page, config.report_end_date_selector, end_date)
        print(
            f"Appointment report date range: {start_date.strftime('%m/%d/%Y')} "
            f"through {end_date.strftime('%m/%d/%Y')}",
            flush=True,
        )

        run_button = first_visible_locator(
            page, config.run_report_button_selectors, timeout_ms=10_000
        )
        if run_button is None:
            raise RuntimeError("Could not find a visible Run Report button.")
        run_button.click()
        wait_report_ready(page, config)

        if first_visible_locator(page, config.no_results_selectors, timeout_ms=1_000):
            write_csv(output_csv, [])
            result = {"rows": 0, "method": "no_results", "output_csv": output_csv}
            if include_rows_data:
                result["rows_data"] = []
            return result

        exported = try_export_report(page, config, output_csv)
        if exported is not None:
            row_count, rows_data = exported
            result = {"rows": row_count, "method": "download", "output_csv": output_csv}
            if include_rows_data:
                result["rows_data"] = rows_data
            return result

        print(
            "Export did not produce a browser download; scraping all report pages instead.",
            flush=True,
        )
        row_count, rows_data = scrape_report_to_csv(page, config, output_csv)
        result = {"rows": row_count, "method": "dom_scrape", "output_csv": output_csv}
        if include_rows_data:
            result["rows_data"] = rows_data
        return result
    except Exception as exc:
        html_path, png_path = save_report_diagnostics(page, output_csv)
        raise RuntimeError(
            f"Appointment report pull failed: {exc}. Diagnostics: {html_path or '<none>'}, "
            f"{png_path or '<none>'}"
        ) from exc
