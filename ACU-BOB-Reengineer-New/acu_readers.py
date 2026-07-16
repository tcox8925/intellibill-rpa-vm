# ==========================================================
#  acu_readers.py
# ==========================================================
"""
Custom file readers for carriers that can't use the standard
read_and_map_file pipeline. Called when rules matrix has
custom_reader_name set.

Both readers return list of (rule_dict, DataFrame) tuples.
The runner creates a task per sub-carrier and processes each independently.

    read_hcsc  — Multi-sheet (Govt/Retail), Producer/Sub NPN routing,
                 splits by state -> one sub-carrier per state
    read_sma   — Multi-carrier workbook, per-sheet carrier splitting

The '/' notation in load_matrix mapping (e.g., Producer NPN/Sub-Producer NPN)
means: use first column for Producers, second for Subproducers.
"""

import io
import os
import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from utils.azure_blob_utils import DEFAULT_CONTAINER


def _safe_excel_file(blob_data):
    """Open an Excel file with engine fallback (auto → openpyxl → xlrd)."""
    try:
        return pd.ExcelFile(io.BytesIO(blob_data))
    except Exception:
        try:
            return pd.ExcelFile(io.BytesIO(blob_data), engine="openpyxl")
        except Exception:
            return pd.ExcelFile(io.BytesIO(blob_data), engine="xlrd")


# ==========================================================
#  HCSC READER
# ==========================================================
HCSC_STATE_IDS = {
    "IL": "2931751000020024204",
    "MT": "2931751000119585861",
    "NM": "2931751000034092012",
    "OK": "2931751000035531281",
    "TX": "2931751000020024151",     # TX ACA default
}
HCSC_TX_MDC_ID = "2931751000481362918"


def _hcsc_read_sheet(xls, sheet_name, mappings_df, skip_rows):
    """
    Read one HCSC sheet, apply Producer/Sub NPN routing via '/' notation.
    Returns a DataFrame in canonical columns.
    """
    df = pd.read_excel(xls, sheet_name=sheet_name, skiprows=skip_rows, dtype=str)
    df.columns = df.columns.str.lower().str.strip()

    # Parse mappings - split '/' into producer/sub pairs
    mapping_pairs = {}   # {canonical: (prod_col, sub_col)}
    simple_mappings = {} # {canonical: file_col}

    for _, m in mappings_df.iterrows():
        raw = str(m.get("mapping", "")).strip()
        canonical = str(m.get("database_column", "")).strip().lower()
        if not raw or raw in ("nan", "NA", "None"):
            continue
        if "/" in raw:
            parts = [p.strip() for p in raw.split("/")]
            if len(parts) == 2:
                mapping_pairs[canonical] = (parts[0], parts[1])
        else:
            simple_mappings[canonical] = raw

    # Column mapped to appointment_type determines Producer vs Subproducer
    # Business rule: if Sub-Producer Name has a value → Subproducer, blank → Producer
    sub_indicator_col = simple_mappings.get("appointment_type", "").lower()
    if sub_indicator_col not in df.columns:
        print(f"    WARNING: Sub-Producer indicator column '{sub_indicator_col}' not found in sheet '{sheet_name}'")
        return pd.DataFrame()

    # Determine Producer vs Subproducer
    is_sub = df[sub_indicator_col].notna() & (df[sub_indicator_col].str.strip() != "")
    df["appointment_type"] = np.where(is_sub, "Subproducer", "Producer")

    # Route paired columns
    for canonical, (prod_col, sub_col) in mapping_pairs.items():
        pc, sc = prod_col.lower().strip(), sub_col.lower().strip()

        if canonical == "parent_npn":
            # Subs get the Producer's NPN as parent; Producers get blank
            df[canonical] = ""
            if pc in df.columns:
                df.loc[is_sub, canonical] = df.loc[is_sub, pc]
        elif canonical in ("agent_npn", "agent_writing_num", "agent_full_name",
                           "contract_status", "contract_date", "appointed_date"):
            # Producers use first col, Subs use second col
            df[canonical] = ""
            if pc in df.columns:
                df.loc[~is_sub, canonical] = df.loc[~is_sub, pc]
            if sc in df.columns:
                df.loc[is_sub, canonical] = df.loc[is_sub, sc]
        else:
            # RTS columns etc - route by Producer/Sub
            df[canonical] = ""
            if pc in df.columns:
                df.loc[~is_sub, canonical] = df.loc[~is_sub, pc]
            if sc in df.columns:
                df.loc[is_sub, canonical] = df.loc[is_sub, sc]

    # Simple mappings (no '/')
    for canonical, file_col in simple_mappings.items():
        if canonical == "appointment_type":
            continue
        fc = file_col.lower().strip()
        if fc in df.columns:
            df[canonical] = df[fc]

    # Zero-pad writing numbers to 9 digits
    if "agent_writing_num" in df.columns:
        na_mask = df["agent_writing_num"].isna() | (df["agent_writing_num"].str.strip() == "")
        df["agent_writing_num"] = (
            df["agent_writing_num"].fillna(0).astype(float).astype(int).astype(str).str.zfill(9)
        )
        df.loc[na_mask, "agent_writing_num"] = None

    # Drop all-blank rows (excluding state)
    data_cols = [c for c in df.columns if c != "appointed_state"]
    df = df.dropna(how="all", subset=data_cols)

    print(f"    {sheet_name}: {len(df)} rows ({(~is_sub).sum()} producers, {is_sub.sum()} subs)")
    return df


def _hcsc_sub_rule(master_rule, carrier_name, carrier_id):
    """Build a sub-carrier rule dict from the master HCSC rule."""
    sub = dict(master_rule) if isinstance(master_rule, dict) else master_rule.to_dict()
    sub["carrier_name"] = carrier_name
    sub["carrier_id"] = carrier_id
    sub["custom_reader_name"] = ""
    sub["custom_logic_flag"] = "N"
    sub["custom_module_name"] = ""
    return sub


