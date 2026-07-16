import os
"""
PCH Claims Detail + RX + Summary — pch-db-dev001
--------------------------------------------------
Reads claims/roster/RX from pch-db-dev001, processes locally,
writes classified detail + RX + summary MV back to pch-db-dev001.

Usage:
  python pch_create_views.py 2026-02-01    # Feb snapshot (report_date <= Feb)
  python pch_create_views.py 2026-03-01    # March snapshot (all data)
  python pch_create_views.py               # Uses MAX(report_date)
"""

import sys
import pandas as pd
import numpy as np
import time
from psycopg2.extras import execute_values
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
import psycopg2

KEY_VAULT_NAME = os.getenv("KEY_VAULT_NAME", "")
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

TARGET_SERVER = os.getenv("PCH_DB_HOST", "")
DATABASE = os.getenv("PCH_DB_NAME", "")
DB_USER = os.getenv("PCH_DB_USER", "")

_cached_token = None


def get_token():
    global _cached_token
    if _cached_token is None:
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=f"https://{KEY_VAULT_NAME}.vault.azure.net/", credential=credential)
        client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
        client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
        tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value
        cred = ClientSecretCredential(tenant_id, client_id, client_secret)
        _cached_token = cred.get_token("https://ossrdbms-aad.database.windows.net/.default").token
    return _cached_token


def get_conn(server):
    conn = psycopg2.connect(
        host=server,
        dbname=DATABASE,
        user=DB_USER,
        password=get_token(),
        sslmode="require",
        options="-c statement_timeout=1800000",
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=5,
    )
    conn.autocommit = True
    return conn


def run_target(label, sql):
    print(f"  [{label}]...")
    t0 = time.time()
    conn = get_conn(TARGET_SERVER)
    cur = conn.cursor()
    cur.execute(sql)
    cur.close()
    conn.close()
    print(f"  ✅ {time.time()-t0:.1f}s\n")


# ─── Minimal columns we need ─────────────────────────────────────────────────

CLAIMS_COLS = """
    report_date,
    member_amisys_nbr,
    first_name,
    last_name,
    claim_nbr,
    serv_line,
    serv_seq,
    service_start_date_dim_ck,
    service_end_date_dim_ck,
    pcp_npi,
    pcp_inst_name,
    attending_npi,
    attending_inst_name,
    proc_code,
    rev_code,
    primary_diag_code,
    place_of_serv_code,
    place_of_serv_desc,
    treatment_type_desc,
    paid_amt,
    ibnr_amt,
    claim_paid_date_dim_ck
"""

ROSTER_COLS = "report_date, amisys_number, pcp_npi"


# ─── Processing ──────────────────────────────────────────────────────────────

def load_data(cutoff=None):
    # Read from TARGET — it has all data (Feb + March after sync)
    conn = get_conn(TARGET_SERVER)

    where = f"WHERE report_date::date <= '{cutoff}'::date" if cutoff else ""

    print(f"  Loading claims from {TARGET_SERVER}...")
    t0 = time.time()
    claims_df = pd.read_sql(f"SELECT {CLAIMS_COLS} FROM wpo.pch_med_claims {where}", conn)
    print(f"  ✅ {len(claims_df):,} rows in {time.time()-t0:.1f}s")

    print(f"  Loading roster from {TARGET_SERVER}...")
    t0 = time.time()
    roster_df = pd.read_sql(f"SELECT {ROSTER_COLS} FROM wpo.pch_member_roster WHERE pcp_npi IS NOT NULL", conn)
    print(f"  ✅ {len(roster_df):,} rows in {time.time()-t0:.1f}s")

    conn.close()
    return claims_df, roster_df


