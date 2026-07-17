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
        if not el.count():
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
            handle = btn.element_handle()
            page.evaluate("(el) => el && el.click()", handle)
        except Exception:
            btn.click(force=True)
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


def scrape_virtual_grid(page, extract_fn, max_scrolls=300):
    """Scroll a MUI virtual DataGrid, collecting extract_fn(row) keyed by appt_id."""
    seen = {}
    stable = 0
    last_count = 0

    for _ in range(max_scrolls):
        rows = page.locator(".MuiDataGrid-row")
        for r in rows.all():
            try:
                rec = extract_fn(r)
            except Exception:
                continue
            if rec.get("appt_id"):
                seen[rec["appt_id"]] = rec

        stable = stable + 1 if len(seen) == last_count else 0
        if stable >= 5:
            break

        last_count = len(seen)
        page.evaluate("""
            () => {
                const g = document.querySelector('.MuiDataGrid-virtualScroller');
                g.scrollTop += g.clientHeight;
            }
        """)
        page.wait_for_timeout(150)

    return seen