def read_hcsc(blob_service_client, blob_name, rule, column_mappings,
              all_rules_df, all_mappings_df, container_name=DEFAULT_CONTAINER):
    """
    Read HCSC multi-sheet Excel file. Returns list of (rule_dict, df) tuples,
    one per state. Each state is an independent carrier with its own carrier_id.

    Govt sheet  -> MDC (Medicare)
    Retail sheet -> ACA
    States: IL, MT, NM, OK, TX (TX has separate ACA and MDC carrier_ids)
    """
    skip_rows = int(rule.get("ignore_header_rows", 0) or 0)

    container_client = blob_service_client.get_container_client(container_name)
    blob_data = container_client.get_blob_client(blob_name).download_blob().readall()
    xls = _safe_excel_file(blob_data)
    print(f"    HCSC sheets: {xls.sheet_names}")

    # Get per-sheet mappings from load matrix
    hcsc_mdc_map = all_mappings_df[
        (all_mappings_df["carrier_name"].str.contains("HCSC", case=False, na=False)) &
        (all_mappings_df["carrier_name"].str.contains("MDC", case=False, na=False)) &
        (all_mappings_df["process_type"] == "ACU")
    ]
    hcsc_aca_map = all_mappings_df[
        (all_mappings_df["carrier_name"].str.contains("HCSC", case=False, na=False)) &
        (all_mappings_df["carrier_name"].str.contains("ACA", case=False, na=False)) &
        (all_mappings_df["process_type"] == "ACU")
    ]

    all_frames = []

    # Read Govt sheet (MDC)
    govt_sheet = next((s for s in xls.sheet_names if "govt" in s.lower()), None)
    if govt_sheet and not hcsc_mdc_map.empty:
        print(f"    Reading '{govt_sheet}' (MDC)")
        df_mdc = _hcsc_read_sheet(xls, govt_sheet, hcsc_mdc_map, skip_rows)
        if not df_mdc.empty:
            df_mdc["_market"] = "MDC"
            all_frames.append(df_mdc)

    # Read Retail sheet (ACA)
    retail_sheet = next((s for s in xls.sheet_names if "retail" in s.lower()), None)
    if retail_sheet and not hcsc_aca_map.empty:
        print(f"    Reading '{retail_sheet}' (ACA)")
        df_aca = _hcsc_read_sheet(xls, retail_sheet, hcsc_aca_map, skip_rows)
        if not df_aca.empty:
            df_aca["_market"] = "ACA"
            all_frames.append(df_aca)

    if not all_frames:
        return []

    combined = pd.concat(all_frames, ignore_index=True)
    combined = combined.loc[:, ~combined.columns.duplicated()]
    print(f"    HCSC combined: {len(combined)} rows")

    # Split by state -> one sub-carrier per state
    if "appointed_state" not in combined.columns:
        return []

    combined["_state"] = combined["appointed_state"].fillna("").astype(str).str.strip().str.upper()
    results = []

    for state, state_cid in HCSC_STATE_IDS.items():
        state_data = combined[combined["_state"] == state].copy()
        if state_data.empty:
            continue

        if state == "TX":
            # TX has separate MDC and ACA carrier_ids
            tx_mdc = state_data[state_data["_market"] == "MDC"].copy()
            tx_aca = state_data[state_data["_market"] == "ACA"].copy()

            if not tx_mdc.empty:
                tx_mdc["carrier_id"] = HCSC_TX_MDC_ID
                tx_mdc.drop(columns=["_market", "_state"], errors="ignore", inplace=True)
                results.append((_hcsc_sub_rule(rule, "HCSC - TX MDC", HCSC_TX_MDC_ID), tx_mdc))
                print(f"      HCSC - TX MDC: {len(tx_mdc)} rows")

            if not tx_aca.empty:
                tx_aca["carrier_id"] = state_cid
                tx_aca.drop(columns=["_market", "_state"], errors="ignore", inplace=True)
                results.append((_hcsc_sub_rule(rule, "HCSC - TX ACA", state_cid), tx_aca))
                print(f"      HCSC - TX ACA: {len(tx_aca)} rows")
        else:
            state_data["carrier_id"] = state_cid
            state_data.drop(columns=["_market", "_state"], errors="ignore", inplace=True)
            results.append((_hcsc_sub_rule(rule, f"HCSC - {state}", state_cid), state_data))
            print(f"      HCSC - {state}: {len(state_data)} rows")

    # Warn about unknown states
    known = set(HCSC_STATE_IDS.keys())
    unknown = combined[~combined["_state"].isin(known)]
    if len(unknown) > 0:
        print(f"    WARNING: {len(unknown)} rows with unknown states: {unknown['_state'].unique().tolist()}")

    print(f"    HCSC total: {len(results)} sub-carrier(s)")
    return results


# ==========================================================
#  SMA READER
# ==========================================================
def read_sma(blob_service_client, blob_name, rule, column_mappings,
             all_rules_df, all_mappings_df, container_name=DEFAULT_CONTAINER):
    """
    Read SMA multi-carrier workbook. One Excel file, many sheets,
    each sheet is a different carrier.

    Returns a list of (carrier_rule, carrier_df) tuples.
    """
    container_client = blob_service_client.get_container_client(container_name)
    blob_data = container_client.get_blob_client(blob_name).download_blob().readall()

    xls = _safe_excel_file(blob_data)
    print(f"    SMA workbook sheets: {xls.sheet_names}")

    sma_rules = all_rules_df[
        all_rules_df["carrier_name"].str.contains("SMA", case=False, na=False) &
        (all_rules_df["process_type"] == "ACU")
    ]

    results = []

    for _, carrier_rule in sma_rules.iterrows():
        carrier_name = carrier_rule["carrier_name"]
        carrier_id = str(carrier_rule.get("carrier_id", ""))
        sheet_hint = str(carrier_rule.get("sheet_name", "")).strip()

        if not sheet_hint or sheet_hint in ("nan", "NA", "None"):
            continue

        matched_sheet = None
        for sheet in xls.sheet_names:
            if sheet_hint.lower() in sheet.lower():
                matched_sheet = sheet
                break

        if not matched_sheet:
            print(f"    {carrier_name}: sheet '{sheet_hint}' not found")
            continue

        print(f"    {carrier_name} -> sheet '{matched_sheet}'")

        try:
            df = pd.read_excel(xls, sheet_name=matched_sheet, dtype=str)
            df.columns = df.columns.str.lower().str.strip()

            if "cigna" in carrier_name.lower():
                df = _sma_cigna_states(df)

            if "humana" in carrier_name.lower():
                df = _sma_humana_filter(df, carrier_name)

            # Get column mappings for this carrier
            carrier_mappings = all_mappings_df[
                (all_mappings_df["carrier_name"] == carrier_name) &
                (all_mappings_df["process_type"] == "ACU")
            ]

            if carrier_mappings.empty:
                carrier_mappings = all_mappings_df[
                    all_mappings_df["carrier_name"].str.contains(
                        carrier_name.replace("SMA - ", "").split()[0],
                        case=False, na=False
                    ) & (all_mappings_df["process_type"] == "ACU")
                ]

            if carrier_mappings.empty:
                print(f"    {carrier_name}: no column mappings found")
                continue

            # Apply column mapping — handle duplicate source columns
            # (e.g., NPN maps to both agent_npn and agent_writing_num)
            col_targets = {}  # {file_col: [canonical1, canonical2, ...]}
            for _, m in carrier_mappings.iterrows():
                file_col = str(m.get("mapping", "NA")).strip().lower()
                canonical = str(m.get("database_column", "")).strip().lower()
                if file_col and file_col not in ("na", "nan", "none") and file_col in df.columns:
                    if file_col not in col_targets:
                        col_targets[file_col] = []
                    col_targets[file_col].append(canonical)

            # Copy columns that map to multiple targets, then rename
            rename_map = {}
            for file_col, targets in col_targets.items():
                if len(targets) == 1:
                    rename_map[file_col] = targets[0]
                else:
                    # First target gets the rename, others get a copy
                    rename_map[file_col] = targets[0]
                    for extra in targets[1:]:
                        df[extra] = df[file_col]

            df = df.rename(columns=rename_map)
            df["carrier_id"] = carrier_id
            df["carrier_name"] = carrier_name
            df = df.dropna(how="all")

            results.append((carrier_rule.to_dict(), df))
            print(f"    {carrier_name}: {len(df)} rows, {len(rename_map)} columns mapped")

        except Exception as e:
            print(f"    ERROR {carrier_name}: {e}")
            continue

    print(f"    SMA total: {len(results)} sub-carrier(s) read")
    return results