def deduplicate(claims_df):
    print("  Deduplicating...")
    t0 = time.time()

    claims_df["report_date"] = pd.to_datetime(claims_df["report_date"], errors="coerce")
    claims_df["service_start_date_dim_ck"] = pd.to_datetime(
        claims_df["service_start_date_dim_ck"], format="mixed", dayfirst=False, errors="coerce"
    )
    claims_df["member_amisys_nbr"] = claims_df["member_amisys_nbr"].astype(str).str.strip()
    claims_df["pcp_npi"] = claims_df["pcp_npi"].astype(str).str.strip()

    dedup_key = ["claim_nbr", "serv_line", "serv_seq"]
    claims_df = claims_df.sort_values(dedup_key + ["report_date"], ascending=[True, True, True, False])
    claims_df["_rank"] = claims_df.groupby(dedup_key, dropna=False).cumcount()
    deduped = claims_df[claims_df["_rank"] == 0].drop(columns=["_rank"]).copy()
    deduped = deduped[deduped["service_start_date_dim_ck"].notna()]
    deduped["dos_month"] = deduped["service_start_date_dim_ck"].dt.to_period("M").astype(str)

    print(f"  ✅ {len(claims_df):,} → {len(deduped):,} in {time.time()-t0:.1f}s")
    return deduped


def build_lookups(roster_df):
    print("  Building lookups...")
    t0 = time.time()

    roster_df["report_date"] = pd.to_datetime(roster_df["report_date"])
    roster_df["report_month"] = roster_df["report_date"].dt.to_period("M").astype(str)
    roster_df["pcp_npi"] = roster_df["pcp_npi"].astype(str).str.strip()
    roster_df["amisys_number"] = roster_df["amisys_number"].astype(str).str.strip()

    lookups = {
        "mm": set(zip(roster_df["amisys_number"], roster_df["report_month"])),
        "pm": set(zip(roster_df["pcp_npi"], roster_df["report_month"])),
        "mpt": set(zip(roster_df["amisys_number"], roster_df["report_month"], roster_df["pcp_npi"])),
        "all_mem": set(roster_df["amisys_number"].unique()),
        "all_prov": set(roster_df["pcp_npi"].unique()),
        "rm": set(roster_df["report_month"].unique()),
        "pa": roster_df.groupby("pcp_npi")["report_date"].min().to_dict(),
    }
    print(f"  ✅ {time.time()-t0:.1f}s")
    return lookups


