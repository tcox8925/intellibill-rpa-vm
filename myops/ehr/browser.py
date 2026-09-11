"""
Playwright browser/grid helpers — moved verbatim from tebra_rpa.py.

Pure UI glue shared by the passes: grid settling, filter drawer, date filter,
virtual-scroll row lookup, patient-row -> facesheet navigation, and the
Tebra Patient ID scrape. No pipeline logic lives here.
"""


def slow_fill(locator, text):
    locator.click()
    locator.press("Control+A")
    locator.press("Backspace")
    locator.fill(text)


def cell(row, field):
    try:
        el = row.locator(f"div[data-field='{field}']")
        # el.count()==0 doesn't mean the field is empty -- it means the cell
        # hasn't mounted into the DOM yet (MUI DataGrid builds a row's cells
        # asynchronously, and a field can be briefly virtualized out even
        # while the row itself is visible). Give it a real chance to attach
        # instead of bailing instantly; only return None if it genuinely
        # never shows up.
        try:
            el.wait_for(state="attached", timeout=1200)
        except Exception:
            return None
        # Short timeout: during virtual-scroll a row can be mid-render or
        # detached. Return None so callers re-read on a later pass rather than
        # hanging 30s and dropping the whole chunk.
        return el.inner_text(timeout=2500).strip()
    except Exception:
        return None


def _drawer_open(page):
    return page.locator("div.MuiDrawer-root").count() > 0


def _close_filters_if_open(page):
    try:
        if _drawer_open(page):
            close_btn = page.locator("button[aria-label='Close']")
            if close_btn.count():
                close_btn.first.click(force=True)
            else:
                page.keyboard.press("Escape")
            page.wait_for_timeout(80)
    except Exception:
        pass


def _open_filters(page):
    _close_filters_if_open(page)
    btn = page.locator("button[aria-label='Table filters']").first
    try:
        btn.click(timeout=5_000)
    except Exception:
        try:
            handle = btn.element_handle(timeout=5_000)
            page.evaluate("(el) => el && el.click()", handle)
        except Exception:
            try:
                btn.click(force=True, timeout=5_000)
            except Exception as e:
                # If the button genuinely isn't on the page, we're not on the
                # worklist grid at all -- most likely a previous patient's
                # cleanup (go_back/facesheet-tab-close) left `page` stranded
                # on a stale route. Surface that instead of a bare 30s
                # locator-timeout so it's obvious this isn't a slow render.
                raise RuntimeError(
                    f"Table filters button not found (not on worklist grid?); "
                    f"current url={page.url!r}: {e!r}"
                ) from e
    page.wait_for_timeout(100)


def _close_filters(page):
    try:
        close_btn = page.locator("button[aria-label='Close']").first
        if close_btn.count():
            close_btn.click(force=True)
        else:
            page.keyboard.press("Escape")
    except Exception:
        page.keyboard.press("Escape")
    page.wait_for_timeout(100)


def ensure_worklist_filters_checked(page, group_names=("Providers", "Staff", "Rooms", "Service Locations")):
    """
    Open the Table filters drawer and select-all for each Provider/Staff/
    Room/Service Location group, if present on this grid — otherwise
    appointments outside whatever's checked by default are invisible in the
    worklist no matter how long you wait or scroll for them. Same idea as
    the dashboard's filter check (passes.py's _ensure_dashboard_filters),
    just for the Worklist/Appointments grid used for facesheet lookups.
    """
    _open_filters(page)
    for group_name in group_names:
        group = page.locator(f"[data-testid='{group_name}-checkbox-group']")
        if group.count() == 0:
            continue
        parent_cb = group.locator("input[type='checkbox']").first
        if parent_cb.count() and not parent_cb.is_checked():
            parent_cb.click(force=True)
            page.wait_for_timeout(200)
    _close_filters(page)
    wait_for_grid_settled(page)


