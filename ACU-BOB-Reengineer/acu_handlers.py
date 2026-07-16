# ==========================================================
#  acu_handlers.py
# ==========================================================
"""
Carrier-specific handler functions for ACU processing.
Each handler is a function: receives DataFrame + rule, returns modified DataFrame.
Standard carriers use handle_default (no-op - matrix drives everything).
handler_map dict at bottom maps custom_module_name to functions.

Handlers that have been ELIMINATED (now matrix-driven):
  - handle_devoted_mdc:  rts_date_rule=TODAY replaces RTS date = today
  - handle_hcsc:         read_hcsc reader handles state split
  - handle_christus_aca: read_christus reader handles status year logic
  - SMA BCBS FL RTS:     rts_date_rule=TODAY replaces RTS date = today

Carriers that DON'T need handlers (matrix-driven instead):
  - Ambetter:       STATUS filter handles Active filtering
  - ManhattanLife:  CONTAINS filter handles substring Active check
  - New Era:        CONTAINS filter handles substring Active check
  - Pivot Health:   appointment_type_value_map NULL:Producer|*:Subproducer
  - Devoted Health: rts_date_rule=TODAY + appointment_type_value_map + PLAN_YEAR rts_filter
"""

import pandas as pd
import numpy as np
from datetime import datetime


def handle_default(df, rule):
    """Standard carriers. No custom logic - matrix flags handle everything."""
    return df


def handle_sma(df, rule):
    """
    SMA carriers - per-carrier custom logic from legacy transformations().
    Matches on carrier_name (set by SMA reader).
    """
    carrier = str(rule.get("carrier_name", "")).upper()
    current_year = str(datetime.today().year)
    next_year = str(datetime.today().year + 1)

    # ── set_all_entries_active: override status to Active ──
    active_override = {"ANTHEM", "AETNA", "BCBS FL", "FREEDOM", "HEALTHSUN",
                       "HUMANA MDC", "HUMANA SUP", "UHC", "WELLCARE", "ZING"}
    carrier_suffix = carrier.replace("SMA - ", "").strip()
    if carrier_suffix in active_override and "contract_status" in df.columns:
        df["contract_status"] = "Active"

    # ── Anthem: Paid Name == Agent Name -> Producer, else Subproducer ──
    if "ANTHEM" in carrier:
        if "appointment_type" in df.columns and "agent_full_name" in df.columns:
            df["appointment_type"] = np.where(
                df["appointment_type"].fillna("").str.strip() == df["agent_full_name"].fillna("").str.strip(),
                "Producer", "Subproducer"
            )
            print(f"    🔧 SMA Anthem appointment_type (name match)")

    # ── Wellcare: ignore Comm_Assign for appointment_type. Parent NPN extraction. ──
    # NOTE: RTS date=today now handled by rts_date_rule=TODAY in matrix
    elif "WELLCARE" in carrier:
        if "appointment_type" in df.columns:
            df["appointment_type"] = ""
        # Parent NPN: extract from "FMO - NPN - Name" format
        if "parent_npn" in df.columns:
            df["parent_npn"] = df["parent_npn"].apply(
                lambda x: str(x).split(" - ")[1].strip()
                if " - " in str(x) else ""
            )
        print(f"    🔧 SMA Wellcare: appointment_type cleared, parent_npn extracted")

    # ── Zing: ignore COMP_ASSIGN for appointment_type. RTS year routing. ──
    elif "ZING" in carrier:
        if "appointment_type" in df.columns:
            df["appointment_type"] = ""
        if "current_rts" in df.columns:
            df["current_rts"] = np.where(
                df["current_rts"].astype(str).str.strip() == current_year, "Yes", ""
            )
            df["current_rts_date"] = np.where(
                df["current_rts"] == "Yes", df.get("current_rts_date", ""), ""
            )
        print(f"    🔧 SMA Zing: RTS year routing")

    # ── UHC: RTS year routing - current/next only if date exists AND year matches ──
    elif "UHC" in carrier:
        if "current_rts" in df.columns and "current_rts_date" in df.columns:
            df["current_rts"] = np.where(
                df["current_rts_date"].notna() & (df["current_rts_date"].str.strip() != ""),
                df["current_rts"], ""
            )
            df["current_rts"] = np.where(
                df["current_rts"].astype(str).str.strip() == current_year, "Yes", ""
            )
            df["current_rts_date"] = np.where(df["current_rts"] == "Yes", df["current_rts_date"], "")
        if "next_rts" in df.columns and "next_rts_date" in df.columns:
            df["next_rts"] = np.where(
                df["next_rts_date"].notna() & (df["next_rts_date"].str.strip() != ""),
                df["next_rts"], ""
            )
            df["next_rts"] = np.where(
                df["next_rts"].astype(str).str.strip() == next_year, "Yes", ""
            )
            df["next_rts_date"] = np.where(df["next_rts"] == "Yes", df["next_rts_date"], "")
        print(f"    🔧 SMA UHC RTS year routing ({current_year}/{next_year})")

    # ── Healthspring: RTS Ready->Yes. Status remap now in contract_status_value_map ──
    elif "HEALTHSPRING" in carrier:
        if "current_rts" in df.columns:
            df["next_rts"] = df["current_rts"].apply(
                lambda x: "Yes" if str(x).strip().lower() == "ready" else ""
            )
            df["current_rts"] = df["current_rts"].apply(
                lambda x: f"{next_year} MED RTS checked" if str(x).strip().lower() == "ready" else ""
            )
            df["current_rts_date"] = ""
            df["next_rts_date"] = ""
        print(f"    🔧 SMA Healthspring status remap + RTS")

    # ── BCBS FL: RTS dates now handled by rts_date_rule=TODAY in matrix ──
    # No handler logic needed

    # ── Molina: RTS swap + clear terminated states ──
    elif "MOLINA" in carrier:
        if "next_rts" in df.columns:
            df["current_rts"] = np.where(df["next_rts"] == "Yes", "Yes", "")
            df["current_rts_date"] = ""
            df["next_rts"] = ""
            df["next_rts_date"] = ""
        if "appointed_state" in df.columns and "contract_status" in df.columns:
            df["appointed_state"] = np.where(
                df["contract_status"].astype(str).str.strip() == "Terminated",
                "0", df["appointed_state"]
            )
        print(f"    🔄 SMA Molina RTS swap + terminated state clear")

    return df


