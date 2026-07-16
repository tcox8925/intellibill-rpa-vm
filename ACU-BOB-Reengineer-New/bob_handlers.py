# ==========================================================
#  bob_handlers.py
# ==========================================================
"""
BOB carrier-specific handlers.

Most BOB logic is now matrix-driven via rules:
  - status_value_map      → mem_status remapping (e.g. HCSC Paid Activated:Active)
  - type_value_map        → product_type → mem_market (e.g. BCBS MI)
  - contract_count        → C/N/Y/ID_SUFFIX for mem_count
  - filter_rule_type      → ALL/STATUS/DATE filtering

Handlers are only needed for logic too complex for rules:
  - Oscar: dynamic writing_num fill with NPN template
  - BCBS MI: product_type → child carrier_id split
  - HCSC: status remap handled by status_value_map now
  - Anthem: date skip handled by skip_date_parse rule

Legacy ACU handlers that moved to rules:
  - HCSC Paid Activated → Active                → status_value_map
  - BCBS MI product_type → mem_market           → type_value_map
  - Imperial mem_count from ID suffix           → contract_count = ID_SUFFIX
"""

import pandas as pd
import numpy as np
from datetime import datetime


def handle_bob_default(df, rule):
    """Standard BOB carriers. Matrix flags handle everything."""
    return df


def handle_oscar_bob(df, rule):
    """
    Oscar BOB:
      - Agents without writing_num get "Pending Sale - {NPN}"
    """
    if "agent_writing_num" in df.columns and "agent_npn" in df.columns:
        mask = (
            df["agent_writing_num"].isna() |
            df["agent_writing_num"].astype(str).str.strip().isin(["", "Pending Sale", "nan", "None"])
        )
        df.loc[mask, "agent_writing_num"] = df.loc[mask, "agent_npn"].apply(
            lambda npn: f"Pending Sale - {npn}" if pd.notna(npn) and str(npn).strip() else ""
        )
        print(f"    🔧 Oscar BOB: filled {mask.sum()} writing_num with 'Pending Sale - NPN'")
    return df


def handle_bcbsmi_bob(df, rule):
    """
    BCBS MI BOB:
      - Single file contains ACA + MDC + SUP data in 'product_type' / 'Line of Business'
      - market_value_map (type_value_map in rules) handles: Medical→ACA, Medicare Advantage→MDC, etc.
      - carrier_id needs to be set per market (ACA vs MDC child carriers)
      - This handler assigns the correct carrier_id based on the derived mem_market.

    Note: The type_value_map already converts product_type → mem_market before this handler runs.
    """
    # mem_market is set from Line of Business by the matrix type_value_map
    # (Medical→ACA, Medicare Advantage→MDC, Dental/Vision→SUP). This carrier owns
    # only its own market (rule.contract_type); the file's other-market rows belong
    # to the sibling BCBS MI carriers (MDC/SUP), so route them out. Rows whose Line
    # of Business didn't map to any market are surfaced (not silently dropped).
    own = str(rule.get("contract_type", "")).strip().upper()
    if own and "mem_market" in df.columns:
        mk = df["mem_market"].fillna("").astype(str).str.strip().str.upper()
        unmapped = mk == ""
        if unmapped.any():
            lob = (df.loc[unmapped, "product_type"].value_counts().to_dict()
                   if "product_type" in df.columns else {})
            print(f"    ⚠️  BCBS MI {own}: {int(unmapped.sum())} row(s) with unmapped Line of Business → exception: {lob}")
        before = len(df)
        df = df[mk == own].copy().reset_index(drop=True)
        print(f"    🔧 BCBS MI {own}: kept {len(df)}/{before} rows (other-market rows belong to sibling carriers)")
    return df


def handle_uhc_bob(df, rule):
    """
    UHC ACA BOB: a single combined file containing both ACA and MDC members.
      - mem_market is derived from 'product' (guide): IFP = ACA; MS / PDP / MA = MDC.
      - planStatus 'A' = Active (the guide notes all rows show active); Medicare
        Supplement rows carry status in a separate 'Plan Status(MS)' column, used
        as a fallback when planStatus is blank.
    """
    # mem_market (IFP→ACA, MS/PDP/MA→MDC) is set by the matrix type_value_map.
    # Genuine multi-column logic stays here: Medicare Supplement rows carry their
    # status in a separate 'Plan Status(MS)' column, used as a fallback when the
    # primary planStatus is blank.
    if "mem_status" in df.columns:
        ms_col = next((c for c in df.columns
                       if c.lower().strip().replace(" ", "") in ("planstatus(ms)",)), None)
        st = df["mem_status"].fillna("").astype(str).str.strip()
        if ms_col is not None:
            st = st.where(st != "", df[ms_col].fillna("").astype(str).str.strip())
        df["mem_status"] = np.where(st.str.upper().eq("A"), "Active", st)
    return df


