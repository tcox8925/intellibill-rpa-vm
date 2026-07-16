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
    # RTS Benefit Year is mapped to current_rts_date (not next_rts); read it there.
    rts_year = df["current_rts_date"].fillna("").str.strip() if "current_rts_date" in df.columns else pd.Series([""] * len(df))

    is_completed = rts_status.str.lower() == "completed"

    df["current_rts"] = np.where(is_completed & (rts_year == current_year), "Yes", "")
    df["next_rts"] = np.where(is_completed & (rts_year == next_year), "Yes", "")
    df["current_rts_date"] = ""
    df["next_rts_date"] = ""

    print(f"    🔧 Prominence: conditional RTS ({is_completed.sum()} completed, year={current_year}/{next_year})")
    return df


def handle_uhc_aca(df, rule):
    """
    UHC-ACA guide logic:
      - Level 2 blank or equal to Agent Name -> Producer
      - Level 2 different from Agent Name -> Subproducer; Level 2 ID is parent contract
      - Appointed states come from the two-letter state columns where value = C
    """
    if "appointment_type" in df.columns and "agent_full_name" in df.columns:
        level2 = df["appointment_type"].fillna("").astype(str).str.strip().str.lower()
        agent_name = df["agent_full_name"].fillna("").astype(str).str.strip().str.lower()

        is_producer = (level2 == "") | (level2 == agent_name)

        df["appointment_type"] = np.where(is_producer, "Producer", "Subproducer")

        if "parent_npn" in df.columns:
            # Producers have no parent.
            df.loc[is_producer, "parent_npn"] = ""
            # Subproducers: per guide, Level 2 is the parent contract. The parent
            # appears in this same file as a Producer row whose Agent Name == Level 2,
            # carrying its own NIPRNUMBER, so Parent_Contract resolves to that NPN
            # (not the name). Build a name -> NPN map from the file and look up
            # Level 2. Unresolvable values (e.g. "UNKNOWN", or a parent not present
            # in the file) get a blank parent_npn rather than a parent exception.
            name_to_npn = {}
            for nm, npn in zip(agent_name, df["agent_npn"].fillna("").astype(str).str.strip()):
                if nm and npn and nm not in name_to_npn:
                    name_to_npn[nm] = npn
            sub_mask = ~is_producer
            df.loc[sub_mask, "parent_npn"] = level2[sub_mask].map(name_to_npn).fillna("")
            resolved_n = int((df.loc[sub_mask, "parent_npn"].astype(str).str.strip() != "").sum())
            print(f"    🔗 UHC-ACA: resolved parent NPN from file for {resolved_n}/{int(sub_mask.sum())} subproducers")

        prod_count = int(is_producer.sum())
        sub_count = int((~is_producer).sum())
        print(f"    🔧 UHC-ACA: Level 2 vs Agent Name -> {prod_count} Producers, {sub_count} Subproducers")
    else:
        df["appointment_type"] = "Producer"
        print(f"    🔧 UHC-ACA: Level 2 or Agent Name column missing - defaulting to Producer")

    state_codes = {
        "AL", "AZ", "CO", "FL", "GA", "IA", "IL", "IN", "KS", "LA", "MD", "MI",
        "MO", "MS", "NC", "NE", "NJ", "NM", "OH", "OK", "SC", "TN", "TX", "VA",
        "WA", "WI", "WY"
    }
    lookup = {str(c).strip().upper(): c for c in df.columns}
    present_state_cols = [(code, lookup[code]) for code in sorted(state_codes) if code in lookup]
    if present_state_cols:
        def _states_for_row(row):
            states = [code for code, col in present_state_cols
                      if str(row.get(col, "")).strip().upper() == "C"]
            return "; ".join(states)
        df["appointed_state"] = df.apply(_states_for_row, axis=1)
        print(f"    🔧 UHC-ACA: built appointed_state from {len(present_state_cols)} state credential columns")

    return df