def handle_allstate_aca(df, rule):
    """Allstate ACA (CSV format): NPN extracted from writing_num (split on '-')."""
    if "agent_writing_num" in df.columns:
        df["agent_npn"] = np.where(
            df["agent_writing_num"].notna(),
            df["agent_writing_num"].apply(lambda x: str(x).split("-")[0].strip() if isinstance(x, str) else None),
            df["appointment_type"].apply(lambda x: str(x).split("-")[0].strip() if isinstance(x, str) else None),
        )
        df["agent_writing_num"] = df["agent_npn"]
        df["appointment_type"] = df["appointment_type"].apply(
            lambda x: "Producer" if x is None or str(x).strip() == "" else "Subproducer"
        )
        df["contract_date"] = ""
        print(f"    🔧 Allstate ACA NPN extraction applied")
    return df


def handle_oscar_aca(df, rule):
    """
    Oscar ACA:
      - contract_status remap now handled by contract_status_value_map in matrix
      - Agents without writing_num get "Pending Sale - {NPN}"
      - Filter out agents without any active states
    """
    if "agent_writing_num" in df.columns and "agent_npn" in df.columns:
        df["agent_writing_num"] = df.apply(
            lambda row: f"Pending Sale - {row['agent_npn']}"
            if pd.isna(row.get("agent_writing_num")) or str(row.get("agent_writing_num", "")).strip() in ("", "Pending Sale")
            else row["agent_writing_num"], axis=1
        )

    if "appointed_state" in df.columns:
        before = len(df)
        df = df[df["appointed_state"].notna() & (df["appointed_state"].astype(str).str.strip() != "")]
        if len(df) < before:
            print(f"    🔧 Oscar ACA: filtered {before - len(df)} rows without active states")

    print(f"    🔧 Oscar ACA: status remap + writing_num fill + state filter")
    return df


def handle_aflac(df, rule):
    """
    Aflac:
      - IMMEDIATEUPLINEAGENTNUMBER = TEW5007730 -> Producer, else -> Subproducer
      - contract_status remap (A01->Active) now handled by contract_status_value_map in matrix
      - Parent: upline agent number for Subproducers
    """
    if "appointment_type" in df.columns:
        df["_upline"] = df["appointment_type"].fillna("").str.strip()
        df["appointment_type"] = np.where(
            df["_upline"] == "TEW5007730", "Producer", "Subproducer"
        )
        if "parent_npn" not in df.columns:
            df["parent_npn"] = ""
        df["parent_npn"] = np.where(
            df["appointment_type"] == "Subproducer", df["_upline"], ""
        )
        df.drop(columns=["_upline"], errors="ignore", inplace=True)

    print(f"    🔧 Aflac: appointment_type by upline")
    return df