def _sma_cigna_states(df):
    """Cigna: individual state columns -> semicolon-separated string."""
    states = ['ak','al','ar','az','ca','co','ct','dc','de','fl','ga','hi','ia','id','il',
              'in','ks','ky','la','ma','md','me','mi','ms','mo','mt','nc','nd','ne','nh','nj',
              'nm','nv','ny','oh','ok','or','pa','pr','ri','sc','sd','tn','tx','us','ut','va',
              'vt','wa','wi','wv','wy']
    state_cols = [s for s in states if s in df.columns]
    if state_cols:
        def _pivot_row(row):
            active = [s.upper() for s in state_cols
                      if str(row.get(s, "")).strip().lower() in ("active", "ready to sell")]
            return "; ".join(active) if active else ""
        df["appointed_state"] = df.apply(_pivot_row, axis=1)
    return df


def _sma_humana_filter(df, carrier_name):
    """Humana: filter by contr_desc_code."""
    if "contr_desc_code" not in df.columns:
        return df
    if "MDC" in carrier_name.upper():
        return df[df["contr_desc_code"].isin(["Medicare", "Medsup", "Achieve"])].copy()
    elif "SUP" in carrier_name.upper():
        return df[df["contr_desc_code"] == "Individual"].copy()
    return df


# ==========================================================
#  QUARTZ READER
# ==========================================================
QUARTZ_ACA_ID = "2931751000481686005"
QUARTZ_MDC_ID = "2931751000736849364"


def read_quartz(blob_service_client, blob_name, rule, column_mappings,
                all_rules_df, all_mappings_df, container_name=DEFAULT_CONTAINER):
    """
    Read Quartz combined ACA+MDC file. One file, two contracts per agent.

    ACA contract: Active if AGENT LOB contains "Individual"
    MDC contract: Active if APPOINTED TO SELL MA = "Yes"
    States:       MA STATE (semicolon-separated)
    RTS (MDC):    READY TO SELL STATUS = "Ready" + extract year from Curriculum Title (PY2026 → 2026)
    AHIP:         AHIP EXPIRATION DATE end-of-year → that year's AHIP

    Returns list of (rule_dict, df) tuples — one for ACA, one for MDC.
    """
    import re

    container_client = blob_service_client.get_container_client(container_name)
    blob_data = container_client.get_blob_client(blob_name).download_blob().readall()

    # Read the data sheet (first sheet, not "guide")
    xls = _safe_excel_file(blob_data)
    data_sheet = [s for s in xls.sheet_names if "guide" not in s.lower()]
    if not data_sheet:
        print(f"    WARNING: No data sheet found in Quartz file")
        return []

    df = pd.read_excel(xls, sheet_name=data_sheet[0], dtype=str)
    df.columns = df.columns.str.strip()
    print(f"    Quartz: {len(df)} rows from sheet '{data_sheet[0]}'")

    # Standardize column access
    col = {c.upper(): c for c in df.columns}

    current_year = str(pd.Timestamp.now().year)
    next_year = str(pd.Timestamp.now().year + 1)

    # ── Extract RTS year from curriculum title (e.g., "...PY2026" → "2026") ──
    curriculum_col = col.get("CURRICULUM - CURRICULUM TITLE (CURRICULUM)", "")
    rts_year = ""
    if curriculum_col and curriculum_col in df.columns:
        def _extract_rts_year(val):
            m = re.search(r'PY(\d{4})', str(val))
            return m.group(1) if m else ""
        df["_rts_year"] = df[curriculum_col].apply(_extract_rts_year)

    # ── Build ACA sub-carrier ──
    lob_col = col.get("AGENT LOB", "")
    if lob_col and lob_col in df.columns:
        aca_mask = df[lob_col].fillna("").str.contains("Individual", case=False, na=False)
        aca_df = df[aca_mask].copy()
    else:
        aca_df = pd.DataFrame()

    # ── Build MDC sub-carrier ──
    ma_col = col.get("APPOINTED TO SELL MA", "")
    if ma_col and ma_col in df.columns:
        mdc_mask = df[ma_col].fillna("").str.strip().str.lower() == "yes"
        mdc_df = df[mdc_mask].copy()
    else:
        mdc_df = pd.DataFrame()

    results = []
    from acu_processor import apply_column_mappings

    # ── ACA output ──
    if not aca_df.empty:
        # Agent column bindings come from the load matrix; reader keeps only the
        # reshape (ACA/MDC split), per-market overrides, and RTS (Bucket C).
        out = apply_column_mappings(aca_df.copy(), column_mappings)
        out["appointed_state"] = ""  # ACA doesn't use MA STATE per guide (override)
        out["contract_status"] = "Active"
        out["appointment_type"] = "Subproducer"  # per guide: ACA = Subproducer
        out["carrier_id"] = QUARTZ_ACA_ID
        out["carrier_name"] = "Quartz - ACA"
        out["market"] = "ACA"

        sub_rule = _quartz_sub_rule(rule, "Quartz - ACA", QUARTZ_ACA_ID)
        results.append((sub_rule, out))
        print(f"      Quartz - ACA: {len(out)} agents")

    # ── MDC output ──
    if not mdc_df.empty:
        out = apply_column_mappings(mdc_df.copy(), column_mappings)
        out["contract_status"] = "Active"
        out["appointment_type"] = "Producer"  # per guide: MDC = Producer
        out["carrier_id"] = QUARTZ_MDC_ID
        out["carrier_name"] = "Quartz - Medicare"
        out["market"] = "MDC"

        # RTS: only if READY TO SELL STATUS = Ready
        rts_col = col.get("READY TO SELL STATUS", "")
        if rts_col and rts_col in mdc_df.columns and "_rts_year" in mdc_df.columns:
            is_ready = mdc_df[rts_col].fillna("").str.strip().str.upper() == "READY"
            rts_years = mdc_df["_rts_year"]

            out["current_rts"] = np.where(
                is_ready & (rts_years == current_year), "Yes", ""
            )
            out["next_rts"] = np.where(
                is_ready & (rts_years == next_year), "Yes", ""
            )
        else:
            out["current_rts"] = ""
            out["next_rts"] = ""

        # AHIP: end-of-year expiration → that year's AHIP
        ahip_col = col.get("AHIP EXPIRATION DATE", "")
        if ahip_col and ahip_col in mdc_df.columns:
            ahip_dates = pd.to_datetime(mdc_df[ahip_col], errors="coerce")
            ahip_year = ahip_dates.dt.year.astype(str).fillna("")
            # If expiration is end of current year → current AHIP
            out["current_rts_date"] = np.where(ahip_year == current_year, mdc_df[ahip_col], "")
            out["next_rts_date"] = np.where(ahip_year == next_year, mdc_df[ahip_col], "")

        sub_rule = _quartz_sub_rule(rule, "Quartz - Medicare", QUARTZ_MDC_ID)
        sub_rule["rts_flag_applicable"] = "N"  # reader already handled RTS
        results.append((sub_rule, out))
        print(f"      Quartz - Medicare: {len(out)} agents ({out['current_rts'].eq('Yes').sum()} RTS ready)")

    print(f"    Quartz total: {len(results)} sub-carrier(s)")
    return results


