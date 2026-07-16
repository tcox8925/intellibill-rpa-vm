# ==========================================================
#  bob_processor.py
# ==========================================================
"""
Core BOB processing logic. Matrix-driven, same patterns as ACU.
Flow: Read file → map columns → filter rules → contract_count →
      normalize → agent matching → enrichment → mem_age → outputs

Reuses from acu_processor: read_and_map_file, normalize_values,
    _apply_value_map, _rule_val, _known_map_keys
"""

import io, os, csv, hashlib
import pandas as pd
import numpy as np
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from typing import Dict, List, Optional, Tuple

from utils.azure_blob_utils import DEFAULT_CONTAINER
from config import (PROCESS_COLUMN_MAP, BOB_EXC_CODES, FEATURES,
                    BOB_EXC_LABEL_NO_CONTRACT, BOB_EXC_LABEL_NO_WRITING_NUM,
                    BOB_EXC_LABEL_NAME_NOT_FOUND, BOB_EXC_LABEL_NAME_MULTIPLE,
                    EXCLUDED_CONTRACT_STATUSES, EXCLUDED_AGENT_STATUSES)
from acu_processor import (read_and_map_file, normalize_values, _rule_val, _BLANK_VALS,
                           _apply_value_map, _known_map_keys, load_contracts,
                           _coerce_column_mappings, _resolve_by_name)
from bob_handlers import get_bob_handler
from bob_readers import get_bob_reader

CONTRACTS_TABLE = "wpo.lup_agents_contracts"
BOB_TABLE = "wpo.bob_carrier_memberships"
PROCESS_TYPE = "BOB"


def _exc_code(label):
    """Get BOB exception 'E{id}' string from the runtime-loaded table dict."""
    return BOB_EXC_CODES.get(label, label)


# ── APPLY MATRIX FLAGS (BOB) ──

def apply_bob_matrix_flags(df, rule):
    """
    Apply filter rules and value maps for BOB carriers.
    Uses PROCESS_COLUMN_MAP to route generic rule columns to BOB data columns.
    """
    from acu_processor import apply_matrix_flags
    return apply_matrix_flags(df, rule, process_type=PROCESS_TYPE)


# ── CONTRACT COUNT → MEM_COUNT ──

def apply_contract_count(df, rule):
    """
    Derive mem_count based on contract_count rule:
      C          = hardcode 1 (one contract per row)
      Y          = use file value + 1 (0-based → 1-based)
      N          = keep as-is from file
      ID_SUFFIX  = derive from last 2 digits of mem_id (Imperial)
    """
    cc = _rule_val(rule, "contract_count", "C")

    if "mem_count" not in df.columns:
        df["mem_count"] = np.nan

    if cc == "C":
        df["mem_count"] = 1
    elif cc == "Y":
        df["mem_count"] = pd.to_numeric(df["mem_count"], errors="coerce").fillna(0).astype(int) + 1
    elif cc == "N":
        pass  # keep as-is
    elif cc == "ID_SUFFIX":
        # Imperial: mem_count from last 2 digits of mem_id
        if "mem_id" in df.columns:
            df["mem_count"] = (
                df["mem_id"].astype(str).str.strip().str[-2:]
                .apply(lambda x: int(x) if x.isdigit() else 0)
            )
        else:
            df["mem_count"] = 1
    else:
        print(f"    ⚠️  Unknown contract_count value: {cc} — defaulting to 1")
        df["mem_count"] = 1

    # contract_count OUTPUT column (legacy vault semantic):
    #   case when is_subscriber = 'N' then 0 else 1 end
    # i.e. the subscriber/policyholder row counts as one contract; dependents
    # count as zero. Blank/absent is_subscriber is treated as a subscriber (1).
    if "is_subscriber" in df.columns:
        _sub = df["is_subscriber"].fillna("").astype(str).str.strip().str.upper()
        df["contract_count"] = np.where(_sub == "N", 0, 1)
    else:
        df["contract_count"] = 1

    print(f"    📊 mem_count applied (rule={cc}); contract_count derived from is_subscriber")
    return df


# ── COLUMN FALLBACK (slash notation in load matrix) ──

def apply_column_fallback(df, column_mappings):
    """
    Handle slash-separated fallback columns in the load matrix mapping.
    e.g., mapping='Sub Producer NPN/Producer NPN' means:
      use 'sub producer npn', fall back to 'producer npn' if null.
    This runs BEFORE standard column rename in read_and_map_file.
    """
    column_mappings = _coerce_column_mappings(column_mappings)
    for _, m in column_mappings.iterrows():
        mapping = str(m.get("mapping", "")).strip()
        if "/" in mapping and mapping.lower() != "na":
            parts = [p.strip().lower() for p in mapping.split("/")]
            primary = parts[0]
            fallback = parts[1] if len(parts) > 1 else None
            if primary in df.columns and fallback and fallback in df.columns:
                df[primary] = df[primary].fillna(df[fallback])
    return df


# ── DATE PARSING ──