def classify(df, dos_month, lookups, data_until):
    n = len(df)
    m = df["member_amisys_nbr"].values
    p = df["pcp_npi"].values
    dos = pd.to_datetime(df["service_start_date_dim_ck"], errors="coerce")

    me = np.array([x in lookups["all_mem"] for x in m])
    pe = np.array([x in lookups["all_prov"] for x in p])

    rm = dos_month if dos_month in lookups["rm"] else None

    if rm:
        mr = np.array([(x, rm) in lookups["mm"] for x in m])
        pr = np.array([(x, rm) in lookups["pm"] for x in p])
        asg = np.array([(x, rm, y) in lookups["mpt"] for x, y in zip(m, p)])
    else:
        mr = pr = asg = np.zeros(n, dtype=bool)

    if rm is None:
        df["classification"] = "No Roster Available — Cannot Determine"
        df["favorability"] = "N/A — No Roster"
    else:
        conds = [~me & ~pe, ~me, ~pe, ~mr & ~pr, ~mr, ~pr, asg]
        df["classification"] = np.select(conds, [
            "Never on Roster (Member & Provider)", "Never on Roster (Member)",
            "Never on Roster (Provider)", "Not Assigned at DOS (Member & Provider)",
            "Not Assigned at DOS (Member)", "Assigned at DOS, Provider Not on Roster",
            "Assigned at DOS",
        ], default="Member Assigned, Provider Unassigned")
        df["favorability"] = np.select(conds, [
            "Favorable - High", "Favorable - High", "Favorable - High",
            "Favorable - High", "Favorable - High", "Favorable - Low", "Unfavorable",
        ], default="Unfavorable")

    ad = pd.Series(p).map(lookups["pa"])
    ha = ad.notna().values
    hd = dos.notna().values
    hb = ha & hd
    ge = np.zeros(n, dtype=bool)
    if hb.any():
        ge[hb] = dos.values[hb] >= pd.to_datetime(ad[hb]).values

    df["attribution_timing"] = np.select(
        [~ha, ha & ~hd, hb & ge, hb & ~ge],
        ["Provider not in assignment records", "DOS unknown",
         "DOS on/after assignment", "DOS before assignment"],
        default="N/A")
    df["attribution_impact"] = np.select(
        [hb & ge, hb & ~ge],
        ["Unfavorable — spend counts against us",
         "Favorable — spend outside our window"],
        default="N/A")

    df["provider_assignment_date"] = ad.values
    df["roster_exists"] = rm is not None
    df["member_ever_on_roster"] = me
    df["member_on_roster_at_dos"] = mr
    df["provider_ever_on_roster"] = pe
    df["provider_on_roster_at_dos"] = pr
    df["member_assigned_to_provider"] = asg
    df["created_date"] = pd.Timestamp.now()
    df["data_until"] = data_until
    df["dos_month_out"] = dos_month
    df["entity_id"] = "931524614"
    df["sub_entity_id"] = "931524614002"
    df["carrier_id"] = "2931751000020024159"

    out = df[[
        "dos_month_out", "member_amisys_nbr", "first_name", "last_name",
        "claim_nbr", "serv_line", "serv_seq",
        "service_start_date_dim_ck", "service_end_date_dim_ck",
        "pcp_npi", "pcp_inst_name", "attending_npi", "attending_inst_name",
        "proc_code", "rev_code", "primary_diag_code",
        "place_of_serv_code", "place_of_serv_desc", "treatment_type_desc",
        "paid_amt", "ibnr_amt", "report_date", "claim_paid_date_dim_ck",
        "provider_assignment_date",
        "roster_exists", "member_ever_on_roster", "member_on_roster_at_dos",
        "provider_ever_on_roster", "provider_on_roster_at_dos", "member_assigned_to_provider",
        "classification", "favorability", "attribution_timing", "attribution_impact",
        "created_date", "data_until",
        "entity_id", "sub_entity_id", "carrier_id",
    ]].copy()

    out = out.where(out.notna(), None)

    def clean_val(v):
        if v is None:
            return None
        if isinstance(v, float) and np.isnan(v):
            return None
        if isinstance(v, pd.Timestamp) and pd.isna(v):
            return None
        if str(v) == 'NaT':
            return None
        return v

    return [tuple(clean_val(v) for v in row) for row in out.values]


INSERT_SQL = """
INSERT INTO wpo.pch_claims_detail (
    dos_month, member_amisys_nbr, first_name, last_name,
    claim_nbr, serv_line, serv_seq,
    service_start_date_dim_ck, service_end_date_dim_ck,
    pcp_npi, pcp_inst_name, attending_npi, attending_inst_name,
    proc_code, rev_code, primary_diag_code,
    place_of_serv_code, place_of_serv_desc, treatment_type_desc,
    paid_amt, ibnr_amt, report_date, claim_paid_date_dim_ck,
    provider_assignment_date,
    roster_exists, member_ever_on_roster, member_on_roster_at_dos,
    provider_ever_on_roster, provider_on_roster_at_dos, member_assigned_to_provider,
    classification, favorability, attribution_timing, attribution_impact,
    created_date, data_until,
    entity_id, sub_entity_id, carrier_id
) VALUES %s
"""