def handle_centene_mdc(df, rule):
    """
    Shared guide logic for the Molina/Christus/Gold Kidney/SCAN/Wellcare/Zing-style
    ACA/MDC roster layout:
      - Broker Status Active/Certified -> Active
      - Status Reason pending values -> normalized pending statuses
      - State is retained only when State Status is Active/Certified
      - AEP Status Ready -> RTS Yes
    """
    # Standardize contract status from Broker Status.
    if "contract_status" in df.columns:
        status = df["contract_status"].fillna("").astype(str).str.strip()
        df["contract_status"] = np.where(
            status.str.lower().eq("active/certified"), "Active", status
        )

    # Use Status Reason for more helpful pending statuses where present.
    reason_col = next((c for c in df.columns if c.lower().strip() in {"status reason", "state status reason"}), None)
    if reason_col and "contract_status" in df.columns:
        reason = df[reason_col].fillna("").astype(str).str.lower()
        df.loc[reason.str.contains("pending principal|pending principal/contract", na=False), "contract_status"] = "Pending"
        df.loc[reason.str.contains("pending training|pending state license/training|certification", na=False), "contract_status"] = "Pending"

    # Retain appointed states only when the state-level status is active/certified.
    state_status_col = next((c for c in df.columns if c.lower().strip() == "state status"), None)
    if state_status_col and "appointed_state" in df.columns:
        active_state = df[state_status_col].fillna("").astype(str).str.strip().str.lower().eq("active/certified")
        df.loc[~active_state, "appointed_state"] = ""

    # AEP Status Ready means RTS checked.
    aep_col = next((c for c in df.columns if c.lower().strip() == "aep status"), None)
    if aep_col:
        ready = df[aep_col].fillna("").astype(str).str.strip().str.lower().eq("ready")
        df["current_rts"] = np.where(ready, "Yes", "")

    print(f"    🔧 Centene-style MDC/ACA guide logic applied")
    return df


def handle_sentara(df, rule):
    """Sentara guide: all producers; ignore rows where the agent name is Sentara-only."""
    if "agent_full_name" in df.columns:
        before = len(df)
        df = df[~df["agent_full_name"].fillna("").astype(str).str.contains("sentara only", case=False, na=False)].copy()
        if len(df) != before:
            print(f"    🔧 Sentara: filtered {before - len(df)} Sentara-only rows")
    df["appointment_type"] = "Producer"
    return df


def handle_physicians_mutual(df, rule):
    """
    Physicians Mutual SUP guide logic (Level 1 / Level 2 only; Level 3-4 ignored):
      One agent per file row — not both levels:
      - Level 2 Role present -> Subproducer only; NPN = L2 NPN or L1 fallback; parent = L1 Name
      - No Level 2 Role -> Producer only; NPN = L1 NPN (also writing number)
      - Appointment Yes -> Active
    """
    rows = []

    def _get(row, *names):
        low = {str(k).lower().strip(): k for k in row.index}
        for name in names:
            key = low.get(name.lower().strip())
            if key is not None:
                return row.get(key)
        return ""

    def _clean(v):
        return "" if pd.isna(v) or str(v).strip().lower() in {"", "nan", "none"} else str(v).strip()

    def _status(v):
        raw = _clean(v)
        return "Active" if raw.lower() in {"yes", "active", "true", "1"} else raw

    for _, row in df.iterrows():
        l1_name = _clean(_get(row, "Level 1 Name"))
        l1_npn = _clean(_get(row, "Level 1 NPN"))
        l2_role = _clean(_get(row, "Level 2 Role"))

        if l2_role:
            l2_npn = _clean(_get(row, "Level 2 NPN")) or l1_npn
            l2_name = _clean(_get(row, "Level 2 Name"))
            rows.append({
                "agent_full_name": l2_name,
                "agent_npn": l2_npn,
                "agent_writing_num": l2_npn,
                "contract_date": _get(row, "Level 2 Contract"),
                "appointed_state": _get(row, "Level 2 State"),
                "contract_status": _status(_get(row, "Level 2 Appointment")),
                "appointment_type": "Subproducer",
                "parent_npn": l1_name,
            })
        elif l1_name or l1_npn:
            rows.append({
                "agent_full_name": l1_name,
                "agent_npn": l1_npn,
                "agent_writing_num": l1_npn,
                "contract_date": _get(row, "Level 1 Contract"),
                "appointed_state": _get(row, "Level 1 State"),
                "contract_status": _status(_get(row, "Level 1 Appointment")),
                "appointment_type": "Producer",
                "parent_npn": "",
            })

    if rows:
        out = pd.DataFrame(rows).drop_duplicates()
        prod = int((out["appointment_type"] == "Producer").sum())
        sub = int((out["appointment_type"] == "Subproducer").sum())
        print(f"    🔧 Physicians Mutual: {len(df)} source rows -> {len(out)} contracts ({prod} producers, {sub} subproducers)")
        return out
    return df