def handle_prominence(df, rule):
    """
    Prominence Health MDC:
      - RTS Status = Completed + RTS Benefit Year = current year -> current_rts = Yes
      - RTS Status = Completed + RTS Benefit Year = next year -> next_rts = Yes
      - In Progress -> leave blank
    """
    current_year = str(datetime.today().year)
    next_year = str(datetime.today().year + 1)

    rts_status = df["current_rts"].fillna("").str.strip() if "current_rts" in df.columns else pd.Series([""] * len(df))
    rts_year = df["next_rts"].fillna("").str.strip() if "next_rts" in df.columns else pd.Series([""] * len(df))

    is_completed = rts_status.str.lower() == "completed"

    df["current_rts"] = np.where(is_completed & (rts_year == current_year), "Yes", "")
    df["next_rts"] = np.where(is_completed & (rts_year == next_year), "Yes", "")
    df["current_rts_date"] = ""
    df["next_rts_date"] = ""

    print(f"    🔧 Prominence: conditional RTS ({is_completed.sum()} completed, year={current_year}/{next_year})")
    return df


def handle_uhc_aca(df, rule):
    """
    UHC-ACA:
      - appointment_type column contains Level 2 name (from load matrix mapping)
      - If Level 2 == Agent Name -> Producer
      - If Level 2 != Agent Name -> Subproducer
      - Blank Level 2 -> Producer (top of hierarchy)
    """
    if "appointment_type" in df.columns and "agent_full_name" in df.columns:
        level2 = df["appointment_type"].fillna("").str.strip().str.lower()
        agent_name = df["agent_full_name"].fillna("").str.strip().str.lower()

        is_producer = (level2 == "") | (level2 == agent_name)

        df["appointment_type"] = np.where(is_producer, "Producer", "Subproducer")

        if "parent_npn" in df.columns:
            df.loc[is_producer, "parent_npn"] = ""

        prod_count = is_producer.sum()
        sub_count = (~is_producer).sum()
        print(f"    🔧 UHC-ACA: Level 2 vs Agent Name -> {prod_count} Producers, {sub_count} Subproducers")
    else:
        df["appointment_type"] = "Producer"
        print(f"    🔧 UHC-ACA: Level 2 or Agent Name column missing - defaulting to Producer")

    return df


# ==========================================================
#  HANDLER MAP
# ==========================================================
def handle_ameritas_life(df, rule):
    """
    Ameritas Life: Writing numbers arrive with spaces around the dash
    (e.g. 'AG00136498- 01'). DB stores them mostly WITH suffix no spaces
    ('AG00271769-02'), some WITHOUT suffix ('AG00136498').

    Strip spaces only — keep the suffix. Identity resolution matches on
    the full value. Unmatched agents fall to NAME fallback which can
    catch the base-format contracts.
    """
    if "agent_writing_num" not in df.columns:
        return df

    before = df["agent_writing_num"].head(3).tolist()

    # Strip spaces only: 'AG00136498- 01' → 'AG00136498-01'
    df["agent_writing_num"] = (
        df["agent_writing_num"]
        .fillna("")
        .astype(str)
        .str.replace(" ", "", regex=False)
        .str.strip()
    )

    after = df["agent_writing_num"].head(3).tolist()
    print(f"    ✂️  Ameritas WR cleaned: {before} → {after}")
    return df


handler_map = {
    "handle_sma": handle_sma,
    "handle_allstate_aca": handle_allstate_aca,
    "handle_oscar_aca": handle_oscar_aca,
    "handle_aflac": handle_aflac,
    "handle_prominence": handle_prominence,
    "handle_uhc_aca": handle_uhc_aca,
    "handle_ameritas_life": handle_ameritas_life,
    "__default__": handle_default,
}


def get_handler(rule):
    """Get handler function for a carrier. Falls back to handle_default."""
    if rule.get("custom_logic_flag") != "Y":
        return handle_default
    module_name = str(rule.get("custom_module_name", "")).strip()
    handler = handler_map.get(module_name, handle_default)
    if handler == handle_default and module_name:
        print(f"    ⚠️  No handler for '{module_name}' - using default")
    return handler