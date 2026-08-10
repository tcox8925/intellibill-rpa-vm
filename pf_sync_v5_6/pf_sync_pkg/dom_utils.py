"""Shared visibility-aware locator helpers used by report_pull.py and chart_ui.py/pdf_pipeline.py.

Pulled into their own module (rather than living in report_pull.py or chart_ui.py) so
neither of those modules needs to import from the other.
"""

import time
from typing import Optional, Sequence

from playwright.sync_api import Locator, Page

from pf_sync_pkg.constants import SHORT_TIMEOUT
from pf_sync_pkg.utils import clean


def visible_match(page: Page, selector: str, max_candidates: int = 40) -> Optional[Locator]:
    """Return the first VISIBLE element matching selector, not merely the first element.

    v5.5: every visibility check in this worker used `page.locator(sel).first`, which
    resolves to the first element in DOM order regardless of visibility. The Print Chart
    page carries two elements with data-element="print-patient-content-modal" -- a hidden
    `carbon-content-modal-component` wrapper followed by the real `.content-modal` dialog
    -- so `.first` pinned the hidden wrapper and the 30s wait could never succeed. Any PF
    view that renders a hidden template alongside a live one hits the same trap.
    """
    if not clean(selector):
        return None
    try:
        locator = page.locator(selector)
        count = min(locator.count(), max_candidates)
    except Exception:
        return None
    for index in range(count):
        candidate = locator.nth(index)
        try:
            if candidate.is_visible():
                return candidate
        except Exception:
            continue
    return None


def first_visible_locator(
    page: Page,
    selectors: Sequence[str],
    timeout_ms: int = SHORT_TIMEOUT,
) -> Optional[Locator]:
    deadline = time.time() + timeout_ms / 1000
    while True:
        for selector in selectors:
            found = visible_match(page, selector)
            if found is not None:
                return found
        if time.time() >= deadline:
            return None
        time.sleep(0.2)


def require_visible_locator(
    page: Page,
    selectors: Sequence[str],
    timeout_ms: int,
    label: str,
) -> Locator:
    """Wait for a visible match or raise with the state that was actually found."""
    found = first_visible_locator(page, selectors, timeout_ms)
    if found is not None:
        return found
    diagnostics = []
    for selector in selectors:
        if not clean(selector):
            continue
        try:
            total = page.locator(selector).count()
        except Exception:
            total = -1
        diagnostics.append(f"{selector} -> {total} match(es), none visible")
    raise RuntimeError(
        f"{label} did not become visible within {timeout_ms}ms. " + "; ".join(diagnostics)
    )
