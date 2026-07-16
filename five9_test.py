import time
import os
import io
import base64
import shutil
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from PIL import Image, ImageOps
import pytesseract

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from azure.communication.email import EmailClient


# =========================
# CONFIG
# =========================
FIVE9_LOGIN_URL = "https://admin.us.five9.net/"

KEYVAULT_NAME = os.getenv("KEY_VAULT_NAME", "")
EMAIL_TO = ["tcox@834labs.com", "jfoster@834labs.com"]
CC = ["dataops@834labs.com"]

CST = ZoneInfo("America/Chicago")

# Timeouts (ms)
NAV_TIMEOUT   = 60_000
SHORT_TIMEOUT = 15_000
LONG_TIMEOUT  = 90_000

# Debug screenshots folder (set to None to disable)
DEBUG_DIR = os.path.join(os.path.dirname(__file__), "debug_screenshots")

# -------------------------
# OCR tuning
# -------------------------
# WebSwing renders the whole VCC Admin to a canvas, so there is no DOM to query
# and OCR is the only way to read the screen.
#
# We upscale + grayscale every screenshot before OCR (small UI text like
# "Actions" is otherwise unreadable), and we run TWO page-segmentation passes:
#   - psm 11 (sparse text): finds isolated labels/buttons such as "Test" that
#     the default layout analysis silently drops because they float alone in
#     whitespace. This was the root cause of "Could not find 'Test'".
#   - psm 3  (default auto): catches dense/multi-word text the sparse pass may
#     merge or miss.
# Tokens from both passes are merged. Coordinates come back in the UPSCALED
# space, so they are divided by OCR_SCALE to map to real viewport pixels.
OCR_SCALE = 3
OCR_CONF_THRESHOLD = 25
OCR_PSM_PRIMARY = 11
OCR_PSM_FALLBACK = 3

# Navigation
TARGET_STATE = "RECORDINGS_SFTP"   # the screen with the Test button
MAX_NAV_ITERS = 14                 # guard against spinning forever
MAX_UNKNOWN_STRIKES = 3            # how many UNKNOWN screens before failing loudly


# =========================
# KEYVAULT AUTH
# =========================
kv_url = f"https://{KEYVAULT_NAME}.vault.azure.net"
secret_client = SecretClient(vault_url=kv_url, credential=DefaultAzureCredential())

acs_conn_str   = secret_client.get_secret("ACS-EMAIL-CONNECTION-STRING").value
five9_username = secret_client.get_secret("dataops-five9-username").value
five9_password = secret_client.get_secret("dataops-five9-password").value


# =========================
# EMAIL CLIENT
# =========================
email_client = EmailClient.from_connection_string(acs_conn_str)
SENDER_ADDRESS = "dataops@834labs.com"