def _quartz_sub_rule(master_rule, carrier_name, carrier_id):
    """Build a sub-carrier rule dict from the master Quartz rule."""
    return _make_sub_rule(master_rule, carrier_name, carrier_id)


# ==========================================================
#  SHARED HELPER
# ==========================================================
def _make_sub_rule(master_rule, carrier_name, carrier_id,
                   custom_logic_flag=None, custom_module_name=None, rts_flag=None):
    """Build a sub-carrier rule dict from any master rule.
    Optionally enable a handler and/or RTS for the sub-carrier."""
    sub = dict(master_rule) if isinstance(master_rule, dict) else master_rule.to_dict()
    sub["carrier_name"] = carrier_name
    sub["carrier_id"] = carrier_id
    sub["custom_reader_name"] = ""
    sub["custom_logic_flag"] = custom_logic_flag or "N"
    sub["custom_module_name"] = custom_module_name or ""
    sub["rts_flag_applicable"] = rts_flag or "N"
    return sub


# ==========================================================
#  CHRISTUS READER
# ==========================================================
CHRISTUS_ACA_ID = "2931751000020024158"
CHRISTUS_MDC_ID = "2931751000382772962"


def read_christus(blob_service_client, blob_name, rule, column_mappings,
                  all_rules_df, all_mappings_df, container_name=DEFAULT_CONTAINER):
    """
    Read Christus combined ACA+MDC file. Splits by LOB column.

    Reader maps columns + pre-computes notes from suspended states.
    Matrix handles everything else via rules_matrix config:
      - filter_rule_type=STATUS on contract_status (Active/Certified → Active)
      - appointed_state_filter=_state_status:Active/Certified (state filtering)
      - appointment_type_value_map=Downline Only:Subproducer|*:Producer
    """
    container_client = blob_service_client.get_container_client(container_name)
    blob_data = container_client.get_blob_client(blob_name).download_blob().readall()

    fname = blob_name.lower().split("/")[-1]
    if fname.endswith(".csv"):
        try:
            df = pd.read_csv(io.BytesIO(blob_data), dtype=str)
        except UnicodeDecodeError:
            df = pd.read_csv(io.BytesIO(blob_data), dtype=str, encoding="latin-1")
    else:
        try:
            df = pd.read_excel(io.BytesIO(blob_data), dtype=str)
        except Exception:
            df = pd.read_excel(io.BytesIO(blob_data), dtype=str, engine="xlrd")

    df.columns = df.columns.str.strip()
    print(f"    Christus: {len(df)} rows, LOB: {df['LOB'].value_counts().to_dict()}")

    # Column bindings come from the load matrix (applied here, never hardcoded).
    # Readers do format/reshape only; the matrix owns every source->target binding,
    # including identifiers like agent_writing_num <- NPN.
    from acu_processor import apply_column_mappings
    df = apply_column_mappings(df, column_mappings)

    # State status column for matrix filter (appointed_state_filter = _state_status:Active/Certified)
    df["_state_status"] = df["State Status"].fillna("").str.strip()

    # Pre-compute notes from suspended states (reader has all rows, matrix will filter later)
    suspended_mask = df["_state_status"] != "Active/Certified"
    if suspended_mask.any():
        reason_col = df["State Status Reason"].fillna("").str.strip()
        suspended = df[suspended_mask].copy()
        suspended["appointed_state"] = suspended["appointed_state"].fillna("").astype(str).str.strip()
        suspended["_note_part"] = suspended.apply(
            lambda r: f"{r['appointed_state']} ({reason_col[r.name]})"
            if reason_col[r.name] and reason_col[r.name] != "-"
            else r["appointed_state"], axis=1
        )
        agent_notes = suspended.groupby("agent_npn")["_note_part"].apply(
            lambda parts: "Suspended: " + ", ".join(sorted({str(p).strip() for p in parts if str(p).strip()}))
        ).to_dict()
        df["note"] = df["agent_npn"].map(agent_notes).fillna("")
        print(f"    📝 {len(agent_notes)} agent(s) have suspended-state notes")

    # Suspended brokers (agent-level Broker Status = Suspended) have no
    # Active/Certified state rows, so the matrix state filter
    # (appointed_state_filter = _state_status:Active/Certified) would drop them
    # entirely. Per guide they must still surface as "Active - Recertification
    # Needed". Force their _state_status to Active/Certified so they survive the
    # state filter and clear appointed_state (they hold no active state); the
    # status_value_map then maps Suspended -> Active - Recertification Needed,
    # and the dedup step collapses them to one row per broker.
    susp_broker = df["contract_status"].fillna("").str.strip().str.lower() == "suspended"
    if susp_broker.any():
        df.loc[susp_broker, "_state_status"] = "Active/Certified"
        df.loc[susp_broker, "appointed_state"] = ""
        print(f"    ↪️  {df.loc[susp_broker, 'agent_npn'].nunique()} suspended broker(s) retained for recertification")

    # Generalized retention. Per the guide, Status is governed solely by Broker
    # Status (Active/Certified -> Active, Suspended -> Active - Recertification
    # Needed); State Status only governs appointed_state ("add the state if
    # Active/Certified") and is NOT an agent-level exclusion. But the matrix
    # implements the state rule as a row-drop, so an agent who is Active/Certified
    # at the broker level yet has NO Active/Certified state on a LOB (every state
    # Suspended) loses all rows to the filter and disappears from both results and
    # exceptions. Retain those agents per (NPN, LOB): if the group has no
    # Active/Certified state, force _state_status to Active/Certified so a row
    # survives and clear appointed_state (no active state to appoint). Leave
    # contract_status untouched so Broker Status still drives the label, and the
    # suspended-state note computed above rides along. Suspended brokers handled
    # above already have _state_status forced, so they are not re-detected here.
    df["_has_active_state"] = (
        df["_state_status"].fillna("").str.strip().str.lower() == "active/certified"
    )
    grp_has_active = df.groupby(["agent_npn", "LOB"])["_has_active_state"].transform("any")
    no_active = ~grp_has_active.fillna(False).astype(bool)
    if no_active.any():
        df.loc[no_active, "_state_status"] = "Active/Certified"
        df.loc[no_active, "appointed_state"] = ""
        n_rescued = df.loc[no_active].groupby(["agent_npn", "LOB"]).ngroups
        print(f"    ↪️  {n_rescued} agent-LOB(s) with no active state retained (Broker Status drives label)")
    df.drop(columns=["_has_active_state"], inplace=True, errors="ignore")

    # Split by LOB: COMM = ACA, MA = MDC
    lob = df["LOB"].fillna("").str.strip().str.upper()
    aca_mask = lob == "COMM"
    mdc_mask = lob == "MA"

    results = []
    if aca_mask.any():
        aca_df = df[aca_mask].copy()
        aca_df["carrier_id"] = CHRISTUS_ACA_ID
        aca_df["carrier_name"] = "CHRISTUS - ACA"
        results.append((_make_sub_rule(rule, "CHRISTUS - ACA", CHRISTUS_ACA_ID), aca_df))
        print(f"      CHRISTUS - ACA: {len(aca_df)} rows")

    if mdc_mask.any():
        mdc_df = df[mdc_mask].copy()
        mdc_df["carrier_id"] = CHRISTUS_MDC_ID
        mdc_df["carrier_name"] = "CHRISTUS - MDC"
        # Route the MDC split to handle_christus so its Medicare RTS logic runs
        # (Active/Certified → current_rts). _make_sub_rule otherwise forces
        # custom_logic_flag=N → handle_default, which skips the MA RTS step.
        results.append((_make_sub_rule(rule, "CHRISTUS - MDC", CHRISTUS_MDC_ID,
                                       custom_logic_flag="Y", custom_module_name="handle_christus"), mdc_df))
        print(f"      CHRISTUS - MDC: {len(mdc_df)} rows")

    print(f"    Christus total: {len(results)} sub-carrier(s)")
    return results


