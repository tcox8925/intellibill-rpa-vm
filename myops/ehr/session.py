"""
Browser session lifecycle: launch, login, practice-select (+OTP), practice
discovery, and local-dir cleanup. Moved from tebra_rpa.py. One place owns
how we get an authenticated Playwright page on a given practice.
"""

import os
import re
import shutil
import time
from datetime import datetime, timezone

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from .config import (
    LOGIN_URL, EMAIL, PASSWORD, DOWNLOAD_DIR, CST_TZ as CST,
)
from .browser import wait_for_grid_settled
from .matching import normalize_text

# OTP + email helpers live alongside the package (otp_info.py, email_read.py).
# Import defensively so a path/layout issue surfaces as a clear message at
# login time rather than an opaque crash on module import.
try:
    from otp_info import handle_tebra_otp_if_present
    from email_read import fetch_latest_tebra_otp_code
    _OTP_IMPORT_ERROR = None
except Exception as _e:  # pragma: no cover
    handle_tebra_otp_if_present = None
    fetch_latest_tebra_otp_code = None
    _OTP_IMPORT_ERROR = _e


class PracticeNotFoundError(RuntimeError):
    """The requested practice isn't available to this Tebra login (no
    matching tile / landed on a different single practice). pipeline.run()
    records this as *skipped* with the reason, not as a failure."""


def now_cst():
    return datetime.now(CST)


def normalize_practice_compare(text):
    """Lowercase and remove spaces only, preserving symbols like '+'."""
    return "".join(text.lower().split())


def login_and_select_practice(page, practice_name):
    """Log in and click into `practice_name`, handling OTP. Raises if the
    practice tile isn't found. Returns Tebra's own canonical practice text
    (the matched tile's text, or the single-practice dashboard header) --
    not necessarily identical to `practice_name`, which callers may pass in
    a normalized (lowercased/space-stripped) form -- so downstream DB
    writes/folder naming stay consistent regardless of how the caller spelled it.

    Confirmed live 2026-09-28/29: when the Tebra login resolves to a single
    practice, it skips the 'Practice select' tile picker entirely and lands
    straight on that practice's dashboard (URL .../scheduling/dashboard/...,
    header shows [data-testid='navigation-practice-name']) -- waiting only
    for the 'Practice select' h3 then hung for the full 30s timeout. Wait
    for 'Practice select' first as the normal case; only fall back to
    reading the single-practice dashboard header if that wait times out.

    Confirmed live 2026-10-01: an OTP challenge can appear some seconds
    after sign-in, not necessarily immediately -- same class of bug
    _get_discovered_practices() (pipeline.py) already had to handle for its
    own login, but a single _handle_otp() check right after the sign-in
    click only covers the first ~1.2s. If Tebra takes longer to render the
    modal than that (observed on the VM's network path), the check misses
    it, and it pops up moments later completely unhandled while this
    function sits blindly waiting on 'Practice select'/dashboard until the
    30s timeout. Poll for OTP over a longer window instead of checking once.
    """
    page.goto(LOGIN_URL)
    page.fill("#userName", EMAIL)
    page.fill("#password", PASSWORD)
    page.click("#sign-in")
    _wait_for_otp_then_resolve(
        page, until=f"h3:has-text('Practice select'), {_PRACTICE_LANDED_SELECTOR}"
    )

    target = normalize_text(practice_name)

    try:
        page.wait_for_selector("h3:has-text('Practice select')", timeout=30_000)
    except PlaywrightTimeoutError:
        single_practice = page.locator("[data-testid='navigation-practice-name']")
        if single_practice.count() == 0:
            raise
        landed = (single_practice.first.get_attribute("title") or single_practice.first.inner_text()).strip()
        norm_landed = normalize_text(landed)
        if target and (target in norm_landed or norm_landed in target):
            print(f"[LOGIN] Single-practice landing '{landed}' matched requested '{practice_name}'")
            _handle_otp(page)
            return landed
        raise PracticeNotFoundError(
            f"Practice '{practice_name}' not found in Tebra UI. "
            f"Account landed directly on single practice '{landed}' instead."
        )

    tiles = page.locator("h6.MuiTypography-subtitle2")
    n = tiles.count()

    # Pass 1: exact-ish substring on normalized text (either direction), same
    # strategy the SFTP folder matcher uses — so login and delivery agree.
    for i in range(n):
        tile_text = tiles.nth(i).inner_text().strip()
        norm = normalize_text(tile_text)
        if target and (target in norm or norm in target):
            print(f"[LOGIN] Matched practice tile '{tile_text}' for '{practice_name}'")
            tiles.nth(i).click()
            _wait_for_practice_landed(page, tile_text)
            return tile_text

    # No match: list what Tebra actually showed, to make the mismatch obvious.
    seen = [tiles.nth(i).inner_text().strip() for i in range(n)]
    raise PracticeNotFoundError(
        f"Practice '{practice_name}' not found in Tebra UI. "
        f"Tiles present: {seen}"
    )


