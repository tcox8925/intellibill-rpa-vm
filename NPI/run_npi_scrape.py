import os
import re
from contextlib import contextmanager
from deactivation_check import check_deactivation_status
from insert_npi_registry_row import run_npi_registry_scrape
from cms_lookup import fetch_cms_data
from scrape_tmb import scrape_tmb_profile, scrape_npiprofile_education  # supports return_pecos=True
from scrape_care_healthline import scrape_care_healthline
from utils.gpt_utils import get_pecos_id, consolidate_education, enrich_carriers, enrich_affiliations
from utils import upload_utils
from oig_check import run_oig_check
from caqh_lookup import run_caqh_lookup
from utils.source_tracking_utils import record_sources_used
from utils.db_utils import get_postgres_connection
from utils.upload_utils import set_db_connection_resolver
from utils.dea_checker import run_dea_analysis

set_db_connection_resolver(get_postgres_connection)

model_name = "data-validator"
PAC_RE = re.compile(r"^\d{10}$")


@contextmanager
def stage(name: str):
    """
    Wrap a pipeline step so any exception bubbles up with a clear stage tag.
    server.py will catch it and log_end(status=FAILED, error='[STAGE] ...')
    """
    try:
        yield
    except Exception as e:
        raise RuntimeError(f"[{name}] {type(e).__name__}: {e}") from e


def _is_valid_pac(x) -> bool:
    return bool(x and PAC_RE.fullmatch(str(x)))


def _add_pecos_identifier(rows: list, pac_id: str) -> list:
    """Append-only PECOS identifier; no duplicates; leaves txn binding to upload stage."""
    if not _is_valid_pac(pac_id):
        return rows or []
    rows = rows or []
    already = any(((r.get("id_type") or r.get("id_description") or "").strip().lower() == "pecos") for r in rows)
    if already:
        return rows
    rows.append({
        "id_type": "PECOS",
        "id_issuer": "PECOS",
        "id_description": "PECOS",
        "id_type_value": str(pac_id),
        "id_state": None,
        "source": "CMS/npiprofile",
    })
    return rows


