# ==========================================================
# runner_sbe.py  (FINAL — ONE LOG ROW PER STATE)
# ==========================================================

import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, Dict, List
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

from handlers.crm_pivot_orchestrator import run_crm_pivot_export
from utils import db_utils
from utils.logger_utils import log_start, log_end
from handlers.enroll_handler import run_enroll_group_handler_buffered
from utils.nipr_pull import bulk_enrich_state
from utils.upload_utils import buffer_nipr_results_to_csv, load_nipr_csv_into_sbe_certs, load_sbe_csv_into_table
from utils.zoho_crm_checkbox_updater import run_zoho_crm_checkbox_update

MAX_STATE_WORKERS = 4
SBE_MATRIX_TABLE = "wpo.ops_sbe_process_matrix"
RUN_ID = str(uuid.uuid4())


# ==========================================================
# DRIVER FACTORY
# ==========================================================
def create_driver(headless: bool = False):
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")

    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")

    return webdriver.Chrome(options=chrome_options)


# ==========================================================
# MATRIX LOADING
# ==========================================================
def load_state_configs(nav_group_filter: Optional[str] = None) -> List[Dict]:
    conn = db_utils.get_postgres_connection()
    cur = conn.cursor()

    sql = f"""
        SELECT state_code, url, company_id, login_required,
               nipr_pull, nav_mode, nav_group, spl_acc, crm_field
        FROM {SBE_MATRIX_TABLE}
    """

    if nav_group_filter:
        sql += " WHERE nav_group = %s"
        cur.execute(sql, (nav_group_filter,))
    else:
        cur.execute(sql)

    cols = [c[0].lower() for c in cur.description]
    rows = cur.fetchall()
    conn.close()

    return [{cols[i]: row[i] for i in range(len(cols))} for row in rows]


# ==========================================================
# SCRAPE WORKER
# ==========================================================
def run_state_scrape(state_cfg: dict, max_pages: Optional[int] = None) -> bool:
    """
    Returns:
        True  → scraped CSV had NEW rows inserted into DB
        False → CSV missing, empty, or all duplicates
    """
    state_code = state_cfg["state_code"]
    driver = None

    try:
        driver = create_driver(headless=False)

        if (state_cfg.get("nav_group") or "").lower() != "enroll_group":
            raise RuntimeError(f"Unsupported nav_group for {state_code}")

        # SCRAPER
        run_enroll_group_handler_buffered(state_cfg, driver, max_pages=max_pages)

        # CSV → DB loader
        #inserted_any = load_nipr_csv_into_sbe_certs(state_cfg)
        inserted_any = load_sbe_csv_into_table(state_cfg)
        return inserted_any

    except Exception as e:
        raise RuntimeError(f"SCRAPE exception for {state_code}: {e}")

    finally:
        if driver:
            try: driver.quit()
            except: pass


# ==========================================================
# NIPR WORKER
# ==========================================================
def run_state_nipr_enrichment(state_cfg: Dict, max_rows: Optional[int] = None):
    state = state_cfg["state_code"]
    nipr_field = (state_cfg.get("nipr_pull") or "").strip().lower()

    if not nipr_field:
        print(f"[NIPR] Skipping {state} (nipr_pull empty)")
        return None

    result = bulk_enrich_state(state_cfg, max_rows=max_rows)

    # result = None → SUCCESS
    # result = string → ERROR
    return result