_PRACTICE_LANDED_SELECTOR = "[data-testid='navigation-practice-name']"


def _wait_for_practice_landed(page, practice_name, timeout_s=60):
    """After clicking a practice tile, keep handling OTP until the practice's
    own shell (navigation header) has actually rendered.

    Seen 2026-10-06 (PrePost+Tennessee grid timeout): this used to be a single
    ~1.2s _handle_otp() check right after the tile click -- the same
    late-OTP gap the post-sign-in check had (see _wait_for_otp_then_resolve).
    When the OTP screen (or just a slow practice load) showed up after that
    window, we returned anyway, pass_appointments' goto_worklist() navigated
    away from a page that wasn't logged into the practice yet, and
    wait_for_grid_settled() reloaded that same non-grid screen 3x before
    failing with a bare '.MuiDataGrid-virtualScroller' 60s timeout."""
    deadline = time.monotonic() + timeout_s
    while True:
        handled = _wait_for_otp_then_resolve(
            page, poll_window_s=max(1, deadline - time.monotonic()),
            until=_PRACTICE_LANDED_SELECTOR,
        )
        if not handled:
            break
        # OTP handled -- loop once more in case the practice is still loading.
        if time.monotonic() >= deadline:
            break
    if page.locator(_PRACTICE_LANDED_SELECTOR).count() == 0:
        print(
            f"[LOGIN] WARNING practice '{practice_name}' header not visible "
            f"{timeout_s}s after tile click; url={page.url!r}"
        )


def _handle_otp(page):
    """Returns True if an OTP modal was present and got handled, False if
    none was visible at the moment of this call (not an error -- OTP is
    optional per login)."""
    otp_since = datetime.now(timezone.utc)
    if handle_tebra_otp_if_present is None:
        raise RuntimeError(
            "OTP helper unavailable — otp_info.py / email_read.py must sit "
            f"next to the ehr/ package. Import error was: {_OTP_IMPORT_ERROR!r}"
        )
    return handle_tebra_otp_if_present(
        page,
        fetch_latest_otp_code_fn=fetch_latest_tebra_otp_code,
        since_dt_utc=otp_since,
        # Confirmed live 2026-09-04: the actual Tebra Verification Code email
        # to this mailbox took 3+ minutes to arrive (login at 10:37:50, email
        # landed 10:41:08) -- 75s wasn't a bug in the polling logic, delivery
        # itself is just slower than that. 240s gives real headroom; the 5s
        # poll interval inside fetch_latest_tebra_otp_code_graph means this
        # still returns fast whenever the email shows up sooner.
        poll_seconds=240,
    )


def _wait_for_otp_then_resolve(page, poll_window_s=20, until=None):
    """Poll for the OTP modal for up to poll_window_s instead of checking
    once. Confirmed live 2026-10-01: a single _handle_otp() check right
    after the sign-in click can miss the modal if Tebra takes longer than
    its ~1.2s visibility check to render it (observed on the VM's network
    path) -- it then pops up moments later completely unhandled, while the
    caller sits blindly waiting on 'Practice select'/dashboard until its own
    30s timeout. Returns True if OTP was found and handled at any point in
    the window, False if it never appeared (normal -- OTP doesn't fire on
    every login).

    `until`: optional selector for the screen the caller expects to land on
    next. Once it's visible (and no OTP is showing) we stop polling early
    instead of burning the full window on every OTP-free login."""
    deadline = time.monotonic() + poll_window_s
    while True:
        if _handle_otp(page):
            return True
        if until and page.locator(until).count() > 0:
            return False
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(1000)