# ==========================================================
#  COMMUNITY HEALTH CHOICE READER
# ==========================================================
CHC_ACA_ID = "2931751000020024160"
CHC_MDC_ID = "2931751000530052453"


def read_community_health(blob_service_client, blob_name, rule, column_mappings,
                          all_rules_df, all_mappings_df, container_name=DEFAULT_CONTAINER):
    """
    Read Community Health Choice combined ACA+MDC file.

    Association Type: "Marketplace-*" → ACA, "Medicare Advantage-*" → MDC
    Agent Type: ACA → Subproducer, Medicare → Producer (per guide)
    Skip Agency rows (no Agent NPN).
    Association Status: Active → contract status Active
    Filter to current contract year.
    """
    container_client = blob_service_client.get_container_client(container_name)
    blob_data = container_client.get_blob_client(blob_name).download_blob().readall()

    # Read first sheet (data, not guide)
    xls = _safe_excel_file(blob_data)
    data_sheet = [s for s in xls.sheet_names if "guide" not in s.lower()]
    if not data_sheet:
        print(f"    WARNING: No data sheet found")
        return []

    df = pd.read_excel(xls, sheet_name=data_sheet[0], dtype=str,
                        skiprows=int(rule.get("ignore_header_rows", 0) or 0))
    df.columns = df.columns.str.strip().str.replace(r'\s+', ' ', regex=True)

    # If all columns are still "Unnamed", the header row wasn't found — try auto-detecting it
    if all("unnamed" in c.lower() for c in df.columns):
        print(f"    ⚠️ All columns unnamed — scanning for header row...")
        raw_df = pd.read_excel(xls, sheet_name=data_sheet[0], dtype=str, header=None)
        for i in range(min(20, len(raw_df))):
            row_vals = [str(v).strip().lower() for v in raw_df.iloc[i].fillna("").tolist()]
            # Header row likely contains "Agent" or "NPN" or "Association"
            matches = sum(1 for v in row_vals if "agent" in v or "npn" in v or "association" in v)
            if matches >= 2:  # Need at least 2 keyword matches to be confident
                print(f"    📌 Found header row at index {i} ({matches} keyword matches)")
                df = pd.read_excel(xls, sheet_name=data_sheet[0], dtype=str,
                                   skiprows=i, header=0)
                df.columns = df.columns.str.strip().str.replace(r'\s+', ' ', regex=True)
                break

    print(f"    Community Health: {len(df)} rows from '{data_sheet[0]}'")
    print(f"    Columns: {df.columns.tolist()}")

    # Find columns flexibly (case-insensitive, partial match fallback)
    def _find_col(df, name):
        col_map = {c.lower(): c for c in df.columns}
        # Exact match first
        if name.lower() in col_map:
            return col_map[name.lower()]
        # Partial match fallback
        for col_lower, col_orig in col_map.items():
            if name.lower() in col_lower:
                return col_orig
        return name

    col_agent_type = _find_col(df, "Agent Type")
    col_agent_npn = _find_col(df, "Agent NPN")
    col_contract_year = _find_col(df, "Contract Year")
    col_assoc_number = _find_col(df, "Association: Association Number")
    col_agent_name = _find_col(df, "Agent Name")
    col_assoc_status = _find_col(df, "Association Status")
    col_assoc_type = _find_col(df, "Association Type")

    # Validate required columns exist
    required = {"Agent Type": col_agent_type, "Agent NPN": col_agent_npn, "Association Type": col_assoc_type}
    missing_cols = [label for label, col in required.items() if col not in df.columns]
    if missing_cols:
        print(f"    ❌ Community Health: required columns not found: {missing_cols}")
        print(f"    Available columns: {df.columns.tolist()}")
        return []

    # Skip Agency rows (no agent-level data)
    agent_type = df[col_agent_type].fillna("").str.strip()
    df = df[agent_type != "Agency"].copy()
    print(f"    Filtered non-Agency: {len(df)} rows")

    # Skip rows without NPN
    df = df[df[col_agent_npn].notna() & (df[col_agent_npn].str.strip() != "")].copy()

    # Column bindings come from the load matrix (applied here, never hardcoded):
    # agent_npn <- Agent NPN, agent_writing_num <- Association: Association Number,
    # agent_full_name <- Agent Name, contract_status <- Association Status.
    # The guide's status conversion (Active / Inactive -> "Active - Recertification
    # Needed") is a status_value_map on the rule, applied downstream in
    # apply_matrix_flags, inherited by both the ACA and MDC sub-rules.
    from acu_processor import apply_column_mappings
    df = apply_column_mappings(df, column_mappings)

    # Contract Year is tracked only (guide r13); it does NOT gate rows or derive status.
    if col_contract_year and col_contract_year in df.columns:
        df["contract_year"] = df[col_contract_year].fillna("").str.strip()  # tracked per guide
    print(f"    CHC raw Association Status (mapped downstream): {df['contract_status'].value_counts().to_dict()}")

    # Split by Association Type
    assoc_type = df[col_assoc_type].fillna("").str.strip().str.upper()
    aca_mask = assoc_type.str.contains("MARKETPLACE", na=False)
    mdc_mask = assoc_type.str.contains("MEDICARE", na=False)

    results = []

    if aca_mask.any():
        aca_df = df[aca_mask].copy()
        aca_df["appointment_type"] = "Subproducer"  # per guide: ACA = Subproducer
        aca_df["carrier_id"] = CHC_ACA_ID
        aca_df["carrier_name"] = "Community Health Choice - ACA"
        aca_df["market"] = "ACA"
        results.append((_make_sub_rule(rule, "Community Health Choice - ACA", CHC_ACA_ID), aca_df))
        print(f"      Community Health Choice - ACA: {len(aca_df)} rows")

    if mdc_mask.any():
        mdc_df = df[mdc_mask].copy()
        mdc_df["appointment_type"] = "Producer"  # per guide: Medicare = Producer
        mdc_df["carrier_id"] = CHC_MDC_ID
        mdc_df["carrier_name"] = "Community Health Choice - MDC"
        mdc_df["market"] = "MDC"
        results.append((_make_sub_rule(rule, "Community Health Choice - MDC", CHC_MDC_ID), mdc_df))
        print(f"      Community Health Choice - MDC: {len(mdc_df)} rows")

    print(f"    Community Health total: {len(results)} sub-carrier(s)")
    return results


# ==========================================================
#  MOLINA READER
# ==========================================================
MOLINA_ACA_ID = "2931751000020024153"
MOLINA_MDC_ID = "2931751000048354001"


