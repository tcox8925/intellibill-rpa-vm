from playwright.sync_api import sync_playwright
from time import sleep
import os
import uuid

SBS_HOME_URL = "https://www.statebasedsystems.com/solar/index.html"


# ==========================================================
# BROWSER
# ==========================================================

def launch_browser(headless: bool = False):
    playwright = sync_playwright().start()
    browser = playwright.chromium.launch(
        headless=headless,
        args=["--start-maximized"]
    )

    context = browser.new_context(
        viewport=None,
        accept_downloads=True,
    )

    page = context.new_page()
    return playwright, browser, context, page


# ==========================================================
# MAIN STATE FLOW
# ==========================================================

def run_state(
    state_row: dict,
    from_date: str,
    to_date: str,
    payment_profile: dict,
    download_dir: str,
):
    """
    Runs one SBS state end-to-end.

    Returns dict:
    {
        rows_count,
        fee_amount,
        pin_number,
        transaction_number,
        downloaded_file_path
    }
    """

    playwright, browser, context, page = launch_browser(headless=False)

    downloaded_file_path = None
    pin_number = None
    transaction_number = None

    try:
        # ------------------------------------------------------
        # LANDING PAGE
        # ------------------------------------------------------
        page.goto(SBS_HOME_URL)
        page.wait_for_load_state("domcontentloaded")

        with context.expect_page() as new_page_info:
            page.click("a[href*='reportGeneratorSearch.jsf']")
        rg_page = new_page_info.value
        rg_page.wait_for_load_state("domcontentloaded")

        # ------------------------------------------------------
        # PAGE 1 — FILTERS
        # ------------------------------------------------------
        rg_page.select_option("#jurisdictionId", label=state_row["jurisdiction"])
        sleep(1)

        rg_page.select_option("#entityTypeId", label=state_row["entity_type"])

        license_types = state_row["license_type"]
        if isinstance(license_types, str):
            license_types = [license_types]
        rg_page.select_option("#licenseType", label=license_types)

        rg_page.wait_for_function(
            """
            () => {
                const loa = document.querySelector('#loaType');
                if (!loa) return false;
                const opts = Array.from(loa.options).map(o => o.text.trim());
                return opts.length > 1 && !opts.every(o => o === 'ALL');
            }
            """,
            timeout=5000
        )

        loas = state_row["line_of_authority"]
        if isinstance(loas, str):
            loas = [loas]

        available_loas = rg_page.locator("#loaType option").all_inner_texts()
        valid_loas = [l for l in loas if l in available_loas]
        if valid_loas:
            rg_page.select_option("#loaType", label=valid_loas)

        rg_page.fill("#activeDateFrom_input", from_date)
        rg_page.fill("#activeDateTo_input", to_date)

        sleep(10)
        rg_page.click("a.ui-commandlink:has-text('Next')")
        rg_page.wait_for_load_state("domcontentloaded")
        try:
            print('Checking for [No Results] error...')
            if rg_page.get_by_text('No results found for the entered search criteria.').is_visible():
                print('No results were found for this state.')
                return {
                    "rows_count": 0,
                    "fee_amount": 0,
                    "pin_number": 'NA',
                    "transaction_number": 'NA',
                    "downloaded_file_path": 'NA',
                }
        except Exception as e:
            print(f'Error searching for [No Results] error: {e}')
        print('[No Results] error was not present. Continuing process...')


        # ------------------------------------------------------
        # PAGE 2 — REVIEW
        # ------------------------------------------------------
        rows_count = int(rg_page.inner_text("#rowsReturned").strip())
        fee_amount = float(rg_page.inner_text("#total").replace("$", "").strip())

        rg_page.fill(
            "#reportName",
            f"SBS_{state_row['jurisdiction']}_{from_date}_{to_date}"
        )

        if not rg_page.is_checked("#termsFlagId"):
            rg_page.check("#termsFlagId")

        if rg_page.locator("#stateTermsFlagId").count() > 0:
            if not rg_page.is_checked("#stateTermsFlagId"):
                rg_page.check("#stateTermsFlagId")

        sleep(10)
        rg_page.click("a.ui-commandlink:has-text('Next')")
        rg_page.wait_for_load_state("domcontentloaded")

        # ------------------------------------------------------
        # PAGE 3 — TRANSITION
        # ------------------------------------------------------
        rg_page.click("a.ui-commandlink:has-text('Next')")
        rg_page.wait_for_load_state("domcontentloaded")

        # ------------------------------------------------------
        # PAGE 4 — PAYMENT
        # ------------------------------------------------------
        if fee_amount > 0:
            rg_page.fill("input[id*='firstNameId']", payment_profile["first_name"])
            rg_page.fill("input[id*='lastNameId']", payment_profile["last_name"])
            rg_page.fill("input[id*='billingStreetId']", payment_profile["street"])
            rg_page.fill("input[id*='billingCityId']", payment_profile["city"])

            rg_page.select_option(
                "select[id*='billingStateId']",
                label=payment_profile["state"]
            )

            rg_page.click("input[id*='billingZipCodeId']")
            sleep(0.5)
            rg_page.keyboard.type(payment_profile["zip"], delay=100)

            rg_page.fill("input[id*='emailId']", payment_profile["email"])

            phone_input = rg_page.locator("input[id*='phoneNumberId']")
            phone_input.click()
            sleep(0.5)
            phone_input.type(payment_profile["phone"], delay=100)

            # Finish
            finish_btn = rg_page.locator("a.ui-commandlink:has-text('Finish')")
            finish_btn.wait_for(state="visible")
            finish_btn.click()

            # Stripe
            stripe_frame = rg_page.frame_locator(
                "iframe[name^='__privateStripeFrame']"
            ).first

            stripe_frame.locator("div.CardField").click()
            sleep(0.5)
            rg_page.keyboard.type(payment_profile["card_combined"], delay=40)

            rg_page.locator("div.ui-chkbox-box").click()
            rg_page.click("a:has-text('Submit Payment')")

            rg_page.wait_for_selector("span[id*='pinNumber']", timeout=30000)

            pin_number = rg_page.inner_text("span[id*='pinNumber']").strip()
            transaction_number = rg_page.inner_text(
                "span[id*='transactionNumber']"
            ).strip()

            # --------------------------------------------------
            # DOWNLOAD (BLOCKING + PERSISTENT)
            # --------------------------------------------------
            os.makedirs(download_dir, exist_ok=True)

            filename = (
                f"SBS_{state_row['jurisdiction']}_"
                f"{from_date.replace('/', '')}_"
                f"{to_date.replace('/', '')}_"
                f"{uuid.uuid4().hex}.csv"
            )

            final_path = os.path.join(download_dir, filename)

            with rg_page.expect_download() as download_info:
                rg_page.click("input[id*='reportDownload']")

            download = download_info.value
            download.save_as(final_path)

            downloaded_file_path = final_path

        return {
            "rows_count": rows_count,
            "fee_amount": fee_amount,
            "pin_number": pin_number,
            "transaction_number": transaction_number,
            "downloaded_file_path": downloaded_file_path,
        }

    finally:
        browser.close()
        playwright.stop()