# otp_info.py
import re
import time
from datetime import datetime, timedelta, timezone

OTP_SUBJECT = "Tebra Verification Code"
OTP_SENDER = "no-reply@tebra.com"

def _is_visible(locator, timeout_ms=1500) -> bool:
    try:
        locator.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False

def handle_tebra_otp_if_present(page, fetch_latest_otp_code_fn, *, since_dt_utc=None, poll_seconds=240):
    # Confirmed live 2026-10-01 (myops/ehr/session.py's OTP handling kept
    # timing out even after retries -- the actual rendered screen was
    # captured and compared against what this function checked for): the
    # real challenge Tebra shows is NOT the method-picker this used to
    # assume (a form named 'Two-Factor Authentication Method Form' with an
    # 'h2:has-text('Two-Factor Authentication')' heading and EMAIL/SMS radio
    # buttons to choose from, then a single #mfa-confirmation-form-code-input
    # text field). Neither of those selectors ever matched anything -- this
    # function silently returned False on every real OTP challenge, no
    # matter how long anything polled for it.
    #
    # The real screen (rendered inside the Tebra sign-in page's <descope-wc>
    # web component, in its shadow DOM -- Playwright locators pierce this
    # fine, but a plain page.content()/outerHTML dump of the document won't
    # show it) already auto-sends the code and jumps straight to a "We've
    # sent a message containing a 6-digit code to <masked email>" / "Enter
    # Code" screen: 6 SEPARATE single-digit <input type="tel"
    # aria-label="passcode digit"> boxes. Their ids (Vaadin-generated, e.g.
    # "input-vaadin-text-field-33") are NOT stable across loads -- select by
    # aria-label, never by id. There is no visible submit/confirm button on
    # this screen; it auto-submits once the 6th digit lands.
    code_boxes = page.locator("input[aria-label='passcode digit']")
    if not _is_visible(code_boxes.first, 1200):
        return False

    print("[OTP] 6-digit code entry screen detected")

    if since_dt_utc is None:
        since_dt_utc = datetime.now(timezone.utc) - timedelta(minutes=2)

    print(f"[OTP] Fetching code from inbox (since {since_dt_utc.isoformat()})")
    code = fetch_latest_otp_code_fn(since_dt_utc=since_dt_utc, poll_seconds=poll_seconds)

    if not code or not re.fullmatch(r"\d{6}", code):
        raise RuntimeError(f"[OTP] Invalid code returned: {code}")

    n = code_boxes.count()
    if n != len(code):
        raise RuntimeError(
            f"[OTP] Expected {len(code)} passcode-digit boxes, found {n} -- "
            "Tebra's OTP screen markup may have changed again."
        )
    for i, digit in enumerate(code):
        box = code_boxes.nth(i)
        box.click()
        # press_sequentially (real keystrokes, not a direct value set) --
        # these boxes' auto-advance/auto-submit is driven by keyboard
        # events, not just the resulting value, so .fill() alone risks
        # never triggering the auto-submit.
        box.press_sequentially(digit)

    # No visible submit button on this screen -- it auto-submits once the
    # 6th digit is entered. Give it a moment; the caller's own post-login
    # wait (Practice select / dashboard) is what actually confirms success.
    try:
        code_boxes.first.wait_for(state="detached", timeout=15_000)
    except Exception:
        page.wait_for_timeout(2000)

    print("[OTP] Code entered")
    return True