def handle_ncd(df, rule):
    """
    NCD ACA guide logic:
      - agent_npn from Code / Code 2 (Code 2 when numeric; ignore "Individual")
      - contract_date from Active Date, fallback Created Date
      - agent_full_name from First + Last, or Agent Label when different
      - all rows are Producer; Parent ID ignored
    """
    def _col(*names):
        lookup = {str(c).lower().strip(): c for c in df.columns}
        for name in names:
            key = lookup.get(name.lower().strip())
            if key is not None:
                return df[key]
        return pd.Series([""] * len(df), index=df.index)

    def _clean(series):
        return series.fillna("").astype(str).str.strip()

    code = _clean(_col("code", "agent_npn"))
    code2 = _clean(_col("code 2"))
    is_agency_npn = code2.str.fullmatch(r"\d+").fillna(False)
    df["agent_npn"] = np.where(is_agency_npn, code2, code)

    active_date = _clean(_col("contract_date", "active date"))
    created_date = _clean(_col("created date"))
    df["contract_date"] = np.where(active_date != "", active_date, created_date)

    fname = _clean(_col("agent_fname", "first name"))
    lname = _clean(_col("agent_lname", "last name"))
    label = _clean(_col("agent_full_name", "agent label"))
    built_name = (fname + " " + lname).str.strip()
    df["agent_full_name"] = np.where(
        (label != "") & (label.str.lower() != built_name.str.lower()),
        label,
        built_name,
    )

    df["appointment_type"] = "Producer"
    df["parent_npn"] = ""

    print(f"    🔧 NCD: NPN/date/name logic applied ({len(df)} rows)")
    return df


# ==========================================================
#  HANDLER MAP
# ==========================================================
def handle_ameritas_life(df, rule):
    """
    Ameritas Life guide logic:
      - The writing number carries a 'situation' suffix (e.g. AG00261901-01 / -02).
        Strip spaces AND the situation suffix so the agent's BASE writing number is
        used for identity/dedup/matching (the file has no NPN). Without this the same
        agent appears once per situation, inflating the count and failing to match a
        contracts table keyed on the base number.
      - If Agent Comp PayableTo differs from Name, the row is a Subproducer and
        PayableTo is the parent contract name (resolved with parent_identity_field=NAME).
    """
    if "agent_writing_num" in df.columns:
        before = df["agent_writing_num"].head(3).tolist()
        # CRM stores the BASE writing number (no situation suffix): a matched row
        # in the contracts table is keyed on "AG00136498", not "AG00136498-01".
        # The file writes it with a stray space and a "-NN" situation suffix
        # ("AG00271769- 02"), so normalize the space AND strip the "-NN" suffix to
        # recover the base number used for identity matching.
        df["agent_writing_num"] = (
            df["agent_writing_num"].fillna("").astype(str)
            .str.replace(" ", "", regex=False)     # "AG00271769- 02" -> "AG00271769-02"
            .str.replace(r"-\d+$", "", regex=True)  # "AG00271769-02"  -> "AG00271769"
            .str.strip()
        )
        after = df["agent_writing_num"].head(3).tolist()
        print(f"    ✂️  Ameritas WR -> base number (suffix stripped): {before} → {after}")

    if "parent_npn" in df.columns and "agent_full_name" in df.columns:
        parent = df["parent_npn"].fillna("").astype(str).str.strip()
        name = df["agent_full_name"].fillna("").astype(str).str.strip()
        is_sub = (parent != "") & (parent.str.lower() != name.str.lower())
        df["appointment_type"] = np.where(is_sub, "Subproducer", "Producer")
        df.loc[~is_sub, "parent_npn"] = ""
        print(f"    🔧 Ameritas: {int(is_sub.sum())} subproducer row(s) identified from PayableTo")

    return df