def read_molina(blob_service_client, blob_name, rule, column_mappings,
                all_rules_df, all_mappings_df, container_name=DEFAULT_CONTAINER):
    """
    Read Molina file and split by LOB column.
    LOB = 'ACA' → Molina ACA carrier
    LOB = 'MA'  → Molina MDC carrier
    Broker Status: Active/Certified → Active
    """
    container_client = blob_service_client.get_container_client(container_name)
    blob_data = container_client.get_blob_client(blob_name).download_blob().readall()

    fname = os.path.basename(blob_name).lower()
    try:
        if fname.endswith(".csv"):
            try:
                df = pd.read_csv(io.BytesIO(blob_data), dtype=str)
            except UnicodeDecodeError:
                df = pd.read_csv(io.BytesIO(blob_data), dtype=str, encoding="latin-1")
        else:
            df = pd.read_excel(io.BytesIO(blob_data), dtype=str)
    except Exception:
        import xlrd
        df = pd.read_excel(io.BytesIO(blob_data), dtype=str, engine="xlrd")

    df.columns = df.columns.str.strip()
    print(f"    Molina: {len(df)} rows")

    if "LOB" not in df.columns:
        print(f"    WARNING: No LOB column — processing as ACA only")
        df["carrier_id"] = MOLINA_ACA_ID
        df["carrier_name"] = "Molina-ACA"
        return [(_make_sub_rule(rule, "Molina-ACA", MOLINA_ACA_ID), df)]

    lob = df["LOB"].fillna("").str.strip().str.upper()
    print(f"    LOB distribution: {df['LOB'].value_counts().to_dict()}")

    # Column bindings come from the load matrix (applied here, never hardcoded).
    # Reader does format/reshape only. contract_status is intentionally NOT mapped
    # (Bucket C: derived below from Broker Status + Status Reason), so those raw
    # columns survive this step.
    from acu_processor import apply_column_mappings
    df = apply_column_mappings(df, column_mappings)

    # Broker Status → contract_status
    # Active/Certified → Active
    # Suspended → use Status Reason column for granular status
    #   (e.g., "Pending Certification", "Pending State License", "Pending Contract")
    if "Broker Status" in df.columns:
        broker_status = df["Broker Status"].fillna("").str.strip()
        status_reason = df["Status Reason"].fillna("").str.strip() if "Status Reason" in df.columns else pd.Series([""] * len(df))

        df["contract_status"] = ""
        df.loc[broker_status == "Active/Certified", "contract_status"] = "Active"
        # For Suspended: map to the specific reason
        suspended_mask = broker_status == "Suspended"
        df.loc[suspended_mask & (status_reason != ""), "contract_status"] = status_reason[suspended_mask & (status_reason != "")]
        df.loc[suspended_mask & (status_reason == ""), "contract_status"] = "Suspended"

        suspended_count = suspended_mask.sum()
        if suspended_count > 0:
            print(f"    Molina: {suspended_count} Suspended agents mapped to Status Reason values")
    else:
        df["contract_status"] = ""

    results = []

    # ACA
    aca_mask = lob == "ACA"
    if aca_mask.any():
        aca_df = df[aca_mask].copy()
        aca_df["carrier_id"] = MOLINA_ACA_ID
        aca_df["carrier_name"] = "Molina-ACA"
        aca_df["market"] = "ACA"
        results.append((_make_sub_rule(rule, "Molina-ACA", MOLINA_ACA_ID), aca_df))
        print(f"      Molina-ACA: {len(aca_df)} rows")

    # MDC (MA)
    mdc_mask = lob == "MA"
    if mdc_mask.any():
        mdc_df = df[mdc_mask].copy()
        mdc_df["carrier_id"] = MOLINA_MDC_ID
        mdc_df["carrier_name"] = "Molina-MDC"
        mdc_df["market"] = "MDC"
        # Per guide: "If MDC and Active/Certified in Broker Status, add year MED RTS."
        # contract_status was derived above (Active/Certified -> "Active").
        mdc_df["current_rts"] = np.where(
            mdc_df["contract_status"].fillna("").str.strip().str.lower() == "active", "Yes", ""
        )
        n_rts = int((mdc_df["current_rts"] == "Yes").sum())
        results.append((_make_sub_rule(rule, "Molina-MDC", MOLINA_MDC_ID), mdc_df))
        print(f"      Molina-MDC: {len(mdc_df)} rows ({n_rts} MED RTS)")

    print(f"    Molina total: {len(results)} sub-carrier(s)")
    return results


# ==========================================================
#  HEALTHFIRST READER
# ==========================================================
HEALTHFIRST_ACA_ID = "2931751000337238816"
HEALTHFIRST_MDC_ID = "2931751000481802178"


def read_healthfirst(blob_service_client, blob_name, rule, column_mappings,
                     all_rules_df, all_mappings_df, container_name=DEFAULT_CONTAINER):
    """
    Read Health First combined ACA+MDC file.
    LOA Product: "Commercial" -> ACA, "Medicare" -> MDC
    Appointment Status: Appointed/Pending Enrollment -> Active (via matrix contract_status_value_map)
    NPN is also writing_number.
    """
    container_client = blob_service_client.get_container_client(container_name)
    blob_data = container_client.get_blob_client(blob_name).download_blob().readall()

    fname = os.path.basename(blob_name).lower()
    if fname.endswith(".csv"):
        try:
            df = pd.read_csv(io.BytesIO(blob_data), dtype=str, encoding="utf-8-sig")
        except UnicodeDecodeError:
            df = pd.read_csv(io.BytesIO(blob_data), dtype=str, encoding="latin-1")
    else:
        try:
            xls = _safe_excel_file(blob_data)
        except Exception as e:
            print(f"    ❌ Health First: cannot open Excel file: {e}")
            return []
        data_sheet = [s for s in xls.sheet_names if "guide" not in s.lower()]
        if not data_sheet:
            print(f"    WARNING: No data sheet found")
            return []
        df = pd.read_excel(xls, sheet_name=data_sheet[0], dtype=str)
    df.columns = df.columns.str.strip()
    print(f"    Health First: {len(df)} rows")

    # Column bindings come from the load matrix (applied here, never hardcoded).
    # The LOA split below still sets per-sub-carrier appointment_type / parent_npn
    # (Bucket B constants) because _make_sub_rule clones the base rule rather than
    # each sub-carrier's own default_type_value / parent_npn.
    from acu_processor import apply_column_mappings
    df = apply_column_mappings(df, column_mappings)

    # Split by LOA Product
    loa = df["LOA Product"].fillna("").str.strip().str.upper()
    aca_mask = loa == "COMMERCIAL"
    mdc_mask = loa == "MEDICARE"

    results = []

    if aca_mask.any():
        aca_df = df[aca_mask].copy()
        aca_df["appointment_type"] = "Subproducer"
        aca_df["parent_npn"] = ""  # guide silent on parent; not always Agility
        aca_df["carrier_id"] = HEALTHFIRST_ACA_ID
        aca_df["carrier_name"] = "Health First - ACA"
        aca_df["market"] = "ACA"
        results.append((_make_sub_rule(rule, "Health First - ACA", HEALTHFIRST_ACA_ID), aca_df))
        print(f"      Health First - ACA: {len(aca_df)} rows")

    if mdc_mask.any():
        mdc_df = df[mdc_mask].copy()
        mdc_df["appointment_type"] = "Subproducer"  # guide: All Subproducer
        mdc_df["parent_npn"] = ""
        mdc_df["carrier_id"] = HEALTHFIRST_MDC_ID
        mdc_df["carrier_name"] = "Health First - MDC"
        mdc_df["market"] = "MDC"
        results.append((_make_sub_rule(rule, "Health First - MDC", HEALTHFIRST_MDC_ID), mdc_df))
        print(f"      Health First - MDC: {len(mdc_df)} rows")

    print(f"    Health First total: {len(results)} sub-carrier(s)")
    return results