# =========================
# HELPERS
# =========================
def send_email(subject: str, body: str, attachment_path: str = None):
    if isinstance(EMAIL_TO, list):
        to_recipients = [{"address": a.strip()} for a in EMAIL_TO]
    else:
        to_recipients = [{"address": a.strip()} for a in EMAIL_TO.replace(";", ",").split(",")]

    if CC:
        if isinstance(CC, list):
            cc_recipients = [{"address": a.strip()} for a in CC]
        else:
            cc_recipients = [{"address": a.strip()} for a in CC.replace(";", ",").split(",")]
    else:
        cc_recipients = []

    recipients = {"to": to_recipients}
    if cc_recipients:
        recipients["cc"] = cc_recipients

    message = {
        "senderAddress": SENDER_ADDRESS,
        "recipients": recipients,
        "content": {
            "subject": subject,
            "plainText": body,
            "html": f"<html><body><pre>{body}</pre></body></html>",
        },
    }

    if attachment_path and os.path.exists(attachment_path):
        with open(attachment_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        message["attachments"] = [
            {
                "name": os.path.basename(attachment_path),
                "contentType": "image/png",
                "contentInBase64": encoded,
            }
        ]

    poller = email_client.begin_send(message)
    poller.result()


def debug_screenshot(page, step_name: str):
    """Save a debug screenshot if DEBUG_DIR is set."""
    if not DEBUG_DIR:
        return
    os.makedirs(DEBUG_DIR, exist_ok=True)
    safe_name = step_name.replace(" ", "_").replace("/", "_")
    path = os.path.join(DEBUG_DIR, f"{safe_name}.png")
    page.screenshot(path=path)
    print(f"  [screenshot] {path}")


# =========================
# OCR CORE
# =========================
def _preprocess_for_ocr(img: "Image.Image") -> "Image.Image":
    """Grayscale -> upscale -> autocontrast. Makes small UI text legible."""
    proc = img.convert("L")
    proc = proc.resize(
        (proc.width * OCR_SCALE, proc.height * OCR_SCALE),
        Image.LANCZOS,
    )
    proc = ImageOps.autocontrast(proc)
    return proc


def _ocr_tokens(page):
    """
    Screenshot the page once, preprocess, then run both OCR passes and merge
    their tokens. Returns a list of dicts with text + bounding box (in upscaled
    OCR coordinates) + confidence.
    """
    screenshot_bytes = page.screenshot()
    img = Image.open(io.BytesIO(screenshot_bytes))
    proc = _preprocess_for_ocr(img)

    tokens = []
    for psm in (OCR_PSM_PRIMARY, OCR_PSM_FALLBACK):
        data = pytesseract.image_to_data(
            proc, config=f"--psm {psm}", output_type=pytesseract.Output.DICT
        )
        for i, text in enumerate(data["text"]):
            txt = text.strip()
            if txt and int(data["conf"][i]) > OCR_CONF_THRESHOLD:
                tokens.append({
                    "text": txt,
                    "left": data["left"][i],
                    "top": data["top"][i],
                    "w": data["width"][i],
                    "h": data["height"][i],
                    "conf": int(data["conf"][i]),
                })
    return tokens


def ocr_words(page):
    """Return the list of words OCR currently sees (debug aid)."""
    return [t["text"] for t in _ocr_tokens(page)]


def _center_in_viewport(tok):
    """Convert an OCR token's box (upscaled space) to a real viewport center."""
    x = (tok["left"] + tok["w"] // 2) // OCR_SCALE
    y = (tok["top"] + tok["h"] // 2) // OCR_SCALE
    return (int(x), int(y))


def find_text_on_screen(page, target_text: str):
    """OCR the screen and return the center (real viewport px) of target_text."""
    tokens = _ocr_tokens(page)
    return _find_in_tokens(tokens, target_text)


def _find_in_tokens(tokens, target_text: str):
    """Locate target_text within an already-captured token list."""
    target_lower = target_text.lower().strip()

    # Exact single-token match
    for t in tokens:
        if t["text"].lower() == target_lower:
            return _center_in_viewport(t)

    # Multi-word fallback (e.g. "VCC Configuration")
    if " " in target_text:
        concat = " ".join(t["text"] for t in tokens).lower()
        if target_lower in concat:
            first_word = target_text.split()[0].lower()
            for t in tokens:
                if t["text"].lower() == first_word:
                    return _center_in_viewport(t)

    return None


def click_text(page, target_text: str, wait_after: float = 1.5):
    """Find text on screen via OCR and click its center."""
    pos = find_text_on_screen(page, target_text)
    if pos:
        page.mouse.click(pos[0], pos[1])
        time.sleep(wait_after)
        return True
    return False


def screen_contains_text(page, target_text: str) -> bool:
    """Check if the screen currently shows the target text."""
    return find_text_on_screen(page, target_text) is not None


def wait_for_text(page, target_text: str, timeout: float = 30,
                  interval: float = 2, label: str = None) -> bool:
    """Poll the screen via OCR until target_text appears or timeout (seconds)."""
    name = label or target_text
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        if screen_contains_text(page, target_text):
            return True
        print(f"  [wait] '{name}' not visible yet (attempt {attempt})")
        time.sleep(interval)
    return False


def wait_until_text_gone(page, target_text: str, timeout: float = 90,
                         interval: float = 2, label: str = None) -> bool:
    """Poll until target_text is NO LONGER on screen (e.g. loading splash)."""
    name = label or target_text
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not screen_contains_text(page, target_text):
            return True
        print(f"  [wait] '{name}' still present, waiting...")
        time.sleep(interval)
    return False


# =========================
# STATE DETECTION
# =========================
def detect_state(page):
    """
    Classify the current screen with a single OCR capture.

    All Configure-modal tab labels render at once, so we key on anchors that
    only exist in a particular state rather than on the tab strip:
      LOADING          - "Starting your application" splash
      POPUP            - Five9 announcement dialog (Acknowledge / Important)
      RESULT           - SFTP test result dialog (Successful / Failed / Incorrect)
      RECORDINGS_SFTP  - Test + SFTP visible  (the target screen)
      EXPORT_ACTIVE    - Export tab active (Recordings / Transcripts sub-tabs)
      CONFIG_OPEN      - modal open (Save + Exit + Export) on some other tab
      READY            - main app, Actions menu visible, no modal
      UNKNOWN          - none of the above
    Returns (state, tokens) so callers can reuse the capture if they want.
    """
    tokens = _ocr_tokens(page)
    words = {t["text"].lower() for t in tokens}
    has = lambda w: w.lower() in words

    if has("Starting"):
        return "LOADING", tokens
    if has("Acknowledge") or has("Important"):
        return "POPUP", tokens
    if has("Successful") or has("Incorrect") or (has("Login") and has("Failed")):
        return "RESULT", tokens

    config_open = has("Save") and has("Exit") and has("Export")

    if has("Test") and has("SFTP"):
        return "RECORDINGS_SFTP", tokens
    if config_open and (has("Recordings") or has("Transcripts")):
        return "EXPORT_ACTIVE", tokens
    if config_open:
        return "CONFIG_OPEN", tokens
    if has("Actions"):
        return "READY", tokens

    return "UNKNOWN", tokens


# =========================
# ACTIONS
# =========================
def dismiss_five9_popup(page, steps, watch_secs: float = 25) -> bool:
    """
    The "Important Message from Five9" popup appears asynchronously after load.
    Watch for it over a window and dismiss via Acknowledge (keyboard fallbacks).
    Returns True if nothing is blocking (dismissed or never appeared).
    """
    print("  [popup] Watching for Five9 announcement popup...")
    deadline = time.time() + watch_secs
    seen_popup = False

    while time.time() < deadline:
        has_ack = screen_contains_text(page, "Acknowledge")
        has_important = screen_contains_text(page, "Important")

        if has_ack:
            seen_popup = True
            if click_text(page, "Acknowledge", wait_after=3):
                if not screen_contains_text(page, "Acknowledge"):
                    steps.append("Dismissed Five9 popup via Acknowledge (OCR)")
                    return True
        elif has_important:
            seen_popup = True
            page.keyboard.press("Enter")
            time.sleep(2)
            if not screen_contains_text(page, "Important"):
                steps.append("Dismissed Five9 popup via Enter")
                return True
            page.keyboard.press("Escape")
            time.sleep(2)
            if not screen_contains_text(page, "Important"):
                steps.append("Dismissed Five9 popup via Escape")
                return True

        time.sleep(2)

    if seen_popup:
        steps.append("WARNING: Five9 popup detected but could not be dismissed")
        return False
    steps.append("No Five9 announcement popup appeared")
    return True


def open_configure(page, steps):
    """
    Open VCC Configuration. Prefer OCR (Actions -> Configure); fall back to the
    keyboard sequence (F10 -> Enter -> ArrowDown x9 -> Enter) if OCR navigation
    doesn't land on the modal.
    """
    if click_text(page, "Actions", wait_after=1.5):
        if wait_for_text(page, "Configure", timeout=6, interval=1,
                         label="Configure menu item"):
            if click_text(page, "Configure", wait_after=4):
                if wait_for_text(page, "Export", timeout=12, interval=2,
                                 label="Export tab (modal opened)"):
                    steps.append("Opened Configure via Actions menu (OCR)")
                    return
    steps.append("OCR menu navigation failed; using keyboard fallback")

    page.mouse.click(400, 400)   # focus the app
    time.sleep(1)
    page.keyboard.press("F10")
    time.sleep(1)
    page.keyboard.press("Enter")
    time.sleep(1)
    debug_screenshot(page, "04_actions_menu_open")
    for _ in range(9):
        page.keyboard.press("ArrowDown")
        time.sleep(0.3)
    page.keyboard.press("Enter")
    time.sleep(4)
    steps.append("Opened Configure via keyboard fallback")


def navigate_to_test_view(page, steps):
    """
    State-machine driver: detect where we are, do the one thing that advances
    us toward the SFTP Recordings view, re-detect, repeat. Idempotent — if the
    app is already on (or partway to) the target, the unneeded steps are
    skipped. Returns True if the target screen is reached.
    """
    unknown_strikes = 0

    for it in range(MAX_NAV_ITERS):
        state, _tokens = detect_state(page)
        steps.append(f"State: {state}")
        print(f"  [state] iter {it + 1}/{MAX_NAV_ITERS}: {state}")
        debug_screenshot(page, f"state_{it + 1:02d}_{state}")

        if state == TARGET_STATE:
            return True

        if state == "LOADING":
            wait_until_text_gone(page, "Starting", timeout=90, interval=2,
                                 label="loading splash")

        elif state == "POPUP":
            dismiss_five9_popup(page, steps, watch_secs=25)

        elif state == "RESULT":
            # Stale dialog from a previous run — clear it and re-test fresh.
            steps.append("Clearing stale result dialog (Enter)")
            page.keyboard.press("Enter")
            time.sleep(2)

        elif state == "READY":
            open_configure(page, steps)

        elif state == "CONFIG_OPEN":
            click_text(page, "Export", wait_after=2)
            wait_for_text(page, "Recordings", timeout=10, interval=2,
                          label="Recordings sub-tab")

        elif state == "EXPORT_ACTIVE":
            click_text(page, "Recordings", wait_after=1.5)
            wait_for_text(page, "Test", timeout=12, interval=2,
                          label="Test button")

        else:  # UNKNOWN
            unknown_strikes += 1
            if unknown_strikes >= MAX_UNKNOWN_STRIKES:
                steps.append(
                    f"Could not classify screen after {MAX_UNKNOWN_STRIKES} tries; aborting"
                )
                return False
            steps.append(f"UNKNOWN screen (strike {unknown_strikes}); waiting")
            time.sleep(3)

        time.sleep(1)

    steps.append(f"Reached iteration cap ({MAX_NAV_ITERS}) without target state")
    return False


# =========================
# HEALTH CHECK
# =========================
def run_health_check():
    """
    1. Login to Five9 admin portal
    2. Open VCC Administrator (WebSwing Java app)
    3. State-machine navigation to Export -> Recordings -> SFTP
    4. Click Test
    5. Read the SFTP test result dialog
    6. Return success/failure
    """
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, slow_mo=500)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            permissions=["clipboard-read", "clipboard-write"],
        )
        page = context.new_page()
        page.set_default_timeout(NAV_TIMEOUT)

        steps = []
        vcc_page = None

        try:
            # ═══════════════════════════════════════
            # PHASE 1 — Login to Five9
            # ═══════════════════════════════════════
            page.goto(FIVE9_LOGIN_URL, wait_until="networkidle")
            steps.append("Navigated to Five9 login")
            debug_screenshot(page, "01_login_page")

            page.wait_for_selector("#SignIn-username", timeout=SHORT_TIMEOUT)
            page.fill("#SignIn-username", five9_username)
            steps.append("Entered username")

            page.wait_for_selector(
                "#SignIn-action-button:not([disabled])", timeout=SHORT_TIMEOUT
            )
            page.click("#SignIn-action-button")
            steps.append("Clicked Next")

            page.wait_for_selector("#SignIn-password", timeout=SHORT_TIMEOUT)
            page.fill("#SignIn-password", five9_password)
            steps.append("Entered password")

            page.wait_for_selector(
                "#SignIn-action-button:not([disabled])", timeout=SHORT_TIMEOUT
            )
            page.click("#SignIn-action-button")
            steps.append("Clicked Sign In")

            page.wait_for_selector(".Home-cards-container", timeout=LONG_TIMEOUT)
            steps.append("Dashboard loaded")
            debug_screenshot(page, "02_dashboard")

            # ═══════════════════════════════════════
            # PHASE 2 — Open VCC Administrator
            # ═══════════════════════════════════════
            with context.expect_page(timeout=LONG_TIMEOUT) as new_page_info:
                page.click("#APP_LAUNCHER_KEY_LEGACY_ADMIN")

            vcc_page = new_page_info.value
            steps.append("Clicked VCC Administrator card")

            context.grant_permissions(
                ["clipboard-read", "clipboard-write"],
                origin="https://webswing.prod.us.five9.net",
            )

            vcc_page.wait_for_load_state("networkidle", timeout=LONG_TIMEOUT)
            steps.append("WebSwing VCC Admin page loaded")
            debug_screenshot(vcc_page, "03_vcc_admin_loaded")

            try:
                print(f"  [OCR debug] Words now on screen: {ocr_words(vcc_page)[:50]}")
            except Exception as e:
                print(f"  [OCR debug] Failed: {e}")

            # ═══════════════════════════════════════
            # PHASE 3 — State-machine navigation to the test view
            # ═══════════════════════════════════════
            reached = navigate_to_test_view(vcc_page, steps)
            debug_screenshot(vcc_page, "09_after_navigation")

            if not reached:
                err_path = None
                if DEBUG_DIR:
                    os.makedirs(DEBUG_DIR, exist_ok=True)
                    err_path = os.path.join(DEBUG_DIR, "error_state.png")
                    vcc_page.screenshot(path=err_path)
                steps.append("SFTP Test: COULD NOT REACH TEST VIEW")
                return False, steps, "Could not reach the SFTP Recordings test view", err_path

            # ═══════════════════════════════════════
            # PHASE 4 — Click Test
            # ═══════════════════════════════════════
            if click_text(vcc_page, "Test", wait_after=2):
                steps.append("Clicked Test button (OCR)")
            else:
                steps.append("WARN: state was target but 'Test' click failed")
            debug_screenshot(vcc_page, "10_test_clicked")

            # ═══════════════════════════════════════
            # PHASE 5 — Read test result (poll; SFTP round-trip takes a moment)
            # ═══════════════════════════════════════
            print("  [wait] Waiting for SFTP test result dialog...")
            dialog_text = ""
            deadline = time.time() + 25
            while time.time() < deadline:
                if screen_contains_text(vcc_page, "Successful"):
                    dialog_text = "FTP Login Successful"
                    break
                if screen_contains_text(vcc_page, "Incorrect"):
                    dialog_text = "FTP Login Failed: Incorrect credentials"
                    break
                if screen_contains_text(vcc_page, "Failed"):
                    dialog_text = "FTP Login Failed"
                    break
                time.sleep(2)

            result_screenshot = os.path.join(DEBUG_DIR, "test_result.png") if DEBUG_DIR else None
            if result_screenshot:
                os.makedirs(DEBUG_DIR, exist_ok=True)
                vcc_page.screenshot(path=result_screenshot)

            if dialog_text:
                steps.append(f"Dialog result: {dialog_text}")
            else:
                steps.append("Could not read dialog result via OCR")

            test_passed = None
            if "failed" in dialog_text.lower() or "incorrect" in dialog_text.lower():
                test_passed = False
            elif "successful" in dialog_text.lower():
                test_passed = True

            # Dismiss the dialog
            vcc_page.keyboard.press("Enter")
            time.sleep(1)

            if test_passed is True:
                steps.append("SFTP Test: PASSED")
                return True, steps, dialog_text, result_screenshot
            elif test_passed is False:
                steps.append("SFTP Test: FAILED")
                return False, steps, dialog_text, result_screenshot
            else:
                steps.append("SFTP Test: UNCERTAIN")
                return False, steps, dialog_text, result_screenshot

        except Exception as e:
            steps.append(f"FAILED: {e}")
            err_screenshot = None
            try:
                if DEBUG_DIR:
                    os.makedirs(DEBUG_DIR, exist_ok=True)
                    err_screenshot = os.path.join(DEBUG_DIR, "error_state.png")
                    target = vcc_page if vcc_page is not None else page
                    target.screenshot(path=err_screenshot)
            except Exception:
                pass
            return False, steps, str(e), err_screenshot

        finally:
            browser.close()


# =========================
# MAIN
# =========================
def run():
    success, steps, error_text, screenshot_path = run_health_check()

    step_log = "\n".join(f"  [{i+1}] {s}" for i, s in enumerate(steps))
    print(step_log)

    if success:
        success_message = (
            "Hello,\n\n"
            "Test file ran successfully."
        )
        print("Five9 VCC Configuration Test Successful")
        send_email(
            "Five9 VCC Configuration Test Successful",
            success_message,
        )
    else:
        failure_message = (
            "Hello,\n\n"
            "VCC test file failed to send – please investigate.\n\n"
            f"Error: {error_text or 'Unknown'}"
        )
        print("Five9 VCC Configuration Test Failure")
        send_email(
            "Five9 VCC Configuration Test Failure",
            failure_message,
            attachment_path=screenshot_path,
        )

    # Clean up debug screenshots
    if DEBUG_DIR and os.path.isdir(DEBUG_DIR):
        shutil.rmtree(DEBUG_DIR, ignore_errors=True)


if __name__ == "__main__":
    run()