def parse_date_columns(df, rule):
    """
    Parse date columns listed in PROCESS_COLUMN_MAP['BOB']['date_columns'].
    Handles:
      - 9999 dates in agent_end_date (preserve as-is)
      - Anthem renew_date skip (via skip_date_parse rule)
      - Standard date parsing with coerce
    """
    col_map = PROCESS_COLUMN_MAP[PROCESS_TYPE]
    date_cols = col_map.get("date_columns", [])
    skip_cols = _rule_val(rule, "skip_date_parse", "").split(",")
    skip_cols = [c.strip().lower() for c in skip_cols if c.strip()]

    for col in date_cols:
        if col not in df.columns:
            continue
        if col.lower() in skip_cols:
            print(f"    ⏭️  Skipping date parse for {col}")
            continue

        if col == "agent_end_date":
            # Preserve 9999 dates
            _9999_pattern = r'(^9999[-/]\d{2}[-/]\d{2}$)|(^\d{2}[-/]\d{2}[-/]9999$)'
            special_mask = df[col].astype(str).str.contains(_9999_pattern, na=False, regex=True)
            df.loc[~special_mask, col] = pd.to_datetime(
                df.loc[~special_mask, col], errors="coerce"
            ).dt.strftime("%Y-%m-%d")
        else:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.strftime("%Y-%m-%d")

    return df


# ── MEMBER AGE ──

def compute_mem_age(df, run_date):
    """
    Compute mem_age as age at end of report month.
    run_date is the report_date (YYYY-MM-DD).
    """
    if "mem_dob" not in df.columns:
        return df

    ref_date = pd.Timestamp(run_date) + pd.offsets.MonthEnd(0)
    dob = pd.to_datetime(df["mem_dob"], errors="coerce")
    age = ((ref_date - dob).dt.days / 365.25).apply(lambda x: int(x) if pd.notna(x) else None)
    df["mem_age"] = age
    print(f"    🎂 mem_age computed (ref_date={ref_date.strftime('%Y-%m-%d')})")
    return df


# ── AGENT MATCHING (BOB-specific) ──

def _normalize_name(s):
    """Normalize agent name for matching: lowercase, strip, collapse whitespace."""
    if pd.isna(s) or str(s).strip() in ("", "nan", "None", "NA"):
        return None
    return " ".join(str(s).lower().strip().split())