def main():
    cutoff = sys.argv[1] if len(sys.argv) > 1 else None

    print("=" * 60)
    print(f"  Server: {TARGET_SERVER}")
    print(f"  Cutoff: {cutoff or 'all data'}")
    print("=" * 60)

    # ── Check target writable ──
    conn = get_conn(TARGET_SERVER)
    cur = conn.cursor()
    cur.execute("SELECT pg_is_in_recovery();")
    if cur.fetchone()[0]:
        print("\n❌ Target is in recovery."); return
    cur.execute("SHOW default_transaction_read_only;")
    if cur.fetchone()[0] == 'on':
        print("\n❌ Target is read-only."); return
    cur.close(); conn.close()
    print("  ✅ Target is writable\n")

    # ── Create table ──
    run_target("Create detail table", """
CREATE TABLE IF NOT EXISTS wpo.pch_claims_detail (
    dos_month TEXT, member_amisys_nbr TEXT, first_name TEXT, last_name TEXT,
    claim_nbr TEXT, serv_line TEXT, serv_seq TEXT,
    service_start_date_dim_ck TIMESTAMP, service_end_date_dim_ck TIMESTAMP,
    pcp_npi TEXT, pcp_inst_name TEXT, attending_npi TEXT, attending_inst_name TEXT,
    proc_code TEXT, rev_code TEXT, primary_diag_code TEXT,
    place_of_serv_code TEXT, place_of_serv_desc TEXT, treatment_type_desc TEXT,
    paid_amt NUMERIC(18,2), ibnr_amt NUMERIC(18,2),
    report_date TEXT, claim_paid_date_dim_ck TEXT,
    provider_assignment_date DATE,
    roster_exists BOOLEAN, member_ever_on_roster BOOLEAN, member_on_roster_at_dos BOOLEAN,
    provider_ever_on_roster BOOLEAN, provider_on_roster_at_dos BOOLEAN, member_assigned_to_provider BOOLEAN,
    classification TEXT, favorability TEXT, attribution_timing TEXT, attribution_impact TEXT,
    created_date TIMESTAMP, data_until DATE,
    entity_id TEXT DEFAULT '931524614',
    sub_entity_id TEXT DEFAULT '931524614002',
    carrier_id TEXT DEFAULT '2931751000020024159'
);
""")

    # ── Get data_until ──
    if cutoff:
        data_until = cutoff
    else:
        conn = get_conn(TARGET_SERVER)
        cur = conn.cursor()
        cur.execute("SELECT MAX(report_date::date) FROM wpo.pch_med_claims;")
        data_until = str(cur.fetchone()[0])
        cur.close(); conn.close()
    print(f"  data_until: {data_until}\n")

    # ── Check existing ──
    conn = get_conn(TARGET_SERVER)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM wpo.pch_claims_detail WHERE data_until = %s", (data_until,))
    if cur.fetchone()[0] > 0:
        cur.close(); conn.close()
        print(f"  ⏭️  Snapshot data_until={data_until} exists. Skipping to summary.\n")
    else:
        cur.close(); conn.close()

        # ── Load from source ──
        print("Loading from source...")
        claims_df, roster_df = load_data(cutoff)

        # ── Process locally ──
        deduped = deduplicate(claims_df)
        del claims_df

        lookups = build_lookups(roster_df)
        del roster_df

        dos_months = sorted(deduped["dos_month"].dropna().unique())
        print(f"\n  {len(dos_months)} DOS months: {dos_months}\n")

        # ── Classify + insert per DOS month ──
        for dm in dos_months:
            t0 = time.time()
            chunk = deduped[deduped["dos_month"] == dm].copy()
            print(f"  [DOS {dm}] {len(chunk):,} rows...")

            rows = classify(chunk, dm, lookups, data_until)
            t1 = time.time()

            conn = get_conn(TARGET_SERVER)
            cur = conn.cursor()
            for i in range(0, len(rows), 5000):
                execute_values(cur, INSERT_SQL, rows[i:i+5000], page_size=5000)
            cur.close(); conn.close()

            print(f"  ✅ classify={t1-t0:.1f}s | insert={time.time()-t1:.1f}s | total={time.time()-t0:.1f}s\n")

        # ── Indexes ──
        run_target("Index PK", "CREATE INDEX IF NOT EXISTS idx_pch_detail_pk ON wpo.pch_claims_detail (claim_nbr, serv_line, serv_seq, data_until);")
        run_target("Index DOS", "CREATE INDEX IF NOT EXISTS idx_pch_detail_dos ON wpo.pch_claims_detail (dos_month, data_until);")
        run_target("Index CLS", "CREATE INDEX IF NOT EXISTS idx_pch_detail_cls ON wpo.pch_claims_detail (dos_month, classification, data_until);")
        run_target("Index DU", "CREATE INDEX IF NOT EXISTS idx_pch_detail_du ON wpo.pch_claims_detail (data_until);")

    # ── RX Claims by Classification (always runs) ──
    print("  Loading RX claims...")
    t0 = time.time()
    conn = get_conn(TARGET_SERVER)

    # Use matching report_date for RX
    cur = conn.cursor()
    cur.execute("SELECT MAX(report_date::date) FROM wpo.pch_rx_claim WHERE report_date::date <= %s", (data_until,))
    rx_report_date = str(cur.fetchone()[0])
    cur.close()
    print(f"  RX report_date: {rx_report_date}")

    rx_df = pd.read_sql(f"""
        SELECT member_amisys_nbr, first_name, last_name, year_mo,
               prescription_nbr, drug_desc, fill_date_dim_ck, prescription_date_dim_ck,
               national_drug_code_nbr, drug_type_desc, brand_generic_ind,
               paid_amt, ingredient_cost_amt, dispensed_fee_amt,
               dispensed_quantity, days_supply, refill_nbr, refills_authorized,
               pcp_npi, pcp_inst_name,
               prescribing_npi, prescribing_institution_name,
               prescribing_prov_prac_first_name, prescribing_prov_prac_last_name,
               pharmacy_nbr, pharmacy,
               report_date
        FROM wpo.pch_rx_claim
        WHERE report_date = '{rx_report_date}'
    """, conn)
    conn.close()
    rx_df["member_amisys_nbr"] = rx_df["member_amisys_nbr"].astype(str).str.strip()
    rx_df["year_mo"] = rx_df["year_mo"].astype(str).str.strip()
    rx_df["paid_amt"] = pd.to_numeric(rx_df["paid_amt"], errors="coerce").fillna(0)

    # Dedup: member + prescription + drug + fill_date + paid_amt
    before_dedup = len(rx_df)
    rx_df = rx_df.drop_duplicates(subset=[
        "member_amisys_nbr", "prescription_nbr", "drug_desc", "fill_date_dim_ck", "paid_amt"
    ])
    print(f"  RX dedup: {before_dedup:,} → {len(rx_df):,} ({before_dedup - len(rx_df)} dupes removed)")

    rx_df = rx_df[rx_df["paid_amt"] != 0].copy()
    print(f"  ✅ {len(rx_df):,} RX rows (non-zero) in {time.time()-t0:.1f}s\n")

    # Get member classifications from detail
    print("  Computing RX per classification...")
    conn = get_conn(TARGET_SERVER)
    detail_members = pd.read_sql(f"""
        SELECT dos_month, classification, favorability, member_amisys_nbr
        FROM wpo.pch_claims_detail
        WHERE data_until = '{data_until}'
    """, conn)
    conn.close()
    detail_members["member_amisys_nbr"] = detail_members["member_amisys_nbr"].astype(str).str.strip()

    dos_months = sorted(detail_members["dos_month"].dropna().unique())

    # Classify each RX claim via merge
    print("  Classifying RX claims...")
    t0 = time.time()

    # Build lookup: one classification per (dos_month, member) — keep worst favorability
    fav_order = {"Unfavorable": 0, "Favorable - Low": 1, "Favorable - High": 2, "N/A — No Roster": 3}
    detail_members["_fav_rank"] = detail_members["favorability"].map(fav_order).fillna(9)
    detail_members = detail_members.sort_values("_fav_rank")
    member_cls = detail_members.drop_duplicates(subset=["dos_month", "member_amisys_nbr"], keep="first")[
        ["dos_month", "member_amisys_nbr", "classification", "favorability"]
    ]

    # Add dos_month to RX (year_mo "202501" → "2025-01")
    rx_df["dos_month"] = rx_df["year_mo"].apply(lambda x: f"{x[:4]}-{x[4:]}" if len(x) == 6 else None)

    # Merge
    rx_df = rx_df.merge(member_cls, on=["dos_month", "member_amisys_nbr"], how="left")
    rx_df["classification"] = rx_df["classification"].fillna("Not in Medical Claims (RX Only)")
    rx_df["favorability"] = rx_df["favorability"].fillna("N/A")

    rx_df["data_until"] = data_until
    rx_df["created_date"] = pd.Timestamp.now()
    rx_df["entity_id"] = "931524614"
    rx_df["sub_entity_id"] = "931524614002"
    rx_df["carrier_id"] = "2931751000020024159"
    print(f"  ✅ Classified {len(rx_df):,} rows in {time.time()-t0:.1f}s\n")

    # ── Create RX detail table ──
    run_target("Create RX detail table", """
CREATE TABLE IF NOT EXISTS wpo.pch_rx_claims_detail (
    data_until DATE,
    dos_month TEXT,
    member_amisys_nbr TEXT,
    first_name TEXT,
    last_name TEXT,
    prescription_nbr TEXT,
    drug_desc TEXT,
    fill_date_dim_ck TEXT,
    prescription_date_dim_ck TEXT,
    national_drug_code_nbr TEXT,
    drug_type_desc TEXT,
    brand_generic_ind TEXT,
    paid_amt NUMERIC(18,2),
    ingredient_cost_amt NUMERIC(18,2),
    dispensed_fee_amt NUMERIC(18,2),
    dispensed_quantity TEXT,
    days_supply TEXT,
    refill_nbr TEXT,
    refills_authorized TEXT,
    pcp_npi TEXT,
    pcp_inst_name TEXT,
    prescribing_npi TEXT,
    prescribing_institution_name TEXT,
    prescribing_prov_prac_first_name TEXT,
    prescribing_prov_prac_last_name TEXT,
    pharmacy_nbr TEXT,
    pharmacy TEXT,
    year_mo TEXT,
    report_date TEXT,
    classification TEXT,
    favorability TEXT,
    created_date TIMESTAMP,
    entity_id TEXT DEFAULT '931524614',
    sub_entity_id TEXT DEFAULT '931524614002',
    carrier_id TEXT DEFAULT '2931751000020024159'
);
""")

    # Delete existing for this data_until
    conn = get_conn(TARGET_SERVER)
    cur = conn.cursor()
    cur.execute("DELETE FROM wpo.pch_rx_claims_detail WHERE data_until = %s", (data_until,))
    cur.close(); conn.close()

    # Insert RX detail
    rx_out = rx_df[[
        "dos_month", "member_amisys_nbr", "first_name", "last_name",
        "prescription_nbr", "drug_desc", "fill_date_dim_ck", "prescription_date_dim_ck",
        "national_drug_code_nbr", "drug_type_desc", "brand_generic_ind",
        "paid_amt", "ingredient_cost_amt", "dispensed_fee_amt",
        "dispensed_quantity", "days_supply", "refill_nbr", "refills_authorized",
        "pcp_npi", "pcp_inst_name",
        "prescribing_npi", "prescribing_institution_name",
        "prescribing_prov_prac_first_name", "prescribing_prov_prac_last_name",
        "pharmacy_nbr", "pharmacy",
        "year_mo", "report_date",
        "classification", "favorability",
        "created_date", "entity_id", "sub_entity_id", "carrier_id",
    ]].copy()
    rx_out["data_until"] = data_until
    rx_out = rx_out.where(rx_out.notna(), None)
    rx_tuples = [tuple(r) for r in rx_out.values]

    RX_DETAIL_INSERT = """
INSERT INTO wpo.pch_rx_claims_detail (
    dos_month, member_amisys_nbr, first_name, last_name,
    prescription_nbr, drug_desc, fill_date_dim_ck, prescription_date_dim_ck,
    national_drug_code_nbr, drug_type_desc, brand_generic_ind,
    paid_amt, ingredient_cost_amt, dispensed_fee_amt,
    dispensed_quantity, days_supply, refill_nbr, refills_authorized,
    pcp_npi, pcp_inst_name,
    prescribing_npi, prescribing_institution_name,
    prescribing_prov_prac_first_name, prescribing_prov_prac_last_name,
    pharmacy_nbr, pharmacy,
    year_mo, report_date,
    classification, favorability,
    created_date, entity_id, sub_entity_id, carrier_id,
    data_until
) VALUES %s
"""
    print(f"  Inserting {len(rx_tuples):,} RX detail rows...")
    conn = get_conn(TARGET_SERVER)
    cur = conn.cursor()
    for i in range(0, len(rx_tuples), 5000):
        execute_values(cur, RX_DETAIL_INSERT, rx_tuples[i:i+5000], page_size=5000)
    cur.close(); conn.close()
    print(f"  ✅ {len(rx_tuples):,} RX detail rows inserted\n")

    run_target("Index RX detail", "CREATE INDEX IF NOT EXISTS idx_rx_det_du ON wpo.pch_rx_claims_detail (data_until);")
    run_target("Index RX detail dos", "CREATE INDEX IF NOT EXISTS idx_rx_det_dos ON wpo.pch_rx_claims_detail (dos_month, data_until);")
    run_target("Index RX detail cls", "CREATE INDEX IF NOT EXISTS idx_rx_det_cls ON wpo.pch_rx_claims_detail (dos_month, classification, data_until);")
    run_target("Index RX detail member", "CREATE INDEX IF NOT EXISTS idx_rx_det_mem ON wpo.pch_rx_claims_detail (member_amisys_nbr, data_until);")

    # ── Summary MV on target ──
    run_target("Drop summary", "DROP MATERIALIZED VIEW IF EXISTS wpo.mv_pch_claims_summary CASCADE;")
    run_target("Create summary", """
CREATE MATERIALIZED VIEW wpo.mv_pch_claims_summary AS
WITH
detail AS (SELECT * FROM wpo.pch_claims_detail),
mcls AS (
    SELECT data_until, dos_month, member_amisys_nbr, COUNT(DISTINCT classification) AS cc
    FROM detail GROUP BY data_until, dos_month, member_amisys_nbr
),
ov AS (SELECT data_until, dos_month, member_amisys_nbr FROM mcls WHERE cc > 1),
med_summary AS (
    SELECT
        d.data_until, d.dos_month, d.classification, d.favorability,
        d.entity_id, d.sub_entity_id, d.carrier_id,
        COUNT(DISTINCT d.member_amisys_nbr) AS med_member_count,
        COUNT(DISTINCT CASE WHEN ov.member_amisys_nbr IS NOT NULL THEN d.member_amisys_nbr END) AS overlapping_members,
        COUNT(*) AS claim_count,
        COALESCE(SUM(d.paid_amt), 0)::numeric(18,2) AS paid_amt,
        COALESCE(SUM(d.ibnr_amt), 0)::numeric(18,2) AS ibnr_amt,
        COALESCE(SUM(d.paid_amt + d.ibnr_amt), 0)::numeric(18,2) AS total_incurred,
        MAX(d.created_date) AS created_date
    FROM detail d
    LEFT JOIN ov ON ov.data_until = d.data_until AND ov.dos_month = d.dos_month AND ov.member_amisys_nbr = d.member_amisys_nbr
    GROUP BY d.data_until, d.dos_month, d.classification, d.favorability, d.entity_id, d.sub_entity_id, d.carrier_id
),
rx_summary AS (
    SELECT
        data_until, dos_month, classification,
        COUNT(DISTINCT member_amisys_nbr) AS rx_members_found,
        COUNT(*) AS rx_claim_count,
        COALESCE(SUM(paid_amt), 0)::numeric(18,2) AS rx_paid
    FROM wpo.pch_rx_claims_detail
    WHERE classification != 'Not in Medical Claims (RX Only)'
    GROUP BY data_until, dos_month, classification
),
rx_only_summary AS (
    SELECT
        data_until, dos_month,
        COUNT(DISTINCT member_amisys_nbr) AS rx_only_members,
        COUNT(*) AS rx_only_claims,
        COALESCE(SUM(paid_amt), 0)::numeric(18,2) AS rx_only_paid
    FROM wpo.pch_rx_claims_detail
    WHERE classification = 'Not in Medical Claims (RX Only)'
    GROUP BY data_until, dos_month
),
rx_only_target AS (
    SELECT DISTINCT ON (data_until, dos_month)
        data_until, dos_month, classification
    FROM (SELECT DISTINCT data_until, dos_month, classification FROM detail) sub
    ORDER BY data_until, dos_month,
        CASE
            WHEN classification LIKE 'No Roster Available%' THEN 0
            WHEN classification = 'Never on Roster (Member)' THEN 1
            WHEN classification LIKE 'Never on Roster%' THEN 2
            ELSE 99
        END
),
rx_only_assigned AS (
    SELECT ro.*, rt.classification
    FROM rx_only_summary ro
    JOIN rx_only_target rt ON rt.data_until = ro.data_until AND rt.dos_month = ro.dos_month
)
SELECT
    m.data_until, m.dos_month, m.classification, m.favorability,
    m.med_member_count, m.overlapping_members,
    m.claim_count, m.paid_amt, m.ibnr_amt, m.total_incurred,
    COALESCE(r.rx_members_found, 0) AS rx_members_found,
    (m.med_member_count - COALESCE(r.rx_members_found, 0)) AS rx_members_not_found,
    COALESCE(r.rx_claim_count, 0) AS rx_claim_count,
    COALESCE(r.rx_paid, 0)::numeric(18,2) AS rx_paid,
    COALESCE(ro.rx_only_members, 0) AS rx_only_members,
    COALESCE(ro.rx_only_claims, 0) AS rx_only_claims,
    COALESCE(ro.rx_only_paid, 0)::numeric(18,2) AS rx_only_paid,
    m.created_date,
    m.entity_id, m.sub_entity_id, m.carrier_id
FROM med_summary m
LEFT JOIN rx_summary r
    ON r.data_until = m.data_until AND r.dos_month = m.dos_month AND r.classification = m.classification
LEFT JOIN rx_only_assigned ro
    ON ro.data_until = m.data_until AND ro.dos_month = m.dos_month AND ro.classification = m.classification
ORDER BY m.data_until, m.dos_month,
    CASE m.favorability WHEN 'Favorable - High' THEN 1 WHEN 'Favorable - Low' THEN 2 WHEN 'Unfavorable' THEN 3 ELSE 4 END;
""")
    run_target("Index summary", "CREATE UNIQUE INDEX idx_mv_summary_pk ON wpo.mv_pch_claims_summary (data_until, dos_month, classification);")

    # ── Verify ──
    print("Verifying...")
    conn = get_conn(TARGET_SERVER)
    cur = conn.cursor()
    cur.execute("SELECT data_until, COUNT(*) FROM wpo.pch_claims_detail GROUP BY data_until ORDER BY data_until;")
    for row in cur.fetchall():
        print(f"  Detail: data_until={row[0]} → {row[1]:,} rows")
    cur.execute("SELECT data_until, COUNT(*) FROM wpo.mv_pch_claims_summary GROUP BY data_until ORDER BY data_until;")
    for row in cur.fetchall():
        print(f"  Summary: data_until={row[0]} → {row[1]:,} rows")
    cur.close(); conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()