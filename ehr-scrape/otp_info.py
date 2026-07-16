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

def handle_tebra_otp_if_present(page, fetch_latest_otp_code_fn, *, since_dt_utc=None, poll_seconds=60):

    otp_form = page.locator("form[name='Two-Factor Authentication Method Form']")
    otp_heading = page.locator("h2:has-text('Two-Factor Authentication')")

    if not (_is_visible(otp_form, 1200) or _is_visible(otp_heading, 1200)):
        return False

    print("[OTP] Two-Factor Authentication modal detected")

    email_radio = page.locator("input[name='Two-Factor Authentication Method'][value='EMAIL']")
    if email_radio.count():
        try:
            email_radio.check(force=True)
        except Exception:
            page.locator("label:has(input[value='EMAIL'])").first.click(force=True)

    continue_btn = page.locator("form[name='Two-Factor Authentication Method Form'] button[type='submit']:has-text('Continue')")
    if continue_btn.count():
        continue_btn.first.click(force=True)
    else:
        page.locator("button[type='submit']:has-text('Continue')").first.click(force=True)

    code_input = page.locator("#mfa-confirmation-form-code-input")
    code_input.wait_for(state="visible", timeout=30_000)

    if since_dt_utc is None:
        since_dt_utc = datetime.now(timezone.utc) - timedelta(minutes=2)

    print(f"[OTP] Fetching code from inbox (since {since_dt_utc.isoformat()})")
    code = fetch_latest_otp_code_fn(since_dt_utc=since_dt_utc, poll_seconds=poll_seconds)

    if not code or not re.fullmatch(r"\d{6}", code):
        raise RuntimeError(f"[OTP] Invalid code returned: {code}")

    code_input.click()
    code_input.press("Control+A")
    code_input.press("Backspace")
    code_input.fill(code)

    confirm_btn = page.locator("button[type='submit']:has-text('Confirm')")
    confirm_btn.wait_for(state="visible", timeout=15_000)
    confirm_btn.click(force=True)

    try:
        page.wait_for_selector("#mfa-confirmation-form-code-input", state="detached", timeout=30_000)
    except Exception:
        pass

    print("[OTP] Confirmed successfully")
    return True