def _do_match(df, contracts_df, left_col, right_col, match_label, one_to_one=False):
    """
    Generic matching pass. Joins df[left_col] against contracts_df[right_col].
    If one_to_one=True (NAME matching), rows that match multiple contracts → exception.
    Returns (matched_df, unmatched_df).
    """
    has_val = df[left_col].notna() & (df[left_col].astype(str).str.strip() != "")
    to_match = df[has_val].copy()
    no_val = df[~has_val].copy()

    if to_match.empty:
        return pd.DataFrame(columns=df.columns), no_val

    # Build the contract subset for the join. The match key (_ct_key) is created
    # as its OWN column copied from right_col, so it can never collide with the
    # descriptive renames below. (Previously, when right_col == "npn", a single
    # rename dict had both {npn: _ct_key} and {npn: ct_npn} — Python keeps the
    # last value, so _ct_key was never produced and the merge KeyError'd.)
    desc_rename = {"npn": "ct_npn", "name": "ct_name",
                   "first_name": "ct_first_name", "last_name": "ct_last_name",
                   "status": "ct_status", "status_date": "ct_status_date", "id": "ct_contract_id"}
    if right_col not in contracts_df.columns:
        return pd.DataFrame(columns=df.columns), pd.concat([to_match, no_val], ignore_index=True)

    ct = contracts_df.copy()
    ct["_ct_key"] = ct[right_col]
    keep = ["_ct_key"] + [c for c in desc_rename if c in ct.columns]
    ct_subset = ct[keep].rename(columns=desc_rename)

    # Collapse multiple contract rows sharing the same key to a single best row.
    # load_contracts loads ALL statuses, so an agent with a status history (e.g.
    # an active AND a terminated record) has >1 row for the same NPN/WR. Without
    # this, the left-join below duplicates every member that agent wrote —
    # inflating mem_count. Preference: status NOT in EXCLUDED_CONTRACT_STATUSES
    # first, then the most recent status_date.
    if len(ct_subset) and ct_subset["_ct_key"].duplicated().any():
        excl = [s.strip().lower() for s in EXCLUDED_CONTRACT_STATUSES]
        _st = ct_subset.get("ct_status", pd.Series("", index=ct_subset.index)).fillna("").astype(str).str.strip().str.lower()
        ct_subset = ct_subset.assign(
            _excluded=_st.isin(excl),
            _sdt=pd.to_datetime(ct_subset.get("ct_status_date"), errors="coerce"),
        )
        n_before = len(ct_subset)
        ct_subset = (ct_subset.sort_values(["_excluded", "_sdt"], ascending=[True, False])
                              .drop_duplicates(subset=["_ct_key"], keep="first")
                              .drop(columns=["_excluded", "_sdt"]))
        print(f"    🔁 {match_label}: collapsed {n_before - len(ct_subset)} duplicate contract row(s) to one-per-key")

    # Stamp a stable row identity for grouping after the merge.
    # pandas merge resets the index to a fresh 0..N range, making
    # groupby(merged.index) useless for detecting multi-match rows.
    to_match = to_match.copy()
    to_match["_row_uid"] = range(len(to_match))

    merged = to_match.merge(ct_subset, left_on=left_col, right_on="_ct_key", how="left")

    if one_to_one:
        # NAME matching: count matches per ORIGINAL row via _row_uid.
        match_counts = merged.groupby("_row_uid").size()
        ambiguous_uids = set(match_counts[match_counts > 1].index)

        if ambiguous_uids:
            # One representative row per ambiguous original
            ambiguous = merged[merged["_row_uid"].isin(ambiguous_uids)].drop_duplicates(subset=["_row_uid"]).copy()
            for col in [c for c in ambiguous.columns if c.startswith("_ct_") or c.startswith("ct_")]:
                ambiguous.drop(columns=[col], inplace=True, errors="ignore")
            ambiguous["exception_id"] = _exc_code(BOB_EXC_LABEL_NAME_MULTIPLE)
            ambiguous["exception_reason"] = f"Ambiguous {match_label} match (multiple contracts)"

            # Keep only non-ambiguous in merged
            merged = merged[~merged["_row_uid"].isin(ambiguous_uids)]
        else:
            ambiguous = pd.DataFrame(columns=df.columns)
    else:
        ambiguous = pd.DataFrame(columns=df.columns)

    matched = merged[merged["_ct_key"].notna()].copy()
    unmatched = merged[merged["_ct_key"].isna()].copy()

    # Enrich matched rows
    if not matched.empty:
        if "ct_name" in matched.columns:
            matched["agent_fullname"] = matched["ct_name"].fillna("")
        if "ct_first_name" in matched.columns:
            matched["agent_fname"] = matched["ct_first_name"].fillna(matched.get("agent_fname", ""))
        if "ct_last_name" in matched.columns:
            matched["agent_lname"] = matched["ct_last_name"].fillna(matched.get("agent_lname", ""))
        if "ct_contract_id" in matched.columns:
            matched["agent_contract_id"] = matched["ct_contract_id"]
        if "ct_npn" in matched.columns and right_col != "npn":
            # Fill NPN from contract if matched by name or WR
            matched["agent_npn"] = matched["ct_npn"].fillna(matched.get("agent_npn", ""))

    # Clean merge artifacts
    for _df in [matched, unmatched, ambiguous]:
        for col in [c for c in _df.columns if c.startswith("_ct_") or c.startswith("ct_")]:
            _df.drop(columns=[col], inplace=True, errors="ignore")
        _df.drop(columns=["_row_uid"], inplace=True, errors="ignore")

    # Combine unmatched + no_val + ambiguous
    all_unmatched = pd.concat([unmatched, no_val, ambiguous], ignore_index=True)

    print(f"    🔗 {match_label}: {len(matched)} matched, {len(all_unmatched)} unmatched"
          + (f" ({len(ambiguous)} ambiguous)" if len(ambiguous) > 0 else ""))

    return matched, all_unmatched