def discover_practices(page=None):
    """Read all practice names from the practice-select screen. If `page` is
    given it's assumed to already be at the select screen; otherwise this is
    called right after login.

    Confirmed live 2026-09-28/29: Tebra skips the 'Practice select' tile
    picker entirely and routes straight into a single practice's dashboard
    (.../scheduling/dashboard/day/..., header shows
    [data-testid='navigation-practice-name']) when the login only resolves
    to one practice -- same landing login_and_select_practice's docstring
    above handles. Wait for the picker as the normal case first; only fall
    back to reading the single-practice dashboard header if that wait
    times out, instead of hanging for the full timeout every time.
    """
    try:
        page.wait_for_selector("h3:has-text('Practice select')", timeout=30_000)
    except PlaywrightTimeoutError:
        single_practice = page.locator("[data-testid='navigation-practice-name']")
        if single_practice.count() == 0:
            raise
        landed = (single_practice.first.get_attribute("title") or single_practice.first.inner_text()).strip()
        print(f"[DISCOVER] Practice-select skipped, single-practice landing: '{landed}'")
        return [landed] if landed else []

    page.wait_for_timeout(2000)
    elements = page.locator("h6.MuiTypography-subtitle2")
    count = elements.count()
    print(f"[DISCOVER] Found {count} elements")
    practices = []
    for i in range(count):
        name = elements.nth(i).inner_text().strip()
        print(f"[DISCOVER]   {i}: '{name}'")
        if name:
            practices.append(name)
    print(f"[DISCOVER] Practices: {practices}")
    return practices


def resolve_practice_name(practice_name, practices):
    """Resolve a caller-supplied practice value to the canonical Tebra tile.

    Accepts normalized inputs like lowercase / no-space variants and returns
    the actual practice text shown by Tebra so downstream DB writes stay
    consistent.
    """
    target = normalize_practice_compare(practice_name)
    for practice in practices:
        norm = normalize_practice_compare(practice)
        if target and (target in norm or norm in target):
            return practice
    raise PracticeNotFoundError(
        f"Practice '{practice_name}' not found in Tebra UI. Tiles present: {practices}"
    )


def goto_worklist(page):
    page.goto("https://app.kareo.com/v2/#/worklist/appointments")
    wait_for_grid_settled(page)


def cleanup_acc_directory():
    print("[CLEANUP] Cleaning /acc root files (not subfolders)")
    if not os.path.isdir(DOWNLOAD_DIR):
        return
    for item in os.listdir(DOWNLOAD_DIR):
        full_path = os.path.join(DOWNLOAD_DIR, item)
        if os.path.isfile(full_path):
            try:
                os.remove(full_path)
            except Exception as e:
                print(f"[CLEANUP ERROR] {item}: {e}")


_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def practice_download_dir(entity: str, sub_entity: str, practice_name: str) -> str:
    """A per-(entity, sub_entity, practice) subfolder under DOWNLOAD_DIR.

    Confirmed live 2026-09-17: with DailyPdfLoaderJob.js now firing every
    practice's /run-tebra call as fire-and-forget instead of one-at-a-time,
    multiple practices' pipeline.run() calls can be mid-flight at once (the
    per-practice lock in myops/server.py only ever prevents the SAME
    practice from double-running). Every consumer of the shared DOWNLOAD_DIR
    root (this module's cleanup_acc_directory, passes.py's facesheet PDF
    writes, zipbuild.py's zip staging + PDF reads/removes) assumed exactly
    one run ever touched it at a time -- with two practices running
    concurrently, one finishing and cleaning up mid-scrape could silently
    delete another practice's just-downloaded, not-yet-zipped file. This
    gives each practice its own isolated folder instead, so pipeline.py can
    let practices run fully in parallel again (see cleanup_practice_download_dir
    below for how that folder gets torn down afterward) without reintroducing
    that race. Distinct (entity, sub_entity, practice_name) is enough to be
    unique even under full concurrency -- the SAME practice can't run twice
    at once regardless (see _acquire_key_lock in myops/server.py), so this
    never needs a run id/uuid on top.
    """
    slug = _UNSAFE_PATH_CHARS.sub("_", f"{entity}_{sub_entity}_{practice_name}").strip("_")
    path = os.path.join(DOWNLOAD_DIR, slug or "unknown_practice")
    os.makedirs(path, exist_ok=True)
    return path


def cleanup_practice_download_dir(dir_path: str) -> None:
    """Removes a practice_download_dir() folder entirely (files + the folder
    itself) once that practice's run is done. Safe to fully remove, unlike
    cleanup_acc_directory()'s shared-root sweep above -- nothing else shares
    this path across concurrent runs."""
    if not dir_path or not os.path.isdir(dir_path):
        return
    print(f"[CLEANUP] Removing practice download folder {dir_path}")
    try:
        shutil.rmtree(dir_path, ignore_errors=True)
    except Exception as e:
        print(f"[CLEANUP ERROR] {dir_path}: {e}")