# ==========================================================
#  ALLSTATE READER
# ==========================================================
ALLSTATE_SUP_ID = "2931751000020024155"


def read_allstate(blob_service_client, blob_name, rule, column_mappings,
                  all_rules_df, all_mappings_df, container_name=DEFAULT_CONTAINER):
    """
    Read AllState Supplemental file. No headers, two columns:
      Column A = "NPN - Name" (Producer)
      Column B = "NPN - Name" (Subproducer under most recent Column A)

    Positional relationship: Column B agents belong to whichever
    Column A entry appeared last above them.
    """
    container_client = blob_service_client.get_container_client(container_name)
    blob_data = container_client.get_blob_client(blob_name).download_blob().readall()

    df = pd.read_excel(io.BytesIO(blob_data), header=None, dtype=str)
    print(f"    AllState: {len(df)} raw rows, {df.columns.tolist()}")

    # Drop fully blank rows
    df = df.dropna(how="all").reset_index(drop=True)

    rows = []
    current_producer_npn = ""

    for _, row in df.iterrows():
        col_a = str(row.get(0, "") or "").strip()
        col_b = str(row.get(1, "") or "").strip()

        if col_a and col_a not in ("nan", "None", ""):
            # Column A = Producer
            npn, name = _parse_npn_name(col_a)
            if npn:
                current_producer_npn = npn
                rows.append({
                    "agent_npn": npn,
                    "agent_writing_num": npn,
                    "agent_full_name": name,
                    "appointment_type": "Producer",
                    "parent_npn": "",
                    "contract_status": "Active",
                })

        if col_b and col_b not in ("nan", "None", ""):
            # Column B = Subproducer under current Producer
            npn, name = _parse_npn_name(col_b)
            if npn:
                rows.append({
                    "agent_npn": npn,
                    "agent_writing_num": npn,
                    "agent_full_name": name,
                    "appointment_type": "Subproducer",
                    "parent_npn": current_producer_npn,
                    "contract_status": "Active",
                })

    if not rows:
        print(f"    WARNING: No agents parsed from AllState file")
        return []

    out = pd.DataFrame(rows)
    out["carrier_id"] = str(rule.get("carrier_id", ALLSTATE_SUP_ID))
    out["carrier_name"] = rule.get("carrier_name", "AllState")

    producers = (out["appointment_type"] == "Producer").sum()
    subs = (out["appointment_type"] == "Subproducer").sum()
    print(f"    AllState: {len(out)} agents ({producers} producers, {subs} subproducers)")
    return [(_make_sub_rule(rule, out["carrier_name"].iloc[0], out["carrier_id"].iloc[0]), out)]