def match_agents(df, contracts_df, rule):
    """
    Join member data against lup_agents_contracts to enrich with agent info.
    Supports primary identity: NPN, WR, or NAME.
    Fallback identity: WR, NAME, or NPN.

    NAME matching is one-to-one: exactly 1 match = G, 0 or >1 = exception.

    Returns (matched_df, unmatched_df) where:
      matched = txn_status 'G' (good)
      unmatched = txn_status 'E' (exception) with exception codes
    """
    primary = str(rule.get("primary_identity_field", "NPN")).strip().upper()
    fallback = str(rule.get("fallback_identity_field", "WR")).strip().upper()

    if contracts_df.empty:
        df["txn_status"] = "E"
        df["exception_id"] = _exc_code(BOB_EXC_LABEL_NO_CONTRACT)
        df["exception_reason"] = "No contracts loaded for carrier"
        return pd.DataFrame(columns=df.columns), df

    # Normalize join keys
    for col in ["agent_npn", "agent_writing_num"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.strip().str.replace(".0", "", regex=False)
            df[col] = df[col].replace({"None": None, "nan": None, "": None, "NA": None})

    contracts_df = contracts_df.copy()
    for col in ["npn", "writing_number"]:
        if col in contracts_df.columns:
            contracts_df[col] = contracts_df[col].astype(str).str.strip().str.replace(".0", "", regex=False)

    # ── Identity field mapping (NPN/WR use merge-based _do_match) ──
    FIELD_MAP = {
        "NPN":  ("agent_npn",        "npn",            False),
        "WR":   ("agent_writing_num", "writing_number", False),
    }

    def _name_resolve(input_df):
        """Route NAME matching through ACU's _resolve_by_name (substring ilike)."""
        # carrier_agent_name is a canonical column and therefore always present, but
        # for carriers whose agent name is loaded into agent_full_name it is empty.
        # Pick whichever name column is actually populated (prefer carrier_agent_name
        # when it has values) so NAME matching doesn't run against an empty column.
        def _has_values(col):
            if col not in input_df.columns:
                return False
            s = input_df[col].astype(str).str.strip()
            return s.replace({"nan": "", "none": "", "None": ""}).ne("").any()
        nc = "carrier_agent_name" if _has_values("carrier_agent_name") else "agent_full_name"
        return _resolve_by_name(
            input_df, contracts_df,
            exc_not_found=_exc_code(BOB_EXC_LABEL_NAME_NOT_FOUND),
            exc_multiple=_exc_code(BOB_EXC_LABEL_NAME_MULTIPLE),
            name_col=nc,
        )

    # ── Pass 1: Primary ──
    if primary == "NAME":
        matched, unmatched = _name_resolve(df)
    elif primary in FIELD_MAP:
        left_col, right_col, one_to_one = FIELD_MAP[primary]
        if left_col in df.columns and right_col in contracts_df.columns:
            matched, unmatched = _do_match(df, contracts_df, left_col, right_col,
                                           f"Primary ({primary})", one_to_one)
        else:
            matched = pd.DataFrame(columns=df.columns)
            unmatched = df.copy()
    else:
        matched = pd.DataFrame(columns=df.columns)
        unmatched = df.copy()

    # ── Pass 2: Fallback (on unmatched only) ──
    if not unmatched.empty and fallback != primary:
        if fallback == "NAME":
            matched_fb, still_unmatched = _name_resolve(unmatched)
            matched = pd.concat([matched, matched_fb], ignore_index=True)
            unmatched = still_unmatched
        elif fallback in FIELD_MAP:
            left_col, right_col, one_to_one = FIELD_MAP[fallback]
            if left_col in unmatched.columns and right_col in contracts_df.columns:
                matched_fb, still_unmatched = _do_match(unmatched, contracts_df, left_col, right_col,
                                                         f"Fallback ({fallback})", one_to_one)
                matched = pd.concat([matched, matched_fb], ignore_index=True)
                unmatched = still_unmatched

    # ── Pass 3: NAME safety-net ──
    # If NAME wasn't already tried and rows are still unmatched, attempt a name
    # match on whatever name is on file. Rescues carriers whose configured
    # NPN/WR is missing from the file or not in the CRM but whose agent name IS
    # present and unambiguous (e.g. Community MDC "Kim Dinh", Healthfirst MDC
    # "Emily Frias"). Name matching flags multi-hits as exceptions, so this
    # can't silently mis-assign an ambiguous name.
    if not unmatched.empty and "NAME" not in (primary, fallback):
        def _name_available(frame):
            for col in ["carrier_agent_name", "agent_full_name"]:
                if col in frame.columns:
                    s = frame[col].astype(str).str.strip().replace({"nan": "", "none": "", "None": ""})
                    if s.ne("").any():
                        return True
            return False
        if _name_available(unmatched):
            # Drop stale exception coding from the prior passes before re-resolving.
            retry = unmatched.drop(columns=["exception_id", "exception_reason"], errors="ignore").copy()
            matched_nm, still_unmatched = _name_resolve(retry)
            if not matched_nm.empty:
                print(f"    🪄 NAME safety-net rescued {len(matched_nm)} row(s) after {primary}/{fallback} failed")
            matched = pd.concat([matched, matched_nm], ignore_index=True)
            unmatched = still_unmatched

    # ── Clean up temp columns ──
    for _df in [matched, unmatched]:
        for col in ["_match_name"]:
            if col in _df.columns:
                _df.drop(columns=[col], inplace=True, errors="ignore")

    # ── Set txn_status and exception codes ──
    if not matched.empty:
        matched["txn_status"] = "G"

    if not unmatched.empty:
        unmatched["txn_status"] = "E"

        # Rows from ambiguous NAME detection already carry exception_id — preserve them.
        if "exception_id" not in unmatched.columns:
            unmatched["exception_id"] = None
        if "exception_reason" not in unmatched.columns:
            unmatched["exception_reason"] = None
        already_coded = unmatched["exception_id"].notna() & (unmatched["exception_id"].astype(str).str.strip() != "")
        uncoded = ~already_coded

        has_identity = pd.Series(False, index=unmatched.index)
        for col in ["agent_npn", "agent_writing_num", "carrier_agent_name", "agent_full_name"]:
            if col in unmatched.columns:
                has_identity |= unmatched[col].notna() & (unmatched[col].astype(str).str.strip() != "")

        # Identity-field-specific code: primary determines the expected match path.
        #   NPN primary → E13 (no active contracts for NPN)
        #   WR  primary → E14 (no writing number in CRM)
        #   NAME primary → E15 (no name matched)
        EXC_BY_FIELD = {
            "NPN":  (_exc_code(BOB_EXC_LABEL_NO_CONTRACT),     "No active contracts for agent on carrier"),
            "WR":   (_exc_code(BOB_EXC_LABEL_NO_WRITING_NUM),  "No writing number found in CRM for carrier"),
            "NAME": (_exc_code(BOB_EXC_LABEL_NAME_NOT_FOUND),  "No name matched the Agent Name provided by carrier"),
        }
        exc_code, exc_reason = EXC_BY_FIELD.get(
            primary, (_exc_code(BOB_EXC_LABEL_NO_CONTRACT), "No active contracts for agent on carrier"))

        unmatched.loc[uncoded & has_identity, "exception_id"] = exc_code
        unmatched.loc[uncoded & has_identity, "exception_reason"] = exc_reason
        unmatched.loc[uncoded & ~has_identity, "exception_id"] = _exc_code(BOB_EXC_LABEL_NO_WRITING_NUM)
        unmatched.loc[uncoded & ~has_identity, "exception_reason"] = "No agent identity found in file"

    print(f"    ✅ Agent matching: {len(matched)} matched (G), {len(unmatched)} exceptions (E)")
    return matched, unmatched


# ── NORMALIZE IDENTIFIERS ──

def normalize_identifiers(df):
    """Clean NPN, writing_num, and other identifier columns."""
    for col in ["agent_npn", "agent_writing_num"]:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(r'[=()" ]', '', regex=True).str.strip()
            df[col] = df[col].str.replace(".0", "", regex=False)
            df[col] = df[col].replace({"None": None, "nan": None, "": None})

    return df


# ── MULTI-FILE DEDUP ──

def dedup_multi_file(main_df, secondary_df, rule, process_type="BOB"):
    """
    Multi-file dedup with main file precedence.

    For ACU (agent-level):
      - From secondary, only pull agents whose identity (NPN/WR/NAME)
        doesn't already exist in main results.

    For BOB (member-level, two-tier):
      Tier 1: New agents (not in main at all) → pull ALL their members
      Tier 2: Existing agents (already in main) → only pull members whose
              mem_id or mem_policy_num doesn't already exist in main.

    Both DataFrames should already be normalized to canonical columns
    (via read_and_map_file), so schema differences don't matter.
    """
    if main_df.empty:
        return secondary_df
    if secondary_df.empty:
        return main_df

    # Determine agent identity column
    primary = str(rule.get("primary_identity_field", "NPN")).strip().upper()
    agent_col_map = {"NPN": "agent_npn", "WR": "agent_writing_num", "NAME": "carrier_agent_name"}
    agent_col = agent_col_map.get(primary, "agent_npn")

    # Fallback if primary col doesn't exist
    if agent_col not in main_df.columns or agent_col not in secondary_df.columns:
        for fallback_col in ["agent_npn", "agent_writing_num", "carrier_agent_name"]:
            if fallback_col in main_df.columns and fallback_col in secondary_df.columns:
                agent_col = fallback_col
                break
        else:
            print(f"    ⚠️  Multi-file dedup: no valid agent column — concatenating all rows")
            return pd.concat([main_df, secondary_df], ignore_index=True)

    # Get unique agents in main
    main_agents = set(
        main_df[agent_col].dropna().astype(str).str.strip().str.lower().unique()
    ) - {"", "nan", "none"}

    sec_agent_vals = secondary_df[agent_col].fillna("").astype(str).str.strip().str.lower()

    if process_type == "ACU":
        # ── ACU: agent-only dedup ──
        # Keep only agents from secondary that don't exist in main
        is_new_agent = ~sec_agent_vals.isin(main_agents)
        new_from_sec = secondary_df[is_new_agent].copy()
        print(f"    🔗 ACU multi-file: {len(secondary_df)} secondary → "
              f"{len(new_from_sec)} new agents (dropped {len(secondary_df) - len(new_from_sec)})")
        return pd.concat([main_df, new_from_sec], ignore_index=True)

    # ── BOB: two-tier dedup ──
    is_new_agent = ~sec_agent_vals.isin(main_agents)
    is_existing_agent = sec_agent_vals.isin(main_agents)

    # Tier 1: New agents — take all their members
    tier1 = secondary_df[is_new_agent].copy()

    # Tier 2: Existing agents — only take new members
    tier2_pool = secondary_df[is_existing_agent].copy()
    tier2 = pd.DataFrame(columns=secondary_df.columns)

    if not tier2_pool.empty:
        # Member identity per row: mem_id if present, else fall back to member name
        # (mem_full_name, or first+last, or mem_policy_num as a last resort).
        # The key is always scoped by agent_npn — a member is unique within an agent.
        def _member_key(d):
            blank = pd.Series([""] * len(d), index=d.index)
            npn = (d["agent_npn"].fillna("").astype(str).str.strip().str.lower()
                   if "agent_npn" in d.columns else blank)
            mid = (d["mem_id"].fillna("").astype(str).str.strip()
                   if "mem_id" in d.columns else blank.copy())
            if "mem_full_name" in d.columns:
                name = d["mem_full_name"].fillna("").astype(str).str.strip()
            elif "mem_fname" in d.columns or "mem_lname" in d.columns:
                fn = d["mem_fname"].fillna("").astype(str).str.strip() if "mem_fname" in d.columns else blank
                ln = d["mem_lname"].fillna("").astype(str).str.strip() if "mem_lname" in d.columns else blank
                name = (fn + " " + ln).str.strip()
            elif "mem_policy_num" in d.columns:
                name = d["mem_policy_num"].fillna("").astype(str).str.strip()
            else:
                name = blank.copy()
            # mem_id when present, else the name fallback
            member = mid.where(mid.astype(str).str.strip() != "", name)
            return npn + "|" + member.astype(str).str.strip().str.lower()

        main_key_set = set(_member_key(main_df).values)
        sec_keys = _member_key(tier2_pool)
        new_members = ~sec_keys.isin(main_key_set)
        tier2 = tier2_pool[new_members].copy()
        print(f"    🔗 BOB tier2 member key = agent_npn + (mem_id else name)")

    total_new = len(tier1) + len(tier2)
    total_dropped = len(secondary_df) - total_new
    print(f"    🔗 BOB multi-file: {len(secondary_df)} secondary → "
          f"{len(tier1)} new-agent rows + {len(tier2)} new-member rows = {total_new} kept "
          f"(dropped {total_dropped})")

    return pd.concat([main_df, tier1, tier2], ignore_index=True)


# ── GENERATE TXN IDS ──

_UID_TEST_NEXT = {}  # process_type -> next seq, keeps test-mode ids unique across carriers


def reserve_uid_block(conn, process_type, table_name, n, test_mode=False):
    """Reserve n unique txn_ids from the wpo.ops_uid_control ledger.

    Format {julian}{seq:08d}, continuing the day's sequence and resetting on a new
    day — the same scheme ACU uses. In prod the issued ids are written back so the
    next carrier/run continues from here (sequential carrier processing => no
    overlap). In test mode nothing is written; an in-process counter keeps ids
    unique across carriers within the run.
    """
    if n <= 0:
        return []
    pt = str(process_type).strip().upper()
    julian = datetime.now().strftime("%y%j")

    if test_mode and pt in _UID_TEST_NEXT:
        seq_start = _UID_TEST_NEXT[pt]
    else:
        seq_start = 1
        try:
            last_df = pd.read_sql(
                f"SELECT MAX(uid) AS uid FROM wpo.ops_uid_control WHERE process_type = '{pt}'", conn)
            last_id = last_df["uid"].iloc[0] if (not last_df.empty and pd.notna(last_df["uid"].iloc[0])) else None
            if last_id:
                s = str(int(last_id))
                seq_start = int(s[5:]) + 1 if s[:5] == julian else 1
        except Exception as e:
            print(f"    ⚠️  uid ledger read failed ({e}); starting sequence at 1")
            seq_start = 1

    ids = [f"{julian}{str(seq_start + i).zfill(8)}" for i in range(n)]

    if test_mode:
        _UID_TEST_NEXT[pt] = seq_start + n
    else:
        try:
            uid_df = pd.DataFrame({"uid": ids, "process_type": pt, "table_name": table_name})
            buf = io.StringIO()
            uid_df.to_csv(buf, index=False, header=False)
            buf.seek(0)
            cur = conn.cursor()
            cur.copy_expert("COPY wpo.ops_uid_control(uid, process_type, table_name) FROM STDIN WITH CSV", buf)
            conn.commit()
            cur.close()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"    ⚠️  uid ledger write failed ({e}); ids issued but not persisted")
    return ids