def run_npi_scrape(
        txn_id: str,
        npi: str,
        module: str = "ALL",
        caqh_id: str = None,
        dry_run: bool = False
) -> str:
    sources = []

    # --- NPI Registry (for ALL/BOARD) ---
    reg = {}
    license_number = None
    if module in ("ALL", "BOARD"):
        with stage("NPI_REGISTRY"):
            reg = run_npi_registry_scrape(npi, txn_id) or {}
            sources.append("NPI_REGISTRY")
            license_number = next(
                (tax.get("license") for tax in reg.get("taxonomies", [])
                 if tax.get("state") == "TX" and tax.get("license")),
                None
            )
            if dry_run:
                print("[DRY RUN] Provider Info:", reg.get("provider_info"))
                print("[DRY RUN] Identifiers:", reg.get("identifiers"))
                print("[DRY RUN] Locations:", reg.get("locations"))
            else:
                upload_utils.upload_provider_info(txn_id, npi, reg.get("provider_info", {}), source="NPI_REGISTRY")
        with stage("NPI_DEACTIVATION_CHECK"):
            result = check_deactivation_status(npi, txn_id, dry_run=dry_run)
            if dry_run:
                print("[DRY RUN] NPI Deactivation Result:", result)

    if module == "ALL":
        # --- CMS ---
        with stage("CMS_LOOKUP"):
            cms = fetch_cms_data(npi) or {}
            sources.append("CMS")
            cms_pac_id = cms.get("pac_id")
            cms_cert_ids = cms.get("cert_ids", [])

            if dry_run:
                print(f"[DRY RUN] CMS PAC ID: {cms_pac_id}")
                print(f"[DRY RUN] CMS CCNs: {cms_cert_ids}")

        # --- TMB (if TX license) ---
        tmb = {}
        if license_number:
            with stage("TMB_SCRAPE"):
                tmb = scrape_tmb_profile(license_number) or {}
                sources.append("TMB")

                if tmb.get("tmb_name"):
                    is_match, sim, db_name = upload_utils.validate_tmb_license_owner(txn_id, npi, tmb["tmb_name"])
                    if not is_match:
                        print(f"[WARN] License mismatch: {tmb['tmb_name']} vs {db_name} ({sim:.1f}%) — skipping TMB upload.")
                        upload_utils.update_license_status(txn_id, license_number, "Inactive")
                        return txn_id  # stop here → skip TMB module
                    else:
                        print(f"[INFO] TMB license verified — name similarity {sim:.1f}%.")

                # --- Update license status (Active/Inactive)
                upload_utils.update_license_status(txn_id, license_number, tmb.get("license_status"))

                tmb_info = {
                    "rx_waiver_expiration_date": tmb.get("rx_waiver_expiration_date"),
                    "board_cert": tmb.get("board_cert"),
                    "board_cert_detail": tmb.get("board_cert_detail"),
                    "race": tmb.get("race"),
                    "awards": tmb.get("awards"),
                }
                if dry_run:
                    print("[DRY RUN] TMB Identifiers:", tmb.get("identifiers", []))
                    print("[DRY RUN] TMB Info:", tmb_info)
                    print("[DRY RUN] TMB Regulatory:", tmb.get("regulatory", []))
                else:
                    upload_utils.upload_provider_info(txn_id, npi, tmb_info, source="TMB")
                    # upload_utils.upload_regulatory_validation(txn_id, tmb.get("regulatory", []))
                    reg_ids = upload_utils.upload_regulatory_validation(txn_id, tmb.get("regulatory", []))

                    # Insert detailed regulatory actions, if any
                    details = tmb.get("regulatory_details", []) or []
                    if details:
                        rows = []
                        for d in details:
                            ct = (d.get("check_type") or "").upper()
                            parent = reg_ids.get(ct)
                            if not parent:
                                continue
                            rows.append({
                                "txn_id_reg": parent,
                                "check_type": d.get("check_type"),
                                "description": d.get("description"),
                                "action_date": d.get("action_date"),
                                "source": d.get("source") or "Texas Medical Board"
                            })
                        if rows:
                            upload_utils.upload_regulatory_fail_details(txn_id, rows)

        # --- Care.Healthline ---
        with stage("CARE_HEALTHLINE_SCRAPE"):
            ch = scrape_care_healthline(npi) or {}
            sources.append("CARE_HEALTHLINE")

            care_locs_raw = ch.get("locations", []) or []
            care_locs = upload_utils.normalize_care_locations(care_locs_raw, txn_id)

            if dry_run:
                print("[DRY RUN] Care Locations (raw):", care_locs_raw)
                print("[DRY RUN] Care Locations (normalized):", care_locs)
            else:
                if reg.get("locations"):
                    upload_utils.upload_locations(txn_id, reg["locations"])
                if care_locs:
                    upload_utils.upload_locations(txn_id, care_locs)

            langs = ch.get("languages_spoken", [])
            if isinstance(langs, list):
                langs = "; ".join([s for s in (l.strip() for l in langs) if s])
            else:
                langs = (langs or "").strip()
            if langs:
                if dry_run:
                    print("[DRY RUN] Care Languages:", langs)
                else:
                    upload_utils.upload_provider_info(txn_id, npi, {"language": langs}, source="Care_Healthline")

        # --- npiprofile.com (edu + PECOS fallback) ---
        with stage("NPI_PROFILE_SCRAPE"):
            npip = scrape_npiprofile_education(npi, return_pecos=True) or {"education": [], "pecos": {}}
            sources.append("NPI_PROFILE")
            np_edus = npip.get("education") or []
            npip_pac = (npip.get("pecos") or {}).get("pecos_pac_id")

            if dry_run:
                print("[DRY RUN] NPIProfile Education:", np_edus)
                print("[DRY RUN] NPIProfile PECOS:", npip.get("pecos"))

        # --- Education consolidation ---
        with stage("EDUCATION_CONSOLIDATE"):
            care_edus = ch.get("education_enrichment", []) or ch.get("education", [])
            final_edus = consolidate_education(
                npi=npi,
                tmb_entries=tmb.get("education", []),
                npiprofile_entries=np_edus,
                care_entries=care_edus,
                use_gpt_normalization=True,
                deployment=model_name
            )
            if dry_run:
                print("[DRY RUN] Final Education List:", final_edus)
            else:
                print("  - Final consolidated education:", len(final_edus))
                upload_utils.upload_education(txn_id, final_edus,
                                              source="GPT_Validation, TMB, NPIProfile, Care_Healthline")

        # --- Identifiers build + upload ---
        with stage("IDENTIFIERS_BUILD_UPLOAD"):
            all_ids = []
            all_ids.append({
                "id_type": "NPI", "id_description": "NPI", "id_type_value": npi,
                "id_state": None, "id_issuer": "NPI", "source": "NPI_REGISTRY",
            })
            for tax in reg.get("taxonomies", []) or []:
                all_ids.append({
                    "id_type": "Taxonomy ID", "id_description": "Taxonomy ID",
                    "id_type_value": tax.get("code"),
                    "id_state": tax.get("state"),
                    "id_issuer": "Taxonomy",
                    "source": "NPI_REGISTRY",
                })
            all_ids.extend(reg.get("identifiers", []) or [])
            all_ids.extend(tmb.get("identifiers", []) or [])

            pac_id = cms_pac_id if _is_valid_pac(cms_pac_id) else None
            if not pac_id and _is_valid_pac(npip_pac):
                pac_id = npip_pac
                sources.append("NPI_PROFILE_PECOS")
            if not pac_id:
                guess = get_pecos_id(npi, cms_pac_id)
                if _is_valid_pac(guess):
                    pac_id = guess
                    sources.append("GPT_PECOS")
            if pac_id:
                all_ids = _add_pecos_identifier(all_ids, pac_id)

            final_ids = upload_utils.normalize_identifiers(all_ids)
            if dry_run:
                print("[DRY RUN] Final Identifiers (normalized, deduped):", final_ids)
            else:
                upload_utils.upload_identifiers(txn_id, final_ids)

        # --- OIG check ---
        try:
            with stage("OIG_CHECK"):
                sources.append("OIG")
                oig_result = run_oig_check(npi, txn_id, dry_run=dry_run)
                if not oig_result:
                    print(f"[INFO] No OIG records found for NPI {npi}")
                else:
                    print(f"[INFO] OIG uploaded {len(oig_result)} records for NPI {npi}")
        except Exception as e:
            print(f"[WARN] OIG check skipped due to error: {e}")

        with stage("DEA_ANALYSIS"):
            sources.append("DEA")
            try:
                dea_summary = run_dea_analysis(get_postgres_connection, txn_id, logger=print)
                print(f"[INFO] DEA result: {dea_summary}")
            except Exception as e:
                print(f"[WARN] DEA analysis skipped due to error: {e}")


        # --- Carriers enrichment ---
        with stage("CARRIERS_ENRICH_UPLOAD"):
            carrier_names = [c.get("carrier_name") for c in ch.get("carriers", []) if c.get("carrier_name")]
            enriched_cars = enrich_carriers(carrier_names)
            for r in enriched_cars:
                if r.get("carrier_niac_number") and not r.get("carrier_naic_number"):
                    r["carrier_naic_number"] = r["carrier_niac_number"]

            print(
                f"[UPLOAD] carriers: scraped={len(carrier_names)} enriched={len(enriched_cars)} with_naic={sum(1 for r in enriched_cars if r.get('carrier_naic_number'))}")
            upload_utils.upload_carriers(txn_id, enriched_cars, source="GPT_Validation, Care_Healthline")

        # --- Affiliations ---
        with stage("AFFILIATIONS_ENRICH_UPLOAD"):
            care_aff_names = [a.get("affiliate_name") for a in ch.get("affiliations", []) if a.get("affiliate_name")]
            final_affs = enrich_affiliations(
                cms_cert_nums=cms_cert_ids,
                care_names=None if cms_cert_ids else care_aff_names
            )
            sources.append("GPT_AFFILIATIONS")
            if dry_run:
                print("[DRY RUN] Final Affiliations:", final_affs)
            else:
                upload_utils.upload_affiliations(txn_id, final_affs, source="GPT_Validation, CMS, Care_Healthline")

    elif module == "BOARD":
        if license_number:
            with stage("TMB_SCRAPE"):
                tmb = scrape_tmb_profile(license_number) or {}
                sources.append("TMB")

                if tmb.get("tmb_name"):
                    is_match, sim, db_name = upload_utils.validate_tmb_license_owner(txn_id, npi, tmb["tmb_name"])
                    if not is_match:
                        print(f"[WARN] License mismatch: {tmb['tmb_name']} vs {db_name} ({sim:.1f}%) — skipping TMB upload.")
                        upload_utils.update_license_status(txn_id, license_number, "Inactive")
                        return txn_id  # stop here → skip TMB module
                    else:
                        print(f"[INFO] TMB license verified — name similarity {sim:.1f}%.")

                # --- Update license status (Active/Inactive)
                upload_utils.update_license_status(txn_id, license_number, tmb.get("license_status"))
                tmb_info = {
                    "rx_waiver_expiration_date": tmb.get("rx_waiver_expiration_date"),
                    "board_cert": tmb.get("board_cert"),
                    "board_cert_detail": tmb.get("board_cert_detail"),
                    "race": tmb.get("race"),
                    "awards": tmb.get("awards"),
                }
                if dry_run:
                    print("[DRY RUN] TMB Identifiers:", tmb.get("identifiers", []))
                    print("[DRY RUN] TMB Info:", tmb_info)
                    print("[DRY RUN] TMB Regulatory:", tmb.get("regulatory", []))
                else:
                    tmb_ids = upload_utils.normalize_identifiers(tmb.get("identifiers", []) or [])
                    upload_utils.upload_identifiers(txn_id, tmb_ids)
                    upload_utils.upload_provider_info(txn_id, npi, tmb_info, source="TMB")
                    # upload_utils.upload_regulatory_validation(txn_id, tmb.get("regulatory", []))
                    reg_ids = upload_utils.upload_regulatory_validation(txn_id, tmb.get("regulatory", []))

                    # Insert detailed regulatory actions, if any
                    details = tmb.get("regulatory_details", []) or []
                    if details:
                        rows = []
                        for d in details:
                            ct = (d.get("check_type") or "").upper()
                            parent = reg_ids.get(ct)
                            if not parent:
                                continue
                            rows.append({
                                "txn_id_reg": parent,
                                "check_type": d.get("check_type"),
                                "description": d.get("description"),
                                "action_date": d.get("action_date"),
                                "source": d.get("source") or "Texas Medical Board"
                            })
                        if rows:
                            upload_utils.upload_regulatory_fail_details(txn_id, rows)
            if not dry_run:
                # After TMB uploads & regulatory detail inserts
                with stage("DEA_ANALYSIS"):
                    sources.append("DEA")
                    try:
                        dea_summary = run_dea_analysis(get_postgres_connection, txn_id, logger=print)
                        print(f"[INFO] DEA result: {dea_summary}")
                    except Exception as e:
                        print(f"[WARN] DEA analysis skipped due to error: {e}")

    elif module == "OIG":
        with stage("OIG_CHECK"):
            sources.append("OIG")
            if dry_run:
                oig_result = run_oig_check(npi, txn_id, dry_run=True)
                print("[DRY RUN] OIG Result:", oig_result)
            else:
                run_oig_check(npi, txn_id, dry_run=False)

        # DEA after OIG-only
        if not dry_run:
            with stage("DEA_ANALYSIS"):
                sources.append("DEA")
                try:
                    dea_summary = run_dea_analysis(get_postgres_connection, txn_id, logger=print)
                    print(f"[INFO] DEA result: {dea_summary}")
                except Exception as e:
                    print(f"[WARN] DEA analysis skipped due to error: {e}")



    elif module == "CAQH":

        with stage("CAQH_LOOKUP"):

            sources.append("CAQH")

            results = run_caqh_lookup(

                npi=npi,

                caqh_id=caqh_id,

                txn_id_provider=txn_id,

                username=os.getenv("CAQH_USERNAME", ""),

                password=os.getenv("CAQH_PASSWORD", "")

            )
            
            if not results.get("success"):
                print(f"[WARN] Invalid or empty CAQH ID: {caqh_id}")
                return txn_id  # terminate early (no inserts)

            # --- Dry run mode ---
            if dry_run:
                print("[DRY RUN] CAQH Results Summary:")
                for tbl, rows in results.items():
                    if isinstance(rows, list):
                        print(f"  {tbl}: {len(rows)} rows")
                return txn_id  # do not upload in dry_run mode

            # --- Upload to SQL ---
            print("[INFO] Uploading CAQH tables...")
            upload_utils.upload_caqh_results(txn_id, results)

            # --- Handle invalid / empty CAQH IDs ---

            if not results.get("success"):
                print(f"[WARN] Invalid or empty CAQH ID: {caqh_id}")

                return txn_id  # terminate early (no inserts)

            # --- Dry run mode ---

            if dry_run:

                print("[DRY RUN] CAQH Results Summary:")

                for tbl, rows in results.items():

                    if isinstance(rows, list):
                        print(f"  {tbl}: {len(rows)} rows")

                return txn_id

            # --- Upload (with debug) ---

            print("[INFO] Uploading CAQH tables...")

            print(f"[DEBUG] CAQH result keys: {list(results.keys())}")

            for k, v in results.items():
                print(f"  {k}: {type(v).__name__}, len={len(v) if isinstance(v, list) else 'N/A'}")

            upload_utils.upload_caqh_results(txn_id, results)

    else:
        raise ValueError(f"Unknown module: {module}")

    # Sources tracking (last)
    with stage("SOURCES_TRACKING"):
        if dry_run:
            print("[DRY RUN] Sources used:", sources)
        else:
            upload_utils.record_sources_used(txn_id, sources)

    return txn_id