def _wait_for_grid_content_stable(page, checks=3, interval_ms=250, max_polls=20):
    """
    "Some row exists" can be true from the PREVIOUS filter's rows while the
    new filter's request is still in flight — a stale-DOM race that makes
    row lookups (or a full-grid scrape) search data that's about to be
    replaced. Poll each row's data-id (MUI DataGrid's own row key, stable
    across any grid/columns) until the set stops changing across a few
    consecutive reads, so callers only proceed once the grid has actually
    caught up to the latest filter/date change.
    """
    prev = None
    stable = 0
    for _ in range(max_polls):
        try:
            ids = page.evaluate(
                "() => Array.from(document.querySelectorAll('.MuiDataGrid-row'))"
                ".map(r => r.getAttribute('data-id'))"
            )
        except Exception:
            ids = None
        if ids is not None and ids == prev:
            stable += 1
            if stable >= checks:
                return
        else:
            stable = 0
        prev = ids
        page.wait_for_timeout(interval_ms)


def wait_for_grid_settled(page, timeout_ms=60_000, max_retries=3):
    for attempt in range(1, max_retries + 1):
        try:
            page.wait_for_selector(".MuiDataGrid-virtualScroller", timeout=timeout_ms)
            page.wait_for_function(
                """
                () => {
                  const hasRow = document.querySelectorAll('.MuiDataGrid-row').length > 0;
                  const noRows =
                    !!document.querySelector('.MuiDataGrid-overlayWrapper') ||
                    !!document.querySelector('[class*="MuiDataGrid-overlay"]') ||
                    (document.body && document.body.innerText && document.body.innerText.includes('No rows'));
                  return hasRow || noRows;
                }
                """,
                timeout=timeout_ms,
            )
            page.wait_for_timeout(150)
            _wait_for_grid_content_stable(page)
            return
        except Exception:
            if attempt < max_retries:
                print(f"[GRID] Timeout on attempt {attempt}/{max_retries}, refreshing page ...")
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(2000)
            else:
                raise


def apply_date_filter(page, from_date, to_date):
    _open_filters(page)
    inputs = page.locator("input[placeholder='MM/DD/YYYY']")
    slow_fill(inputs.nth(0), from_date.strftime("%m/%d/%Y"))
    slow_fill(inputs.nth(1), to_date.strftime("%m/%d/%Y"))
    _close_filters(page)
    wait_for_grid_settled(page)


def find_row_by_appt_id_with_scroll(page, appt_id, max_scrolls=120):
    grid = page.locator(".MuiDataGrid-virtualScroller")
    grid.wait_for(state="visible", timeout=30_000)

    for _ in range(max_scrolls):
        id_locator = page.locator(
            f"div[data-field='APPOINTMENT_ID'] >> text=\"{appt_id}\""
        )
        if id_locator.count() > 0:
            row = id_locator.first.locator(
                "xpath=ancestor::div[contains(@class,'MuiDataGrid-row')]"
            )
            row.scroll_into_view_if_needed()
            page.wait_for_timeout(80)
            return row

        page.evaluate("""
            () => {
                const g = document.querySelector('.MuiDataGrid-virtualScroller');
                g.scrollTop += g.clientHeight;
            }
        """)
        page.wait_for_timeout(120)

    return None


def click_patient_row(page, row):
    """
    Returns (facesheet_page, opened_new_tab: bool).

    Closes any stray tab opened by a failed attempt so tabs don't accumulate
    across a long run (open tabs slow the browser and cascade failures).
    """
    link_btn = row.locator("button.MuiLink-button").first
    if link_btn.count() == 0:
        link_btn = row.locator("div[data-field='PATIENT_NAME']").first

    link_btn.scroll_into_view_if_needed()

    pages_before = set(page.context.pages)

    def _cleanup_stray(keep=None):
        for pg in page.context.pages:
            if pg not in pages_before and pg is not keep and pg is not page:
                try:
                    pg.close()
                except Exception:
                    pass

    # Attempt 1: opens in a new tab
    try:
        with page.context.expect_page(timeout=6_000) as p:
            link_btn.click(force=True)
        fs = p.value
        try:
            fs.wait_for_load_state("domcontentloaded")
            fs.wait_for_url("**/Facesheet/**", timeout=15_000)
        except Exception:
            pass
        if "/Facesheet/" in fs.url:
            return fs, True
        _cleanup_stray()  # opened a tab but it wasn't a facesheet
    except Exception:
        _cleanup_stray()

    # Attempt 2: same-tab navigation
    try:
        link_btn.click(force=True)
        page.wait_for_url("**/Facesheet/**", timeout=15_000)
        if "/Facesheet/" in page.url:
            _cleanup_stray(keep=page)  # in case the click also spawned a tab
            return page, False
    except Exception:
        pass

    _cleanup_stray()
    raise RuntimeError("Unable to open facesheet")