def generate_txn_ids(n, prefix="BOB"):
    """Legacy local generator (kept for back-compat). Not ledger-backed — prefer
    reserve_uid_block, which guarantees uniqueness across carriers and runs."""
    julian = datetime.today().strftime("%y%j")
    return [f"{julian}{str(i).zfill(8)}" for i in range(1, n + 1)]


# ── LENGTH VALIDATION ──

def validate_row_count(input_count, output_count, carrier_name):
    """
    Verify that agent matching didn't cause row multiplication
    (e.g., from duplicate contracts creating Cartesian products).
    """
    if output_count > input_count:
        ratio = output_count / input_count
        print(f"    🚨 Row multiplication detected for {carrier_name}: {input_count} → {output_count} ({ratio:.1f}x)")
        return False
    return True


# ── PROCESS CARRIER (main entry point) ──

def process_bob_carrier(blob_service_client, conn, rule, column_mappings, blob_names,
                        temp_dir, run_date, container_name=DEFAULT_CONTAINER, pre_read_df=None):
    """
    Process a single BOB carrier end-to-end.
    Returns metrics dict with results + exceptions DataFrames.
    """
    column_mappings = _coerce_column_mappings(column_mappings)
    carrier_name = rule["carrier_name"]
    _cid = rule["carrier_id"]
    carrier_id = str(int(_cid)) if isinstance(_cid, float) else str(_cid).strip().replace(".0", "")
    report_level = _rule_val(rule, "report_level", "DETAIL")

    print(f"\n  🔄 {carrier_name} BOB ({len(blob_names) if blob_names else 0} file(s), level={report_level})")

    handler = get_bob_handler(rule)
    print(f"    🔧 Handler: {handler.__name__}")

    # ── READ ──
    reader_name = _rule_val(rule, "custom_reader_name")

    if pre_read_df is not None:
        # HCSC/SMA sub-carriers: data already read by the multi-carrier reader.
        # .copy() so downstream in-place edits don't trip SettingWithCopyWarning
        # on a slice handed in by the reader/groupby.
        df = pre_read_df.copy()
        print(f"    📄 Pre-read: {len(df)} rows")
    elif reader_name:
        reader_fn = get_bob_reader(reader_name)
        if reader_fn:
            print(f"    📖 Custom reader: {reader_name}")
            df = reader_fn(blob_service_client, blob_names[0], rule, column_mappings, container_name)
        else:
            print(f"    ⚠️  Reader '{reader_name}' not found — falling back to standard")
            frames = [read_and_map_file(blob_service_client, b, rule, column_mappings, container_name)
                      for b in blob_names]
            df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    else:
        # Standard reader — handle multi-file
        multi_file = _rule_val(rule, "multi_file_enabled", "N")
        if multi_file == "Y" and len(blob_names) > 1:
            main_id = _rule_val(rule, "main_file_identifier", "")
            main_blobs = [b for b in blob_names if main_id.lower() in os.path.basename(b).lower()] if main_id else [blob_names[0]]
            sec_blobs = [b for b in blob_names if b not in main_blobs]

            main_frames = [read_and_map_file(blob_service_client, b, rule, column_mappings, container_name)
                           for b in main_blobs]
            main_df = pd.concat(main_frames, ignore_index=True) if main_frames else pd.DataFrame()

            sec_frames = [read_and_map_file(blob_service_client, b, rule, column_mappings, container_name)
                          for b in sec_blobs]
            sec_df = pd.concat(sec_frames, ignore_index=True) if sec_frames else pd.DataFrame()

            df = dedup_multi_file(main_df, sec_df, rule, process_type=PROCESS_TYPE)
        else:
            frames = [read_and_map_file(blob_service_client, b, rule, column_mappings, container_name)
                      for b in blob_names]
            df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    if df.empty:
        return _empty_bob_metrics(carrier_name, carrier_id, "no_data")

    total_rows = len(df)

    # ── NORMALIZE ──
    df = normalize_values(df)
    df = normalize_identifiers(df)

    # A file whose columns are all blank/unmapped can normalize down to an
    # empty or column-less frame. The very next step (apply_matrix_flags) does
    # a scalar assignment `df["contract_status"] = "Active"` which raises
    # "cannot set a frame with no defined index and a scalar" on such a frame
    # (this was the NEW ERA failure). Re-check here and exit cleanly as no_data.
    if df.empty or len(df.columns) == 0:
        return _empty_bob_metrics(carrier_name, carrier_id, "no_data")

    # ── FILTER + VALUE MAPS ──
    df = apply_bob_matrix_flags(df, rule)
    # Capture rows the STATUS/CONTAINS/DATE filter removed NOW: df.attrs does not
    # survive the reassignments below (handler, date parse, metadata, matching),
    # so grab the stash immediately. These are surfaced as exceptions at return so
    # the file total reconciles against results + exceptions (no silent drops) —
    # mirrors the ACU processor, which BOB was missing.
    _status_filtered = getattr(df, "attrs", {}).pop("_status_filtered", None)

    # ── HANDLER (carrier-specific transforms) ──
    # report_date (= run_date, the file's snapshot date) is needed by some
    # handlers (e.g. Alignment's effective-date rule), so set it before the call.
    df["report_date"] = run_date
    df = handler(df, rule)

    # ── CONTRACT COUNT → MEM_COUNT ──
    df = apply_contract_count(df, rule)

    # ── DATES ──
    df = parse_date_columns(df, rule)

    # ── MEM_AGE ──
    df = compute_mem_age(df, run_date)

    # ── ADD METADATA ──
    df["carrier_id"] = carrier_id
    df["carrier_name"] = carrier_name
    df["mem_market"] = df.get("mem_market", rule.get("contract_type", ""))
    df["process_date"] = run_date
    df["load_date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df["report_date"] = run_date

    # Generate txn_ids (ledger-backed: unique across carriers and runs)
    txn_ids = reserve_uid_block(conn, "BOB", "bob_carrier_memberships",
                                len(df), FEATURES.get("test_mode", False))
    df["txn_id"] = txn_ids

    # ── SUMMARY CARRIERS: skip agent matching, write directly ──
    if report_level == "SUMMARY":
        df["txn_status"] = "S"
        print(f"    📋 Summary carrier — skipping agent matching, {len(df)} rows")
        exc_sf = _surface_filtered_exceptions(pd.DataFrame(), _status_filtered,
                                              rule, carrier_name, carrier_id, run_date)
        return _build_bob_metrics(carrier_name, carrier_id, total_rows,
                                  results_df=df, exceptions_df=exc_sf,
                                  temp_dir=temp_dir, status="success")

    # ── DETAIL CARRIERS: agent matching ──
    # Load contracts
    if "carrier_id" in df.columns and df["carrier_id"].nunique() > 1:
        # Multi-carrier-id (HCSC): load contracts for all
        unique_cids = [str(c) for c in df["carrier_id"].dropna().unique() if str(c).strip()]
        contracts_frames = []
        for cid in unique_cids:
            ct = load_contracts(conn, cid)
            if not ct.empty:
                ct["_carrier_id"] = cid
                contracts_frames.append(ct)
        contracts_df = pd.concat(contracts_frames, ignore_index=True) if contracts_frames else pd.DataFrame()
    else:
        contracts_df = load_contracts(conn, carrier_id)

    matched_df, unmatched_df = match_agents(df, contracts_df, rule)

    # Length validation
    output_count = len(matched_df) + len(unmatched_df)
    if not validate_row_count(total_rows, output_count, carrier_name):
        # Dedup matched to remove Cartesian products from contract join
        if not matched_df.empty and "agent_npn" in matched_df.columns:
            before = len(matched_df)
            matched_df = matched_df.drop_duplicates(subset=["txn_id"])
            print(f"    🧹 Deduped matched: {before} → {len(matched_df)}")

    # Combine results
    results_df = pd.concat([matched_df, unmatched_df], ignore_index=True)

    # Surface rows the STATUS/CONTAINS/DATE filter removed (pre-identity) as
    # tracked exceptions so file total = results + exceptions (no silent drops).
    exceptions_df = _surface_filtered_exceptions(unmatched_df, _status_filtered,
                                                 rule, carrier_name, carrier_id, run_date)

    return _build_bob_metrics(carrier_name, carrier_id, total_rows,
                              results_df=results_df,
                              exceptions_df=exceptions_df,
                              temp_dir=temp_dir, status="success",
                              contracts_loaded=len(contracts_df))


# ── METRICS BUILDERS ──

def _surface_filtered_exceptions(exceptions_df, status_filtered, rule,
                                 carrier_name, carrier_id, run_date):
    """Re-emit rows the matrix STATUS/CONTAINS/DATE filter removed as tracked
    exceptions, so the file total reconciles against results + exceptions and
    excluded members never silently disappear (mirrors the ACU processor).

    Member-level: one exception row per dropped member. The rows are captured
    pre-identity/pre-transform, so we stamp on the carrier + exception metadata
    the exception report expects; other columns align on concat (NaN-filled).
    """
    if not isinstance(status_filtered, pd.DataFrame) or status_filtered.empty:
        return exceptions_df if exceptions_df is not None else pd.DataFrame()

    sf = status_filtered.copy()
    ftype = str(_rule_val(rule, "filter_rule_type", "")).strip().upper()
    fcol = _rule_val(rule, "filter_column", "")

    if ftype == "DATE":
        sf["exception_reason"] = (f"Excluded — coverage past term date ('{fcol}')"
                                  if fcol else "Excluded — coverage past term date")
    elif fcol and fcol in sf.columns:
        rawv = sf[fcol].fillna("").astype(str).str.strip()
        sf["exception_reason"] = (f"Filtered out by {ftype or 'STATUS'} filter on "
                                  f"'{fcol}': '") + rawv + "'"
    else:
        sf["exception_reason"] = f"Filtered out by {ftype or 'STATUS'} filter"

    sf["exception_id"] = _exc_code("StatusFiltered")
    sf["txn_status"] = "E"
    sf["carrier_id"] = carrier_id
    sf["carrier_name"] = carrier_name
    sf["report_date"] = run_date
    sf["process_date"] = run_date

    if exceptions_df is None or exceptions_df.empty:
        out = sf
    else:
        out = pd.concat([exceptions_df, sf], ignore_index=True)
    print(f"    🔧 Filter-dropped rows surfaced as exceptions: {len(sf)}")
    return out


def _empty_bob_metrics(cn, ci, status, errors=None):
    return {
        "carrier_name": cn, "carrier_id": ci, "total_rows": 0,
        "results_count": 0, "exceptions_count": 0, "exception_rate": 0,
        "status": status, "errors": errors or [],
        "results_df": pd.DataFrame(), "exceptions_df": pd.DataFrame(),
    }


def _build_bob_metrics(cn, ci, total, results_df, exceptions_df, temp_dir, status, contracts_loaded=0):
    results_count = len(results_df[results_df.get("txn_status", pd.Series(dtype=str)) != "E"]) if not results_df.empty else 0
    exc_count = len(exceptions_df)
    exc_rate = round(exc_count / total * 100, 1) if total > 0 else 0

    return {
        "carrier_name": cn, "carrier_id": ci, "total_rows": total,
        "results_count": results_count, "exceptions_count": exc_count,
        "exception_rate": exc_rate, "status": status,
        "results_df": results_df, "exceptions_df": exceptions_df,
        "contracts_loaded": contracts_loaded,
    }