def handle_medica_bob(df, rule):
    """
    Medica BOB:
      - The export is multi-carrier: column 'Carrier' holds MEDICA plus ~12 other
        carriers (BCBSNE, Bright Health, GEOBLUE, Ambetter, ...). Keep MEDICA only.
      - 'Carrier' is mapped to 'source_carrier' in the load matrix; this handler
        drops every non-MEDICA row. The standard mem_status filter still applies.
    """
    col = "source_carrier"
    if col in df.columns:
        before = len(df)
        carr = df[col].fillna("").astype(str).str.strip().str.upper()
        # Guide: valid carriers are MEDICA and BCBSNE; any other carrier in this
        # multi-carrier export is unexpected and must be surfaced (not silently dropped).
        unexpected = sorted(set(carr[~carr.isin(["MEDICA", "BCBSNE"])]) - {""})
        if unexpected:
            cnt = carr[~carr.isin(["MEDICA", "BCBSNE"])].value_counts().to_dict()
            print(f"    ⚠️  Medica BOB: UNEXPECTED carriers in file (exception report): {cnt}")
        keep = carr == "MEDICA"
        df = df[keep].copy().reset_index(drop=True)
        print(f"    🔧 Medica BOB: Carrier==MEDICA filter {before} → {len(df)} rows")
    else:
        print(f"    ⚠️  Medica BOB: '{col}' not found — no carrier filter applied")

    # mem_market (Medical→ACA, Medicare/Part D/etc→MDC, Dental/STM→SUP) is set by
    # the matrix type_value_map. The MEDICA-only carrier filter above is genuine
    # multi-carrier logic and stays here.
    return df


def handle_alignment_bob(df, rule):
    """Alignment Health BOB. Guide rule: 'Active if enrolled and after effective
    date.' The STATUS filter (filter_values='Enrolled') keeps enrolled rows and
    status_value_map (Enrolled:Active) labels them Active. This handler completes
    the rule by dropping members whose effective date is AFTER the report date
    (coverage not yet in force as of the snapshot), so only enrolled + already-
    effective members remain Active. report_date is the file's snapshot date."""
    # NOTE: an earlier version dropped members whose effective date is after the
    # report date ("not yet effective"). That silently removed future-effective
    # members (no exception, no trace) and contradicts the business direction to
    # keep future/pending actives. We now KEEP them; only log the count for
    # visibility. If business later wants them excluded, do it as a tracked
    # filter, not a silent handler drop.
    if "mem_effective_date" in df.columns and "report_date" in df.columns and not df.empty:
        ref = pd.to_datetime(df["report_date"].iloc[0], errors="coerce")
        eff = pd.to_datetime(df["mem_effective_date"], errors="coerce")
        if pd.notna(ref):
            n = int((eff.notna() & (eff > ref)).sum())
            if n:
                print(f"    ℹ️  Alignment BOB: {n} future-effective member(s) retained "
                      f"(effective date after report date {ref.date()})")

    # mem_plan_year: the Alignment file has no plan-year column (the guide's
    # "Client Name -> mem_plan_year" mapping is wrong), so derive the plan year
    # from the coverage effective date's year, falling back to the report-date
    # year when the effective date is missing.
    if not df.empty:
        eff_yr = pd.to_datetime(df.get("mem_effective_date"), errors="coerce").dt.year
        ref_yr = None
        if "report_date" in df.columns:
            ref_yr = pd.to_datetime(df["report_date"].iloc[0], errors="coerce")
            ref_yr = ref_yr.year if pd.notna(ref_yr) else None
        df["mem_plan_year"] = (
            eff_yr.fillna(ref_yr).astype("Int64").astype(str).replace("<NA>", "")
        )
    return df