def scrape_tebra_patient_id(fs_page):
    """
    From an open facesheet/chart page, navigate to Demographics, scrape the
    Tebra Patient ID, then return None on any failure.
    """
    try:
        demo_link = fs_page.locator(
            "a[data-testid='clinical-page-nav-link-demographics']"
        )
        try:
            demo_link.wait_for(state="visible", timeout=5_000)
        except Exception:
            print("[PATIENT_ID] Demographics link not found (timeout)")
            return None

        demo_link.click()
        fs_page.wait_for_load_state("domcontentloaded")
        fs_page.wait_for_timeout(300)

        pair = fs_page.locator(
            "div.pair:has(div.label:has-text('Tebra Patient ID'))"
        )
        try:
            pair.wait_for(state="visible", timeout=3_000)
        except Exception:
            print("[PATIENT_ID] Tebra Patient ID pair not found (timeout)")
            return None

        raw = pair.locator("div.value").inner_text().strip().replace("\xa0", "").strip()
        print(f"[PATIENT_ID] Scraped: {raw}")
        return raw if raw else None
    except Exception as e:
        print(f"[PATIENT_ID ERROR] {e}")
        return None


def scrape_virtual_grid(page, extract_fn, max_scrolls=300, repair_passes=2, max_extra_sweeps=8):
    """
    Scroll a MUI virtual DataGrid top-to-bottom, collecting extract_fn(row)
    keyed by appt_id.

    A single top-to-bottom sweep with a full-screen, zero-overlap scroll step
    reads almost every row exactly once. If a field misses on that one read
    (cell() catching a row mid-render -- see cell()'s docstring), there is no
    second chance and it's recorded as None/"" forever, even though Tebra's
    own data isn't actually missing. To make that field-level flakiness
    self-heal for real, do one or more full extra sweeps after the first,
    each independently re-reading every row and patching only fields that
    are still None/"" -- a field that already has a value is never
    overwritten, so a bad read on a later sweep can't clobber a good one.

    Confirmed live (2026-09-11, PrePost+Tennessee 2026-05-28): the grid's
    virtualization can also drop a row from the sweep ENTIRELY -- not just
    blank a field on a row it did render -- when scroll timing races
    Tebra's own render (13 real appointments on Tebra, only 9 captured by a
    single sweep). A row that's simply never seen has no None/"" fields to
    trigger a repair sweep, so the old "only repair if some field is
    blank" gate could skip re-sweeping altogether and that row was gone for
    good.

    This used to be a bounded, best-effort retry (repair_passes) with no way
    to know if it actually caught everything -- just hoping a couple of
    extra sweeps were enough. The grid tells us the real answer: MUI stamps
    `aria-rowcount` on `.MuiDataGrid-main`, one more than the actual data row
    count (the ARIA header-row convention) -- confirmed live, 13 real rows
    reported as aria-rowcount=14. So instead of guessing, sweep again
    whenever `len(seen)` is still short of that expected count (bounded by
    max_extra_sweeps so a page that never reports a sane count, or is
    genuinely stuck, can't loop forever) -- each sweep independent and
    additive-only via _merge, so it can only add missing rows/fields, never
    overwrite a good read. If it still can't reach the expected count after
    the bound, that's logged loudly instead of silently returning short.
    """
    seen = {}

    def _merge(rec):
        appt_id = rec.get("appt_id")
        if not appt_id:
            return
        prev = seen.get(appt_id)
        if prev:
            rec = {k: (v if v not in (None, "") else prev.get(k)) for k, v in rec.items()}
        seen[appt_id] = rec

    def _scroll_top():
        return page.evaluate(
            "() => { const g = document.querySelector('.MuiDataGrid-virtualScroller'); "
            "return g ? g.scrollTop : 0; }"
        )

    def _scroll_to(pos):
        page.evaluate(
            "(p) => { const g = document.querySelector('.MuiDataGrid-virtualScroller'); "
            "if (g) g.scrollTop = p; }",
            pos,
        )

    def _scroll_forward():
        page.evaluate(
            "() => { const g = document.querySelector('.MuiDataGrid-virtualScroller'); "
            "if (g) g.scrollTop += g.clientHeight; }"
        )

    def _scan_current():
        for r in page.locator(".MuiDataGrid-row").all():
            try:
                _merge(extract_fn(r))
            except Exception:
                continue

    def _wait_for_rows_settled(poll_ms=50, stable_polls_needed=2, max_wait_ms=2000):
        """Wait for the currently-mounted `.MuiDataGrid-row` count to stop
        changing, instead of a blind fixed sleep -- attacks the actual race
        directly (we used to scroll again before Tebra finished mounting
        rows at the new position, so a fixed 150ms either wasted time on a
        fast render or wasn't enough on a slow one). Polls the row count at
        poll_ms intervals; considers it settled once it reads the same count
        stable_polls_needed times in a row. Bounded by max_wait_ms so a page
        that genuinely never settles can't hang the whole scrape."""
        last_count = -1
        stable_streak = 0
        elapsed = 0
        while elapsed < max_wait_ms:
            page.wait_for_timeout(poll_ms)
            elapsed += poll_ms
            count = page.locator(".MuiDataGrid-row").count()
            if count == last_count:
                stable_streak += 1
                if stable_streak >= stable_polls_needed:
                    return
            else:
                stable_streak = 0
            last_count = count

    def _sweep():
        """One independent full top-to-bottom read of every currently-loaded row."""
        _scroll_to(0)
        _wait_for_rows_settled()
        prev_pos = -1
        for _ in range(max_scrolls):
            _scan_current()
            pos = _scroll_top()
            if pos == prev_pos:
                break  # scrollTop stopped advancing -- we've hit the bottom
            prev_pos = pos
            _scroll_forward()
            _wait_for_rows_settled()

    def _expected_count():
        """Tebra's own claimed row count for the current filter, straight from
        the grid -- MUI's aria-rowcount is (data rows + 1) for the header
        row. Returns None if the grid doesn't expose it (don't block on a
        signal that isn't there)."""
        try:
            raw = page.locator(".MuiDataGrid-main").first.get_attribute("aria-rowcount")
            return int(raw) - 1 if raw else None
        except Exception:
            return None

    _sweep()
    expected = _expected_count()
    prev_count = len(seen)

    for extra_sweeps in range(1, max_extra_sweeps + 1):
        fields_incomplete = any(v in (None, "") for rec in seen.values() for v in rec.values())
        short_of_expected = expected is not None and len(seen) < expected
        # Keep sweeping while there's a known reason to (blank fields, or
        # confirmed-short of Tebra's own count), or while we're still inside
        # the unconditional minimum (repair_passes) that guards the case
        # where the grid doesn't expose aria-rowcount at all.
        if not fields_incomplete and not short_of_expected and extra_sweeps > repair_passes:
            break
        _sweep()
        new_count = len(seen)
        no_progress = new_count == prev_count
        prev_count = new_count
        if no_progress and not fields_incomplete and not short_of_expected:
            break  # this sweep found nothing new and there's no known gap left -- done
    else:
        if expected is not None and len(seen) < expected:
            print(f"[GRID] WARNING scrape_virtual_grid still short after "
                  f"{max_extra_sweeps} extra sweeps: got {len(seen)}, "
                  f"Tebra grid claims {expected}")

    if expected is not None and len(seen) < expected:
        print(f"[GRID] WARNING scrape_virtual_grid returning {len(seen)} rows, "
              f"Tebra grid claims {expected} (aria-rowcount)")

    return seen
