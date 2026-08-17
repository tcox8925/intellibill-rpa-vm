"""Chrome profile reuse + Playwright/CDP connection lifecycle."""

import argparse
import os
import shutil
import subprocess
import time
from typing import Optional

from playwright.sync_api import BrowserContext, Locator, Page, TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from pf_sync_pkg.constants import (
    BUILD_ID,
    DEFAULT_TIMEOUT,
    LOGIN_URL,
    PF_LOGIN_BUTTON_SELECTOR,
    PF_PASSWORD_SELECTOR,
    PF_USERNAME_SELECTOR,
    PROFILE_CACHE_IGNORE,
)

# Only build_browser/close_browser mutate this; both live in this module so the
# global stays single-owner instead of drifting between two copies.
_CHROME_PROC: Optional[subprocess.Popen] = None


def _pf_headless() -> bool:
    """Whether to launch Chrome with --headless=new instead of a real, visible
    window. Mirrors myops/ehr/config.py's EHR_PLAYWRIGHT_HEADLESS parsing
    (same truthy strings), but the *default* here is the opposite of Tebra's:
    headed unless explicitly turned on, because Practice Fusion's OTP/
    security-check step has no automated reader (see wait_for_pf_login below)
    -- a human has to see and solve it the first time (and again whenever PF's
    "remember this device" session lapses), which requires a real/visible
    window, not a headless one. Only set PF_PLAYWRIGHT_HEADLESS=true in .env
    AFTER that first login has already succeeded in this exact
    chrome_user_data_dir, so ongoing automated runs skip past the login form
    without ever hitting the OTP screen. If PF ever does re-challenge OTP
    while this is headless, wait_for_pf_login() will just time out after
    login_timeout_seconds with no way for anyone to see or solve it -- flip
    this back to false (and use the Xvfb+VNC setup in myops/DEPLOYMENT.md) to
    re-authenticate, then switch back to headless once it succeeds again.
    """
    raw = os.environ.get("PF_PLAYWRIGHT_HEADLESS", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def find_chrome_exe(explicit: str = "") -> str:
    if explicit and os.path.isfile(explicit):
        return explicit
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    # NOT SystemExit -- see wait_devtools' comment above for why: a BaseException
    # here silently escapes server.py's dispatch code instead of surfacing as a
    # clean error.
    raise RuntimeError("Could not locate Google Chrome. Pass --chrome-exe explicitly.")


def clone_profile_if_needed(args: argparse.Namespace) -> None:
    if args.attach:
        return
    destination_root = os.path.abspath(args.chrome_user_data_dir)
    destination_profile = os.path.join(destination_root, "Default")
    if os.path.isdir(destination_profile) and not args.refresh_profile:
        print(f"Reusing saved Practice Fusion Chrome profile: {destination_profile}")
        return

    os.makedirs(destination_root, exist_ok=True)
    if not args.source_user_data_dir:
        print(
            "No source profile supplied. A dedicated profile will be retained after "
            "the first manual Practice Fusion login."
        )
        return

    source_profile = os.path.join(
        os.path.abspath(args.source_user_data_dir), args.source_profile
    )
    if not os.path.isdir(source_profile):
        # NOT SystemExit -- see wait_devtools' comment above for why.
        raise RuntimeError(f"Source Chrome profile does not exist: {source_profile}")
    if args.refresh_profile and os.path.isdir(destination_profile):
        shutil.rmtree(destination_profile, ignore_errors=True)

    print(f"Cloning Chrome profile:\n  from: {source_profile}\n  to:   {destination_profile}")
    try:
        shutil.copytree(
            source_profile,
            destination_profile,
            ignore=PROFILE_CACHE_IGNORE,
            dirs_exist_ok=True,
        )
    except PermissionError as exc:
        # NOT SystemExit -- see wait_devtools' comment above for why.
        raise RuntimeError(
            "Chrome profile files are locked. Close every Chrome window and try again."
        ) from exc

    source_local_state = os.path.join(args.source_user_data_dir, "Local State")
    if os.path.isfile(source_local_state):
        shutil.copy2(source_local_state, os.path.join(destination_root, "Local State"))
    print("Profile clone complete. Later runs will reuse this dedicated profile.")


def wait_devtools(endpoint: str, timeout_seconds: float = 30.0) -> None:
    import urllib.request

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(endpoint + "/json/version", timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.4)
    # NOT SystemExit -- this used to raise SystemExit, which is a BaseException,
    # not an Exception. That silently escaped server.py's `except Exception`
    # handlers (e.g. _dispatch_browser_job's background-thread wrapper), got
    # swallowed by Python's default handling of an uncaught SystemExit in a
    # non-main thread, and left the dispatch code with no recorded result OR
    # error -- surfacing as a confusing `KeyError: 'result'` instead of this
    # message. A normal exception propagates correctly through both the CLI
    # and the server's job dispatch.
    raise RuntimeError(f"Chrome DevTools endpoint did not start: {endpoint}")


def is_logged_in(page: Page) -> bool:
    try:
        url = (page.url or "").lower()
        if "securitycheck" in url or "/login" in url:
            return False
        if "#/pf" in url:
            return True
        if page.locator("a[data-tracking='Reports']").count():
            return True
    except Exception:
        pass
    return False


def find_logged_in_page(context: BrowserContext) -> Optional[Page]:
    for candidate in list(context.pages):
        if is_logged_in(candidate):
            return candidate
    return None


def _type_login_value(locator: Locator, value: str, delay_ms: int, label: str = "field") -> None:
    """Enter a login value through regular keyboard/input events, verifying it took.

    v5.17: confirmed live -- Chrome's own autofill/password-manager can repopulate
    this field asynchronously, racing with Control+A/Delete/.type(). Observed result:
    the field ended up holding the OLD autofilled email concatenated with a partially
    retyped copy of the new one ("tcox@thrivmd.com" + "cox@thrivmd.com" glued together
    into one bad address), which Practice Fusion then correctly rejected as wrong
    credentials -- the login failure was a garbled field, not a security block. The
    field's actual value is now read back after typing and compared to what was
    intended; a mismatch clears and retypes (autofill losing the race a second time is
    unlikely) rather than submitting an unverified credential.
    """
    locator.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
    for _ in range(4):
        locator.click()
        locator.press("Control+A")
        locator.press("Delete")
        # Belt-and-suspenders: also clear the underlying DOM value directly. Autofill
        # can set el.value without going through the key events Control+A/Delete rely
        # on, so a keyboard-only clear can leave stale autofilled text in place.
        try:
            locator.evaluate(
                """
                el => {
                    el.value = '';
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                }
                """
            )
        except Exception:
            pass
        locator.type(value, delay=max(0, int(delay_ms)))
        try:
            current = locator.input_value()
        except Exception:
            current = None
        if current == value:
            locator.press("Tab")
            return
    raise RuntimeError(
        f"LOGIN_FIELD_NOT_SETTABLE: could not get the {label} field to hold the "
        "intended value after retries (Chrome autofill likely interfering). "
        "Never submitted -- credentials were not sent."
    )


def _find_login_page(context: BrowserContext) -> Optional[Page]:
    for candidate in list(context.pages):
        try:
            if candidate.locator(PF_PASSWORD_SELECTOR).count():
                return candidate
        except Exception:
            continue
    return None


def wait_for_pf_login(context: BrowserContext, page: Page, args: argparse.Namespace) -> Page:
    """Reuse an authenticated tab or perform the PF username/password login.

    Credentials come from --username/--password or PF_USERNAME/PF_PASSWORD. The
    function automatically continues when any Chrome tab exposes the authenticated
    Practice Fusion EHR. If PF requests OTP, it waits without requiring a console
    ENTER; OTP itself is not bypassed.
    """
    found = find_logged_in_page(context)
    if found is not None:
        found.bring_to_front()
        return found

    username = (getattr(args, "username", "") or os.getenv("PF_USERNAME", "")).strip()
    password = getattr(args, "password", "") or os.getenv("PF_PASSWORD", "")
    timeout_seconds = max(30, int(getattr(args, "login_timeout_seconds", 900)))
    typing_delay_ms = max(0, int(getattr(args, "typing_delay_ms", 65)))

    try:
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
    except Exception:
        pass

    deadline = time.time() + timeout_seconds
    submitted = False
    submitted_at = 0.0
    last_status = 0.0

    while time.time() < deadline:
        found = find_logged_in_page(context)
        if found is not None:
            found.bring_to_front()
            print(f"Practice Fusion authenticated. Active tab: {found.url}")
            return found

        login_page = _find_login_page(context) or page
        login_url = (login_page.url or "").lower()

        try:
            password_field = login_page.locator(PF_PASSWORD_SELECTOR).first
            login_form_visible = password_field.count() and password_field.is_visible()
        except Exception:
            login_form_visible = False

        if login_form_visible and not submitted:
            if not username or not password:
                raise ValueError(
                    "Practice Fusion login form is visible, but credentials are missing. "
                    "Set PF_USERNAME and PF_PASSWORD before running, or pass --username "
                    "and --password (environment variables are safer)."
                )

            print(f"[{BUILD_ID}] Practice Fusion login form detected; entering saved credentials...")
            username_field = login_page.locator(PF_USERNAME_SELECTOR).first
            _type_login_value(username_field, username, typing_delay_ms, label="username/email")
            _type_login_value(password_field, password, typing_delay_ms, label="password")

            login_button = login_page.locator(PF_LOGIN_BUTTON_SELECTOR).first
            login_button.wait_for(state="visible", timeout=DEFAULT_TIMEOUT)
            try:
                login_button.click(timeout=DEFAULT_TIMEOUT)
            except Exception:
                login_button.evaluate("el => el.click()")
            submitted = True
            submitted_at = time.time()
            print("Credentials submitted; waiting for the authenticated EHR...")

        if "securitycheck" in login_url:
            now = time.time()
            if now - last_status >= 10:
                print(
                    "Practice Fusion security verification/OTP is open. "
                    "Waiting automatically for the authenticated EHR..."
                )
                last_status = now
        elif submitted and time.time() - submitted_at > 45 and login_form_visible:
            raise RuntimeError(
                "Practice Fusion remained on the login form after credentials were "
                "submitted. Verify PF_USERNAME/PF_PASSWORD and inspect the Chrome window "
                "for an error message or security challenge."
            )

        time.sleep(0.5)

    raise PWTimeout(
        f"Timed out after {timeout_seconds} seconds waiting for an authenticated "
        "Practice Fusion tab."
    )


def build_browser(args: argparse.Namespace):
    global _CHROME_PROC
    port = int(args.debug_port)
    endpoint = f"http://127.0.0.1:{port}"

    if args.attach:
        print(f"Attaching Playwright to existing Chrome at {endpoint}")
        wait_devtools(endpoint, timeout_seconds=8)
        playwright = sync_playwright().start()
        browser = playwright.chromium.connect_over_cdp(endpoint)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = find_logged_in_page(context) or (
            context.pages[0] if context.pages else context.new_page()
        )
        page.set_default_timeout(DEFAULT_TIMEOUT)
        page.set_default_navigation_timeout(DEFAULT_TIMEOUT)
        return playwright, context, page

    clone_profile_if_needed(args)
    user_data_dir = os.path.abspath(args.chrome_user_data_dir)
    chrome_exe = find_chrome_exe(args.chrome_exe)
    command = [
        chrome_exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--profile-directory=Default",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-notifications",
        "--start-maximized",
        "about:blank",
    ]
    headless = _pf_headless()
    if headless:
        # Real Chrome's own --headless=new flag (not Playwright's bundled
        # browser -- this is still the same chrome_exe/profile as the headed
        # path above, just rendering off-screen instead of into a real/Xvfb
        # display) -- see _pf_headless()'s docstring for when this is safe.
        command.append("--headless=new")
    print(
        "Launching Chrome with the reusable Practice Fusion profile"
        + (" (headless)..." if headless else "...")
    )
    _CHROME_PROC = subprocess.Popen(command)
    wait_devtools(endpoint)
    playwright = sync_playwright().start()
    browser = playwright.chromium.connect_over_cdp(endpoint)
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.pages[0] if context.pages else context.new_page()
    page.set_default_timeout(DEFAULT_TIMEOUT)
    page.set_default_navigation_timeout(DEFAULT_TIMEOUT)
    return playwright, context, page


def close_browser(args: argparse.Namespace, playwright, context: BrowserContext, page: Page) -> None:
    global _CHROME_PROC
    if args.attach:
        print("Detaching Playwright; existing Chrome remains open.")
        try:
            playwright.stop()
        except Exception:
            pass
        return
    if args.keep_browser_open:
        print("Chrome left open because --keep-browser-open was supplied.")
        try:
            playwright.stop()
        except Exception:
            pass
        return
    print("Closing Chrome cleanly and preserving the Practice Fusion session...")
    try:
        cdp = context.new_cdp_session(page)
        cdp.send("Browser.close")
    except Exception:
        pass
    try:
        playwright.stop()
    except Exception:
        pass
    if _CHROME_PROC is not None:
        try:
            _CHROME_PROC.wait(timeout=15)
        except Exception:
            try:
                _CHROME_PROC.terminate()
            except Exception:
                pass