# ──────────────────────────────────────────────────────────────────────────
#  Centene-style roster carriers — split out of the old shared handle_centene_mdc.
#  Each carrier maps Status Reason to its OWN target string per its guide, so
#  they get dedicated handlers. Only the genuinely identical sub-steps
#  (all-Producer, Active/Certified->Active, state-gating) are shared helpers.
# ──────────────────────────────────────────────────────────────────────────
def _set_all_producer(df):
    if "appointment_type" in df.columns:
        df["appointment_type"] = "Producer"
    return df


def _norm_active_certified(df):
    """Broker Status 'Active/Certified' -> 'Active'. Leaves every other status
    untouched so the per-carrier Status Reason logic can set it."""
    if "contract_status" in df.columns:
        s = df["contract_status"].fillna("").astype(str).str.strip()
        df["contract_status"] = np.where(s.str.lower().eq("active/certified"), "Active", s)
    return df


def _blank_inactive_states(df):
    """Guide rule shared by SCAN/Zing/Gold Kidney: 'add the appointed_state only
    if State Status = Active/Certified'. Blank the state when it is not active but
    KEEP the agent row (no drop), so an agent with no active state still surfaces."""
    ss_col = next((c for c in df.columns if c.lower().strip() == "state status"), None)
    if ss_col and "appointed_state" in df.columns:
        inactive = df[ss_col].fillna("").astype(str).str.strip().str.lower() != "active/certified"
        df.loc[inactive, "appointed_state"] = ""
    return df


def _reason_lower(df):
    col = next((c for c in df.columns if c.lower().strip() == "status reason"), None)
    if col is None:
        return None
    return df[col].fillna("").astype(str).str.strip().str.lower()


def _aep_ready_rts(df):
    """AEP Status 'Ready' -> current_rts check (Wellcare/Zing)."""
    aep = next((c for c in df.columns if c.lower().strip() == "aep status"), None)
    if aep:
        df["current_rts"] = np.where(
            df[aep].fillna("").astype(str).str.strip().str.lower().eq("ready"), "Yes", "")
    return df


def handle_wellcare(df, rule):
    """Wellcare guide: All Producer; Active/Certified -> Active; if Status Reason
    is Pending Principal -> contract_status = Pending; AEP Ready -> RTS check.
    (This file has no state column.)"""
    df = _norm_active_certified(df)
    reason = _reason_lower(df)
    if reason is not None:
        df.loc[reason.str.contains("pending principal", na=False), "contract_status"] = "Pending"
    df = _aep_ready_rts(df)
    df = _set_all_producer(df)
    print("    🔧 Wellcare: Active/Certified->Active, Pending Principal->Pending, AEP->RTS, all Producer")
    return df


def handle_scan(df, rule):
    """SCAN guide: All Producer; Active/Certified -> Active; by Status Reason:
    Pending Principal -> 'Request Sent to Carrier';
    Pending State License/Training and Pending Training -> 'Pending - Certification Required'.
    appointed_state only where State Status = Active/Certified."""
    df = _norm_active_certified(df)
    reason = _reason_lower(df)
    if reason is not None:
        df.loc[reason.str.contains("pending principal", na=False), "contract_status"] = "Request Sent to Carrier"
        df.loc[reason.str.contains("pending state license|pending training", na=False), "contract_status"] = "Pending - Certification Required"
    df = _blank_inactive_states(df)
    df = _set_all_producer(df)
    print("    🔧 SCAN: reason map (Request Sent to Carrier / Pending - Certification Required), states gated, all Producer")
    return df


