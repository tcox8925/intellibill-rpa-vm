import os
import time
import sys
from playwright.sync_api import sync_playwright

# import Zoho helper
from .zoho_crm import update_contact_from_scrape

USERNAME = os.getenv("NIPR_USERNAME", "")
PASSWORD = os.getenv("NIPR_PASSWORD", "")


def normalize_dob(dob_raw):
    """
    Convert DOB from NIPR format (MM/DD/YYYY) -> Zoho format (YYYY-MM-DD)
    """
    try:
        month, day, year = dob_raw.split("/")
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
    except:
        return None


def run_scraper(npn: str, update_zoho: bool = True):
    print(f"Starting crawler for NPN: {npn}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=[
            "--disable-gpu",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ])
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        try:
            page.goto("https://pdb-reports.app.nipr.com/home", wait_until="networkidle")

            print("Logging in...")
            page.fill("#username", USERNAME)
            page.fill("#password", PASSWORD)
            page.get_by_role("button", name="Log in").click()
            page.wait_for_load_state("networkidle")

            # ---- Dismiss cookie banner if present ----
            try:
                page.locator(".cmplz-btn.cmplz-accept").click(timeout=3000)
                print("Cookie banner accepted")
            except Exception:
                pass

            # ---- Close any popup tabs ----
            for extra_page in context.pages[1:]:
                extra_page.close()

            print("Navigating to Create Report...")
            page.locator("a[href*='/create-report/detail-report']").click()
            page.wait_for_load_state("networkidle")

            # ---- Dismiss again in case it reappears ----
            try:
                page.locator(".cmplz-btn.cmplz-accept").click(timeout=2000)
            except Exception:
                pass
            for extra_page in context.pages[1:]:
                extra_page.close()

            print("Entering NPN...")
            page.locator("#npn").fill(npn)
            page.locator("button[data-testid='button_detail_person_search']").click()
            print("Searching...")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(3000)

            # Extract Resident State
            resident_input = page.locator(
                "xpath=//label[contains(text(),'Resident states')]/following::input[1]"
            )
            resident_input.wait_for(state="attached", timeout=10000)
            resident_state = (resident_input.input_value() or "").strip()

            # Extract DOB
            dob_input = page.locator(
                "xpath=//label[contains(text(),'DOB')]/following::input[1]"
            )
            dob_input.wait_for(state="attached", timeout=10000)
            dob = (dob_input.input_value() or "").strip()

            print("=======================================")
            print("Scrape Complete")
            print(f"NPN:               {npn}")
            print(f"Resident State(s): {resident_state}")
            print(f"DOB:               {dob}")
            print("=======================================")

            normalized_dob = normalize_dob(dob)

        finally:
            browser.close()
            print("Browser closed")

    if update_zoho:
        print("Updating Zoho CRM with scraped values...")
        try:
            zoho_update = update_contact_from_scrape(
                npn=npn,
                resident_state=resident_state,
                dob=normalized_dob,
            )
            print("Zoho update result:", zoho_update)
        except Exception as e:
            print(f"Zoho update failed: {e}")


# Optional: CLI test
if __name__ == "__main__":
    npn = sys.argv[1]
    update_zoho = sys.argv[2].lower() != "false" if len(sys.argv) > 2 else True
    run_scraper(npn, update_zoho=update_zoho)
