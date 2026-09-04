"""
Browser session lifecycle: launch, login, practice-select (+OTP), practice
discovery, and local-dir cleanup. Moved from tebra_rpa.py. One place owns
how we get an authenticated Playwright page on a given practice.
"""

import os
from datetime import datetime, timezone

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


def now_cst():
    return datetime.now(CST)


def normalize_practice_compare(text):
    """Lowercase and remove spaces only, preserving symbols like '+'."""
    return "".join(text.lower().split())


def login_and_select_practice(page, practice_name):
    """Log in and click into `practice_name`, handling OTP. Raises if the
    practice tile isn't found."""
    page.goto(LOGIN_URL)
    page.fill("#userName", EMAIL)
    page.fill("#password", PASSWORD)
    page.click("#sign-in")

    page.wait_for_selector("h3:has-text('Practice select')")
    target = normalize_text(practice_name)
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
            _handle_otp(page)
            return

    # No match: list what Tebra actually showed, to make the mismatch obvious.
    seen = [tiles.nth(i).inner_text().strip() for i in range(n)]
    raise RuntimeError(
        f"Practice '{practice_name}' not found in Tebra UI. "
        f"Tiles present: {seen}"
    )


def _handle_otp(page):
    otp_since = datetime.now(timezone.utc)
    if handle_tebra_otp_if_present is None:
        raise RuntimeError(
            "OTP helper unavailable — otp_info.py / email_read.py must sit "
            f"next to the ehr/ package. Import error was: {_OTP_IMPORT_ERROR!r}"
        )
    handle_tebra_otp_if_present(
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


def discover_practices(page=None):
    """Read all practice names from the practice-select screen. If `page` is
    given it's assumed to already be at the select screen; otherwise this is
    called right after login."""
    page.wait_for_selector("h3:has-text('Practice select')", timeout=30_000)
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
    raise RuntimeError(
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