def handle_zing(df, rule):
    """Zing guide (David): map the defined Status Reasons; drop the rest.
      - Broker Status Active/Certified              -> Active
      - Status Reason 'Pending Principal/Contract'  -> 'Pending - Agency Appointment'
      - Status Reason 'Pending Training'            -> 'Pending - Certification Required'
      - Status Reason 'Pending Contract'            -> 'Pending'
      - ANY other reason (e.g. 'Pending Training/Contract') is out of scope: the
        agent is dropped — not processed, no result, no exception.
    AEP Ready -> RTS check; appointed_state only where State Status = Active/Certified;
    all Producer."""
    df = _norm_active_certified(df)
    reason = _reason_lower(df)
    if reason is not None:
        r = reason.str.strip()
        is_active = df["contract_status"].fillna("").astype(str).str.strip().str.lower().eq("active")
        defined_ppc = r.eq("pending principal/contract")
        defined_train = r.eq("pending training")
        defined_contract = r.eq("pending contract")
        df.loc[defined_ppc, "contract_status"] = "Pending - Agency Appointment"
        df.loc[defined_train, "contract_status"] = "Pending - Certification Required"
        df.loc[defined_contract, "contract_status"] = "Pending"
        keep = is_active | defined_ppc | defined_train | defined_contract
        dropped = int((~keep).sum())
        df = df[keep].copy().reset_index(drop=True)
        if dropped:
            print(f"    🔧 Zing: dropped {dropped} row(s) — Status Reason not in defined scope (per David)")
    df = _aep_ready_rts(df)
    df = _blank_inactive_states(df)
    df = _set_all_producer(df)
    print("    🔧 Zing: reason map (Pending Principal/Contract→Agency Appointment, "
          "Pending Training→Certification Required, Pending Contract→Pending), out-of-scope dropped, "
          "AEP→RTS, states gated, all Producer")
    return df


def handle_gold_kidney(df, rule):
    """Gold Kidney guide: All Producer; NPN is also the writing number;
    Active/Certified -> Active (Status Reason ignored - no remap);
    appointed_state only where State Status = Active/Certified."""
    df = _norm_active_certified(df)
    if "agent_npn" in df.columns:
        df["agent_writing_num"] = df["agent_npn"]
    df = _blank_inactive_states(df)
    df = _set_all_producer(df)
    print("    🔧 Gold Kidney: Active/Certified->Active, NPN->writing_num, states gated, all Producer")
    return df


def handle_christus(df, rule):
    """Christus guide (applies to both ACA and MDC sub-carriers produced by
    read_christus, which already maps columns, splits LOB COMM/MA, and retains
    agents with no active state):
      - Broker Status: Active/Certified -> Active; Suspended -> 'Active - Recertification Needed'
      - MA (MDC) side only: an Active/Certified contract is Ready-To-Sell -> current_rts check
    appointed_state gating is handled by the matrix appointed_state_filter on the
    ACA row plus the reader's retention; status normalization lives here because
    there is no contract_status value-map column."""
    if "contract_status" in df.columns:
        s = df["contract_status"].fillna("").astype(str).str.strip()
        low = s.str.lower()
        out = s.mask(low.eq("active/certified"), "Active")
        out = out.mask(low.str.contains("suspended", na=False), "Active - Recertification Needed")
        df["contract_status"] = out

    is_mdc = "MDC" in str(rule.get("carrier_name", "")).upper()
    if is_mdc:
        if "current_rts" not in df.columns:
            df["current_rts"] = ""
        active = df["contract_status"].fillna("").astype(str).str.strip().str.lower().eq("active")
        df["current_rts"] = df["current_rts"].mask(active, "Yes")
    print(f"    🔧 Christus: Active/Certified->Active, Suspended->Active - Recertification Needed"
          + (", MA active->RTS" if is_mdc else ""))
    return df


def handle_verda(df, rule):
    """Verda guide: All Producer; contract_status comes from the 'Active' column
    (mapped via the load matrix); current_rts is derived from the Status column,
    gated on Certification Year — set RTS only when Status is Active AND the
    certification year is the current selling year ('only add active', 'check
    certification year column')."""
    year = str(datetime.today().year)
    status_col = next((c for c in df.columns if c.lower().strip() == "status"), None)
    cy_col = next((c for c in df.columns if c.lower().strip() == "certification year"), None)
    if "current_rts" not in df.columns:
        df["current_rts"] = ""
    if status_col is not None:
        st = df[status_col].fillna("").astype(str).str.strip().str.lower()
        cy = (df[cy_col].fillna("").astype(str).str.strip()
              if cy_col is not None else pd.Series([""] * len(df), index=df.index))
        df["current_rts"] = np.where(st.eq("active") & cy.eq(year), "Yes", "")
    if "appointment_type" in df.columns:
        df["appointment_type"] = "Producer"
    print(f"    🔧 Verda: current_rts from Status+Certification Year (Active & {year}), all Producer")
    return df