# ==========================================================
# ORCHESTRATOR — ONE LOG ROW PER STATE
# ==========================================================
def run_all_states(max_pages: Optional[int] = 20):
    print(f"\n[RUNNER] Starting SBE job, RUN_ID={RUN_ID}")

    state_cfgs = load_state_configs(nav_group_filter="enroll_group")
    if not state_cfgs:
        print("[RUNNER] No states found.")
        return

    # Track errors & scrape results
    scrape_errors = {cfg["state_code"]: None for cfg in state_cfgs}
    nipr_errors   = {cfg["state_code"]: None for cfg in state_cfgs}
    scrape_insert_flags = {cfg["state_code"]: False for cfg in state_cfgs}

    # Log contexts (start entries, not inserted yet)
    log_contexts = {
        cfg["state_code"]: log_start(
            script_name=f"SBE_RPA_{cfg['state_code']}",
            run_id=RUN_ID,
            company_id=cfg.get("company_id"),
            sub_entity_id='270681372001'
        )
        for cfg in state_cfgs
    }

    # ======================================================
    # SCRAPE PHASE
    # ======================================================
    print("\n[RUNNER] === SCRAPE PHASE ===")

    with ThreadPoolExecutor(max_workers=MAX_STATE_WORKERS) as executor:
        futures = {
            executor.submit(run_state_scrape, cfg, max_pages): cfg
            for cfg in state_cfgs
        }

        for future in as_completed(futures):
            cfg = futures[future]
            state = cfg["state_code"]

            try:
                result = future.result()   # Boolean: True/False
                scrape_insert_flags[state] = bool(result)

            except Exception as e:
                scrape_errors[state] = str(e)
                print(f"[RUNNER] SCRAPE failed for {state}: {e}")

    # ======================================================
    # NIPR PHASE
    # ======================================================
    print("\n[RUNNER] === NIPR ENRICHMENT PHASE ===")

    for cfg in state_cfgs:
        state = cfg["state_code"]

        # Skip if scrape failed OR this state inserted no new rows
        if scrape_errors[state] or not scrape_insert_flags[state]:
            print(f"[NIPR] Skipping {state} (no new scrape rows or scrape failed)")
            continue

        print(f"\n[NIPR] === Processing {state} ===")

        # --------------------------------------------------
        # 1) Bulk scrape → write results to nipr_temp CSV
        # --------------------------------------------------
        try:
            nipr_results = run_state_nipr_enrichment(cfg)
            scraped = buffer_nipr_results_to_csv(cfg, nipr_results)
        except Exception as e:
            nipr_errors[state] = f"NIPR scrape error: {e}"
            print(f"[NIPR_ERR] {state} bulk scrape failed: {e}")
            continue

        if not scraped:
            print(f"[NIPR] No NIPR results produced for {state}")
            continue

        # --------------------------------------------------
        # 2) Load CSV in bulk → update raw.sbe_certs
        # --------------------------------------------------
        try:
            loaded = load_nipr_csv_into_sbe_certs(cfg)
        except Exception as e:
            nipr_errors[state] = f"NIPR load error: {e}"
            print(f"[NIPR_ERR] {state} load failed: {e}")
            continue

        if loaded:
            print(f"[NIPR] Completed bulk NIPR for {state}")
        else:
            print(f"[NIPR] No updates written for {state}")

    # ======================================================
    # CRM PIVOT PHASE
    # ======================================================
    print("\n[RUNNER] === CRM PIVOT PHASE ===")
    try:
        print('==[Skipping CRM File Pivot Export Phase]')
        #run_crm_pivot_export()
    except Exception as e:
        print(e)

    # ======================================================
    # CRM UPDATE PHASE
    # ======================================================
    print("\n[RUNNER] === CRM UPDATE PHASE ===")
    try:
        print('==[Skipping CRM Update Phase]')
        #run_zoho_crm_checkbox_update()
    except Exception as e:
        print(e)

    # ======================================================
    # FINAL LOGGING
    # ======================================================
    print("\n[RUNNER] === FINAL LOGGING PHASE ===")

    for cfg in state_cfgs:
        state = cfg["state_code"]
        ctx = log_contexts[state]

        if scrape_errors[state]:
            log_end(ctx, phase="SCRAPE", error_message=scrape_errors[state])
        elif nipr_errors[state]:
            log_end(ctx, phase="NIPR", error_message=nipr_errors[state])
        else:
            log_end(ctx)

    print("\n[RUNNER] ALL PHASES COMPLETED.\n")


if __name__ == "__main__":
    run_all_states(max_pages=None)