def _parse_npn_name(cell_value):
    """
    Parse 'NPN - Name' format. e.g. '15752925 - ANH TRUONG' -> ('15752925', 'ANH TRUONG')
    Handles edge cases: 'GEN397614 - VAQAR NAQVI' (writing number, not pure NPN)
    """
    if not cell_value or cell_value in ("nan", "None"):
        return "", ""
    parts = cell_value.split(" - ", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    # No separator — try splitting on first space after digits
    parts = cell_value.strip().split(None, 1)
    if len(parts) == 2 and any(c.isdigit() for c in parts[0]):
        return parts[0].strip(), parts[1].strip()
    return cell_value.strip(), ""


def read_bcbs_fl(blob_service_client, blob_name, rule, column_mappings,
                 container_name=DEFAULT_CONTAINER):
    """
    BCBS Florida: One workbook with 3 active tabs — AHIP Needed, Ready To Train,
    Ready To Sell. All agents in these tabs are Active Producers.

    RTS logic: agents on the Ready To Sell tab whose CERTIFICATION STATUS column
    contains 'Ready To Sell {current_year}' get current_rts = Yes.

    Columns across tabs: AGENT WRITING NUMBER (A), AGENT NAME, NPN.
    Ready To Sell has an extra column: CERTIFICATION STATUS (L).
    """
    from datetime import datetime

    container_client = blob_service_client.get_container_client(container_name)
    blob_data = container_client.get_blob_client(blob_name).download_blob().readall()

    xls = _safe_excel_file(blob_data)
    current_year = str(datetime.today().year)
    active_tabs = ["AHIP Needed", "Ready To Train", "Ready To Sell"]

    frames = []
    for tab in active_tabs:
        matching = [s for s in xls.sheet_names if s.lower().strip() == tab.lower().strip()]
        if not matching:
            print(f"    ⚠️  BCBS FL: sheet '{tab}' not found, skipping")
            continue

        df = pd.read_excel(xls, sheet_name=matching[0], dtype=str)
        df.columns = df.columns.str.strip().str.replace("\n", " ", regex=False)

        # Standardize column names
        col_wr = next((c for c in df.columns if "WRITING NUMBER" in c.upper()), None)
        col_name = next((c for c in df.columns if "AGENT NAME" in c.upper()), None)
        col_npn = next((c for c in df.columns if c.upper().strip() == "NPN"), None)
        col_cert = next((c for c in df.columns if "CERTIFICATION STATUS" in c.upper()), None)

        if not col_wr or not col_npn:
            print(f"    ⚠️  BCBS FL: '{tab}' missing key columns (WR={col_wr}, NPN={col_npn})")
            continue

        out = pd.DataFrame({
            "agent_writing_num": df[col_wr].fillna("").astype(str).str.strip(),
            "agent_full_name": df[col_name].fillna("").astype(str).str.strip() if col_name else "",
            "agent_npn": df[col_npn].fillna("").astype(str).str.strip(),
            "appointment_type": "Producer",
            "contract_status": "Active",
            "current_rts": "",
            "current_rts_date": "",
        })

        # RTS: only Ready To Sell tab, only 'Ready To Sell {current_year}'
        if tab.lower() == "ready to sell" and col_cert:
            cert_vals = df[col_cert].fillna("").astype(str).str.strip()
            is_rts = cert_vals.str.lower() == f"ready to sell {current_year}".lower()
            out.loc[is_rts, "current_rts"] = "Yes"
            rts_count = is_rts.sum()
            print(f"    BCBS FL: {tab} — {rts_count}/{len(out)} flagged RTS (current year {current_year})")

        # Drop blank NPN rows
        out = out[out["agent_npn"].str.strip() != ""].reset_index(drop=True)
        frames.append(out)
        print(f"    BCBS FL: {tab} — {len(out)} agents")

    if not frames:
        print(f"    WARNING: No agents parsed from BCBS FL file")
        return pd.DataFrame()

    combined = pd.concat(frames, ignore_index=True)

    # Dedup on NPN — keep the row with RTS if any (prefer Ready To Sell tab)
    combined["_has_rts"] = (combined["current_rts"] == "Yes").astype(int)
    combined = combined.sort_values("_has_rts", ascending=False).drop_duplicates(subset=["agent_npn"]).drop(columns=["_has_rts"]).reset_index(drop=True)

    carrier_id = str(rule.get("carrier_id", ""))
    carrier_name = rule.get("carrier_name", "BCBS FL")
    combined["carrier_id"] = carrier_id
    combined["carrier_name"] = carrier_name

    rts_total = (combined["current_rts"] == "Yes").sum()
    print(f"    BCBS FL: {len(combined)} unique agents, {rts_total} RTS")
    return combined


def read_bcbs_ne(blob_service_client, blob_name, rule, column_mappings,
                 container_name=DEFAULT_CONTAINER):
    """
    BCBS NE ACA: .xls file with 'Carrier Appointments List' sheet.
    Row 0 is a title row (skip via ignore_header_rows=1), row 1 is headers.

    NPN is embedded in 'Item Description' as '\\r\\nNPN = 19089956'.
    Extract via regex. Rows without a parseable NPN go to exceptions.

    All rows in the file are ACTIVE. Appointment Number = writing number.
    Appointed Broker = agent name. States = appointed state.
    """
    import re

    container_client = blob_service_client.get_container_client(container_name)
    blob_data = container_client.get_blob_client(blob_name).download_blob().readall()

    skip = int(rule.get("ignore_header_rows", 0) or 0)
    sheet = rule.get("sheet_name", "Carrier Appointments List")
    df = pd.read_excel(io.BytesIO(blob_data), sheet_name=sheet, skiprows=skip, dtype=str)
    df = df.dropna(how="all").reset_index(drop=True)
    print(f"    BCBS NE: {len(df)} rows from '{sheet}'")

    # Extract NPN from Item Description
    npn_series = (
        df["Item Description"]
        .fillna("")
        .astype(str)
        .str.extract(r"NPN\s*=\s*(\d+)", flags=re.IGNORECASE, expand=False)
    )

    extracted = npn_series.notna().sum()
    print(f"    BCBS NE: NPN extracted from Item Description: {extracted}/{len(df)}")

    out = pd.DataFrame({
        "agent_npn": npn_series.fillna(""),
        "agent_full_name": df["Appointed Broker"].fillna("").astype(str).str.strip(),
        "agent_writing_num": df["Appointment Number"].fillna("").astype(str).str.strip(),
        "contract_status": df["Appointment Status"].fillna("").astype(str).str.strip(),
        "appointed_state": df["States"].fillna("").astype(str).str.strip(),
        "appointment_type": "Producer",
        "current_rts": "",
        "current_rts_date": "",
    })

    carrier_id = str(rule.get("carrier_id", ""))
    carrier_name = rule.get("carrier_name", "BCBS NE ACA")
    out["carrier_id"] = carrier_id
    out["carrier_name"] = carrier_name

    print(f"    BCBS NE: {len(out)} agents ({extracted} with NPN, {len(out) - extracted} without)")
    return out


# ==========================================================
#  PHYSICIANS MUTUAL READER
# ==========================================================
def read_physicians_mutual(blob_service_client, blob_name, rule, column_mappings,
                           all_rules_df, all_mappings_df, container_name=DEFAULT_CONTAINER):
    """
    Physicians Mutual ACU — multi-level hierarchy file (Agency / Level 1-4).
    Per guide, only Level 1 + Level 2 are used (Agency and Levels 3-4 ignored).
    Each file row yields exactly ONE agent record (not both levels):
      • Level 2 Role present → Subproducer only:
          agent_npn & agent_writing_num <- Level 2 NPN, falling back to Level 1 NPN when blank;
          agent_full_name <- Level 2 Name; parent = Level 1 Name (parent_identity_field=NAME).
      • No Level 2 Role → Producer only:
          agent_npn & agent_writing_num <- Level 1 NPN (NPN is also writing number);
          agent_full_name <- Level 1 Name.
    Non-active rows (Appointment != Yes) get blank contract_status and are removed by the
    rule's STATUS=Active filter downstream.
    """
    container_client = blob_service_client.get_container_client(container_name)
    blob_data = container_client.get_blob_client(blob_name).download_blob().readall()
    df = pd.read_csv(io.BytesIO(blob_data), dtype=str).fillna("")
    print(f"    Physicians Mutual: {len(df)} raw hierarchy rows")

    def g(row, col):
        return str(row.get(col, "") or "").strip()

    def is_yes(v):
        return str(v).strip().lower() in ("yes", "y", "true", "1")

    rows = []
    for _, row in df.iterrows():
        l1_npn = g(row, "Level 1 NPN")
        l1_name = g(row, "Level 1 Name")
        l2_role = g(row, "Level 2 Role")
        if l2_role:
            l2_npn = g(row, "Level 2 NPN")
            l2_name = g(row, "Level 2 Name")
            npn = l2_npn if l2_npn else l1_npn
            rows.append({
                "agent_full_name": l2_name,
                "agent_npn": npn,
                "agent_writing_num": npn,
                "contract_date": g(row, "Level 2 Contract"),
                "appointed_state": g(row, "Level 2 State"),
                "contract_status": "Active" if is_yes(g(row, "Level 2 Appointment")) else "",
                "appointment_type": "Subproducer",
                "parent_npn": l1_npn,  # parent producer's NPN (was l1_name — a name, not an NPN)
            })
        elif l1_npn or l1_name:
            rows.append({
                "agent_full_name": l1_name,
                "agent_npn": l1_npn,
                "agent_writing_num": l1_npn,
                "contract_date": g(row, "Level 1 Contract"),
                "appointed_state": g(row, "Level 1 State"),
                "contract_status": "Active" if is_yes(g(row, "Level 1 Appointment")) else "",
                "appointment_type": "Producer",
                "parent_npn": "",
            })

    if not rows:
        print("    WARNING: No agents parsed from Physicians Mutual file")
        return []

    out = pd.DataFrame(rows)
    out["carrier_id"] = str(rule.get("carrier_id", ""))
    out["carrier_name"] = rule.get("carrier_name", "Physicians Mutual")
    prod = int((out["appointment_type"] == "Producer").sum())
    sub = int((out["appointment_type"] == "Subproducer").sum())
    print(f"    Physicians Mutual: {len(out)} agent rows ({prod} producers, {sub} subproducers)")
    return [(_make_sub_rule(rule, out["carrier_name"].iloc[0], out["carrier_id"].iloc[0]), out)]


# ==========================================================
#  READER REGISTRY
# ==========================================================
CUSTOM_READERS = {
    "read_hcsc": read_hcsc,
    "read_sma": read_sma,
    "read_quartz": read_quartz,
    "read_christus": read_christus,
    "read_community_health": read_community_health,
    "read_molina": read_molina,
    "read_healthfirst": read_healthfirst,
    "read_allstate": read_allstate,
    "read_bcbs_fl": read_bcbs_fl,
    "read_bcbs_ne": read_bcbs_ne,
    "read_physicians_mutual": read_physicians_mutual,
}


def get_custom_reader(name):
    return CUSTOM_READERS.get(name)