def handle_solis(df, rule):
    """Solis guide: All Producer; current_rts comes from the 'RTS <year>?' column
    ('Yes = <year>' ready-to-sell for the current selling year)."""
    year = str(datetime.today().year)
    rts_col = next((c for c in df.columns if "rts" in c.lower() and year in c), None)
    if rts_col is None:
        rts_col = next((c for c in df.columns if c.lower().strip().startswith("rts ")), None)
    if "current_rts" not in df.columns:
        df["current_rts"] = ""
    if rts_col is not None:
        df["current_rts"] = np.where(
            df[rts_col].fillna("").astype(str).str.strip().str.upper().eq("YES"), "Yes", "")
    if "appointment_type" in df.columns:
        df["appointment_type"] = "Producer"
    print(f"    🔧 Solis: current_rts from {rts_col!r} (Yes->RTS), all Producer")
    return df


# ──────────────────────────────────────────────────────────────────────────
#  Subproducer / parent-contract carriers. Each guide says "all Subproducer";
#  AmeriHealth/AVMED also name a parent contract that comes from the Group Name
#  column (Agility / Pandora), resolved downstream by NAME (matrix
#  parent_identity_field=NAME + load maps parent_npn<-Group Name).
# ──────────────────────────────────────────────────────────────────────────
def _set_all_subproducer(df):
    if "appointment_type" in df.columns:
        df["appointment_type"] = "Subproducer"
    return df


def _rts_from_benefit_year(df, by_names, date_names):
    """current_rts = Yes when the benefit-year column equals the current selling
    year; current_rts_date = the ready-to-sell date for those rows."""
    year = str(datetime.today().year)
    by = next((c for c in df.columns if c.lower().strip() in by_names), None)
    dt = next((c for c in df.columns if c.lower().strip() in date_names), None)
    for col in ("current_rts", "current_rts_date"):
        if col not in df.columns:
            df[col] = ""
    if by is not None:
        ready = df[by].fillna("").astype(str).str.strip().eq(year)
        df["current_rts"] = np.where(ready, "Yes", "")
        if dt is not None:
            df["current_rts_date"] = np.where(ready, df[dt].fillna("").astype(str).str.strip(), "")
    return df


def handle_amerihealth_aca(df, rule):
    """AmeriHealth ACA guide: all Subproducer; parent contract = Group Name
    (Agility), resolved by NAME via the matrix/load config."""
    df = _set_all_subproducer(df)
    print("    🔧 AmeriHealth ACA: all Subproducer (parent = Group Name / Agility)")
    return df


def handle_amerihealth_mdc(df, rule):
    """AmeriHealth MDC guide: all Subproducer; parent = Group Name (Agility);
    current_rts from Benefit Year (current selling year) + current_rts_date from
    Ready to Sell Date; agent_writing_num from Writing Code (load)."""
    df = _rts_from_benefit_year(df, {"benefit year"}, {"ready to sell date"})
    df = _set_all_subproducer(df)
    print("    🔧 AmeriHealth MDC: all Subproducer (parent = Group Name), RTS from Benefit Year")
    return df


def handle_avmed_mdc(df, rule):
    """AVMED MDC guide: all Subproducer; parent = Group Name (Pandora);
    Is Appointed '1' -> contract_status Active; current_rts from RTS Benefit Year +
    current_rts_date from RTS Date."""
    if "contract_status" in df.columns:
        df["contract_status"] = np.where(
            df["contract_status"].fillna("").astype(str).str.strip().eq("1"),
            "Active", df["contract_status"])
    df = _rts_from_benefit_year(df, {"rts benefit year"}, {"rts date"})
    df = _set_all_subproducer(df)
    print("    🔧 AVMED MDC: all Subproducer (parent = Group Name / Pandora), Is Appointed 1->Active, RTS from RTS Benefit Year")
    return df


def handle_cigna_aca(df, rule):
    """Cigna ACA guide: all Subproducer; Writing Agent Appointment Status active ->
    contract_status Active; appointed_state retained only where the appointment is
    active."""
    if "contract_status" in df.columns:
        cs = df["contract_status"].fillna("").astype(str).str.strip()
        active = cs.str.lower().eq("active")
        df["contract_status"] = np.where(active, "Active", cs)
        if "appointed_state" in df.columns:
            df.loc[~active, "appointed_state"] = ""
    df = _set_all_subproducer(df)
    print("    🔧 Cigna ACA: all Subproducer, appointment status active->Active, states gated")
    return df