def handle_imperial_bob(df, rule):
    """Imperial (ACA) BOB. Guide: mem_policy_num is the Subscriber ID, but
    'If Subscriber ID is empty, this is the policy_num' for the Plan column.
    Subscriber ID is mapped to mem_policy_num; 'Plan' remains as the raw column.
    Backfill mem_policy_num from Plan wherever Subscriber ID was blank."""
    # read_and_map_file lower-cases every column, so the unmapped raw 'Plan'
    # column arrives as 'plan'. Resolve it case-insensitively (matching
    # handle_ethos_bob) rather than assuming a fixed casing.
    _plan_col = next((c for c in df.columns if str(c).strip().lower() == "plan"), None)
    if "mem_policy_num" in df.columns and _plan_col:
        blank = df["mem_policy_num"].fillna("").astype(str).str.strip() == ""
        n = int(blank.sum())
        df.loc[blank, "mem_policy_num"] = df.loc[blank, _plan_col]
        print(f"    🔧 Imperial BOB: backfilled mem_policy_num from Plan for {n} blank Subscriber IDs")
    return df


def handle_ethos_bob(df, rule):
    """Ethos (life) BOB. Guide rule: a member is Active only when
    Stage == 'Policy' AND Policy Status == 'Premium Paying' (compound, two
    columns), so the single-column matrix STATUS filter can't express it.
    Stage is mapped to mem_status; 'Policy Status' is left as the raw column.
    This handler applies the compound keep and normalizes mem_status to Active.
    Filter rule is set to ALL so the matrix filter does not also drop rows."""
    stage = df["mem_status"].fillna("").astype(str).str.strip().str.lower() if "mem_status" in df.columns else ""
    # read_and_map_file lower-cases every column, so the unmapped raw status
    # column arrives as 'policy status'. Resolve it case-insensitively rather
    # than assuming a fixed casing.
    _ps_col = next((c for c in df.columns if str(c).strip().lower() == "policy status"), None)
    # Normalize both sides (case-fold + collapse internal whitespace) so the
    # compound keep is robust to casing/spacing variants in the raw file.
    pstat = (df[_ps_col].fillna("").astype(str).str.strip().str.lower()
             .str.replace(r"\s+", " ", regex=True)) if _ps_col else ""
    keep = (stage == "policy") & (pstat == "premium paying")
    before = len(df)
    df = df[keep].copy().reset_index(drop=True)
    if "mem_status" in df.columns:
        df["mem_status"] = "Active"
    print(f"    🔧 Ethos BOB: Stage='policy' AND Policy Status='Premium Paying' -> {before} -> {len(df)} active ({before - len(df)} dropped)")
    return df


def handle_worldtrips_bob(df, rule):
    """WorldTrips (supplemental travel). Guide rule: 'Map all as Travel
    Insurance'. The raw file has no product column, so product_type is a
    constant for every row.

    The WorldTrips extract also repeats each membership (observed 2×–8× per
    member in the raw file: identical policy, coverage dates and agent, no
    distinguishing field), which would double-count members downstream. Collapse
    on the membership key so each member is written once. This runs before the
    downstream steps assign a per-row txn_id, so the dedup is effective.
    """
    if not df.empty:
        df["product_type"] = "Travel Insurance"
        key = [c for c in ["mem_policy_num", "mem_full_name", "mem_id",
                           "mem_eff_date", "mem_cov_start_date", "mem_cov_end_date",
                           "agent_writing_num"] if c in df.columns]
        if key:
            before = len(df)
            df = df.drop_duplicates(subset=key, keep="first").reset_index(drop=True)
            if len(df) != before:
                print(f"    🔁 WorldTrips: collapsed {before - len(df)} duplicate "
                      f"membership row(s) on {key}")
    return df


# ==========================================================
#  HANDLER MAP
# ==========================================================

bob_handler_map = {
    "handle_oscar_bob": handle_oscar_bob,
    "handle_bcbsmi_bob": handle_bcbsmi_bob,
    "handle_uhc_bob": handle_uhc_bob,
    "handle_medica_bob": handle_medica_bob,
    "handle_alignment_bob": handle_alignment_bob,
    "handle_ethos_bob": handle_ethos_bob,
    "handle_imperial_bob": handle_imperial_bob,
    "handle_worldtrips_bob": handle_worldtrips_bob,
    "__default__": handle_bob_default,
}


def get_bob_handler(rule):
    """Get handler function for a BOB carrier. Falls back to handle_bob_default."""
    if rule.get("custom_logic_flag") != "Y":
        return handle_bob_default
    module_name = str(rule.get("custom_module_name", "")).strip()
    handler = bob_handler_map.get(module_name, handle_bob_default)
    if handler == handle_bob_default and module_name:
        print(f"    ⚠️  No BOB handler for '{module_name}' - using default")
    return handler
