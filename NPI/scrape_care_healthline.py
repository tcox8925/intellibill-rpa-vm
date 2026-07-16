import time
from typing import Dict, Any
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup


def scroll_page(driver, pause: float = 1.0):
    last_height = driver.execute_script("return document.body.scrollHeight")
    while True:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(pause)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height


def _is_provider_page(url: str) -> bool:
    # Healthline redirects to /find-care/provider/<slug>-<NPI>
    return "/find-care/provider/" in (url or "")


def _safe_quit(driver: webdriver.Chrome, service: Service, timeout: float = 5.0):
    # Be defensive: quit first, then stop the service if quit hangs
    try:
        driver.quit()
        return
    except Exception:
        pass
    # Hard stop
    try:
        service.stop()
    except Exception:
        pass


def scrape_care_healthline(npi: str) -> Dict[str, Any]:
    url = f"https://care.healthline.com/find-care/provider/{npi}"

    options = Options()
    # options.add_argument("--headless=new")  # enable once working reliably
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    )
    # Don’t wait on every network request (e.g., map tiles)
    options.page_load_strategy = "eager"

    service = Service()  # let Selenium find chromedriver on PATH
    driver = webdriver.Chrome(service=service, options=options)

    # Reasonable timeouts
    driver.set_page_load_timeout(20)
    driver.set_script_timeout(20)

    # Hide webdriver flag
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"}
        )
    except Exception:
        pass

    try:
        driver.get(url)
        WebDriverWait(driver, 15).until(
            lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
        )

        # Accept slugged provider URLs (don’t require /{npi})
        if not _is_provider_page(driver.current_url):
            # one retry via JS (helps after cookies/scripts settle)
            driver.execute_script("window.location.href = arguments[0];", url)
            WebDriverWait(driver, 15).until(
                lambda d: d.execute_script("return document.readyState") in ("interactive", "complete")
            )
            if not _is_provider_page(driver.current_url):
                print(f"[CARE] Still not on provider page → {driver.current_url}")
                return {"affiliations": [], "carriers": [], "locations": []}

        # Wait for any of the big modules; specifically include carriers
        WebDriverWait(driver, 20).until(EC.any_of(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.ProviderInsuranceAccepted")),
            EC.presence_of_element_located((By.CSS_SELECTOR, "div.ProviderHospitalAffiliations")),
            EC.presence_of_element_located((By.CSS_SELECTOR, "ps-providerlocationsmodule"))
        ))

        scroll_page(driver, pause=0.8)
        time.sleep(0.5)

        # ---- Carriers (li > span per your HTML) ----
        carriers = []
        try:
            li_nodes = driver.find_elements(
                By.CSS_SELECTOR,
                "div.ProviderInsuranceAccepted ul.ProviderInsuranceAccepted-description li span"
            )
            for el in li_nodes:
                name = (el.text or "").strip()
                if name:
                    carriers.append({
                        "carrier_name": name,
                        "source": "Care.Healthline",
                        "plans": [],
                        "naic_info": None
                    })
        except Exception:
            pass
        print(f"[CARE] carriers found: {len(carriers)}")

        # ---- Affiliations (li > span) ----
        affiliations = []
        try:
            aff_nodes = driver.find_elements(
                By.CSS_SELECTOR,
                "div.ProviderHospitalAffiliations ul.ProviderHospitalAffiliations-description li span"
            )
            for el in aff_nodes:
                nm = (el.text or "").strip()
                if nm:
                    affiliations.append({"affiliate_name": nm})
        except Exception:
            pass
        print(f"[CARE] affiliations found: {len(affiliations)}")

        # ---- Locations (HTML you pasted is visible to BS4) ----
        soup = BeautifulSoup(driver.page_source, "html.parser")
        locations = []
        try:
            location_module = soup.find("ps-providerlocationsmodule")
            if location_module:
                location_ol = location_module.find("ol")
                if location_ol:
                    for li in location_ol.find_all("li", class_="ProviderLocationsModule-location"):
                        address_tag = li.find("address")
                        if not address_tag:
                            continue
                        loc_name_div = address_tag.find("div", class_="ProviderLocationsModuleAlternative-office-title")
                        location_name = loc_name_div.get_text(strip=True) if loc_name_div else None
                        address_span = address_tag.find("span")
                        full_address = address_span.get_text(strip=True) if address_span else None
                        locations.append({
                            "source": "care_healthline",
                            "location_name": location_name,
                            "type": "Practice",
                            "address": full_address
                        })
        except Exception:
            pass
        print(f"[CARE] locations found: {len(locations)}")

        languages_spoken = []
        try:
            # Selenium path (web components-friendly)
            stat_titles = driver.find_elements(By.CSS_SELECTOR,
                                               "#about-me.ProviderAboutStats .ProviderAboutStatsItem-title")
            for title_el in stat_titles:
                title = (title_el.text or "").strip().lower()
                if "languages spoken" in title:
                    ul = title_el.find_element(By.XPATH, "../ul[contains(@class,'ProviderAboutStatsItem-text')]")
                    for li in ul.find_elements(By.TAG_NAME, "li"):
                        txt = (li.text or "").strip()
                        if txt:
                            languages_spoken.append(txt)  # <-- keep raw; no splitting
                    break
        except Exception:
            pass

        # BeautifulSoup fallback
        if not languages_spoken:
            try:
                about_me = soup.find("div", id="about-me", class_="ProviderAboutStats")
                if about_me:
                    for stat in about_me.select("ps-stat.ProviderAboutStatsItem"):
                        t = stat.select_one(".ProviderAboutStatsItem-title")
                        title = (t.get_text(strip=True).lower() if t else "")
                        if "languages spoken" in title:
                            for li in stat.select("ul.ProviderAboutStatsItem-text li"):
                                txt = li.get_text(strip=True)
                                if txt:
                                    languages_spoken.append(txt)  # <-- keep raw; no splitting
                            break
            except Exception:
                pass

        print(f"[CARE] languages_spoken: {languages_spoken}")

        return {
            "affiliations": affiliations,   # affiliate_name
            "carriers": carriers,           # carrier_name
            "locations": locations,
            "languages_spoken": languages_spoken
        }

    except Exception as e:
        print(f"Care.Healthline scrape failed for NPI {npi}: {e}")
        return {"affiliations": [], "carriers": [], "locations": []}
    finally:
        _safe_quit(driver, service)