def handle_caresource(df, rule):
    """Caresource guide: set all contract_status Active and appointment_type Producer;
    add appointed_state only where Active Appointment = Yes."""
    if "contract_status" in df.columns:
        df["contract_status"] = "Active"
    aa = next((c for c in df.columns if c.lower().strip() == "active appointment"), None)
    if aa is not None and "appointed_state" in df.columns:
        not_yes = df[aa].fillna("").astype(str).str.strip().str.lower().ne("yes")
        df.loc[not_yes, "appointed_state"] = ""
    if "appointment_type" in df.columns:
        df["appointment_type"] = "Producer"
    print("    🔧 Caresource: all Active + Producer, appointed_state only where Active Appointment=Yes")
    return df


def handle_healthfirst(df, rule):
    """Health First guide: Appointment Status -> contract_status:
    Appointed / Pending Enrollment -> Active; Terminated -> Terminated.
    (read_healthfirst already splits LOA Product into ACA/MDC and maps columns;
    the status map lives here because there is no contract_status value-map column.)"""
    if "contract_status" in df.columns:
        s = df["contract_status"].fillna("").astype(str).str.strip()
        low = s.str.lower()
        out = s.mask(low.isin(["appointed", "pending enrollment"]), "Active")
        out = out.mask(low.eq("terminated"), "Terminated")
        df["contract_status"] = out
    print("    🔧 Health First: Appointed/Pending Enrollment->Active, Terminated->Terminated")
    return df


def handle_medica(df, rule):
    """Medica guide: the appointments file is multi-carrier (MEDICA / BCBSNE /
    GIGCARE) under a 'Carrier Appointments List' title row (handled by
    ignore_header_rows=1). Keep only MEDICA rows; all Subproducer. Appointment
    Status ACTIVE -> contract_status Active (mapped column + STATUS filter)."""
    cc = next((c for c in df.columns if c.lower().strip() in ("carrier name", "carrier")), None)
    if cc is not None:
        before = len(df)
        df = df[df[cc].fillna("").astype(str).str.strip().str.upper().eq("MEDICA")].copy()
        print(f"    🔧 Medica: filtered {before} -> {len(df)} rows to MEDICA carrier")
    if "appointment_type" in df.columns:
        df["appointment_type"] = "Subproducer"
    return df


def handle_newera(df, rule):
    """New Era guide: Status 'A Active - writing number' -> contract_status Active;
    all Producer. (The STATUS filter already keeps only the 'A Active' rows; this
    normalizes the label so downstream active-detection works.)"""
    if "contract_status" in df.columns:
        cs = df["contract_status"].fillna("").astype(str).str.strip()
        df["contract_status"] = np.where(cs.str.lower().str.startswith("a active"), "Active", cs)
    if "appointment_type" in df.columns:
        df["appointment_type"] = "Producer"
    print("    🔧 New Era: 'A Active - writing number' -> Active, all Producer")
    return df


handler_map = {
    "handle_sma": handle_sma,
    "handle_wellcare": handle_wellcare,
    "handle_scan": handle_scan,
    "handle_zing": handle_zing,
    "handle_gold_kidney": handle_gold_kidney,
    "handle_christus": handle_christus,
    "handle_verda": handle_verda,
    "handle_solis": handle_solis,
    "handle_amerihealth_aca": handle_amerihealth_aca,
    "handle_amerihealth_mdc": handle_amerihealth_mdc,
    "handle_avmed_mdc": handle_avmed_mdc,
    "handle_cigna_aca": handle_cigna_aca,
    "handle_caresource": handle_caresource,
    "handle_healthfirst": handle_healthfirst,
    "handle_medica": handle_medica,
    "handle_newera": handle_newera,
    "handle_allstate_aca": handle_allstate_aca,
    "handle_oscar_aca": handle_oscar_aca,
    "handle_aflac": handle_aflac,
    "handle_prominence": handle_prominence,
    "handle_uhc_aca": handle_uhc_aca,
    "handle_ameritas_life": handle_ameritas_life,
    "handle_centene_mdc": handle_centene_mdc,
    "handle_sentara": handle_sentara,
    "handle_physicians_mutual": handle_physicians_mutual,
    "handle_ncd": handle_ncd,
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