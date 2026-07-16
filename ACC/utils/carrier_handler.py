"""
Carrier handler module
----------------------
Each handler focuses purely on carrier-specific logic.

Now includes:
    • Global rule enforcement (missing NPN/email → Needs Attention)
    • CareSource & Priority Health handler workflows
    • No EOD logic (handled in runner_acc)
"""
import numpy
import time

import pandas as pd
from datetime import datetime
from utils import config
from utils.db_utils import update_queue_where, get_postgres_connection, get_contracts, get_agents, fetch_responsible_agent
import utils.zoho_utils as zoho_utils
from utils.file_utils import validate_priority_docs
from utils.queue_utils import fetch_queue, deduplicate_contracts
from utils.db_insertor import insert_queue_records, sanitize_sql_param
from utils.logger_utils import log_phase_start, log_phase_success, safe_log,init_log_entry
from utils.handle_ops_fail import handle_ops_fail
from utils.runner_utils import now_cst
import os



def _summarize_queue(carrier_id: str, carrier_name: str, carrier_row: pd.Series) -> dict:
    """
    Returns summary stats for a given carrier_id to feed into summarize_run().
    Includes total contracts, success, needs attention, and fail counts.
    """
    try:
        q = fetch_queue(carrier_id=carrier_id)
        if q.empty:
            return {
                "carrier": carrier_name,
                "carrier_id": carrier_id,
                "total_contracts": 0,
                "success_count": 0,
                "needs_attention": 0,
                "fail_count": 0,
                "crm_success_status": carrier_row.get("crm_success_status")
            }

        crm_success_status = str(carrier_row.get("crm_success_status") or "Success").lower()

        total_contracts = len(q)
        success_count = len(q[q["contract_status"].astype(str).str.lower() == crm_success_status])
        needs_attention = len(q[q["contract_status"].astype(str).str.lower() == "needs attention"])
        fail_count = len(q[q["status"].astype(str).str.lower() == "fail"])

        return {
            "carrier": carrier_name,
            "carrier_id": carrier_id,
            "total_contracts": total_contracts,
            "success_count": success_count,
            "needs_attention": needs_attention,
            "fail_count": fail_count,
            "crm_success_status": carrier_row.get("crm_success_status")
        }

    except Exception as e:
        safe_log("SUMMARY_CALC", f"Summary generation failed for {carrier_name}: {e}")
        return {
            "carrier": carrier_name,
            "carrier_id": carrier_id,
            "total_contracts": 0,
            "success_count": 0,
            "needs_attention": 0,
            "fail_count": 0,
            "crm_success_status": carrier_row.get("crm_success_status")
        }


def _order_principal_below_agency(df: pd.DataFrame) -> pd.DataFrame:
    """
    Correct ordering for CareSource:
      Agency → its Principals → (later) Agents

    Removes orphan principals (i.e., principals whose agency did not survive
    filtering or global rules). Agents remain untouched.
    """
    df = df.copy()

    # Normalize
    df["type_norm"] = df["agent_type"].astype(str).str.upper()
    df["txn_str"] = df["txn_id"].astype(str)

    # 1️⃣ Identify agencies that remain in df after filtering
    valid_agency_txns = set(
        df[df["type_norm"] == "AGENCY"]["txn_str"]
    )

    # 2️⃣ Remove orphan principals
    df = df[
        ~(
            (df["type_norm"] == "PRINCIPAL") &
            (~df["txn_str"].isin(valid_agency_txns))
        )
    ].copy()

    # 3️⃣ Build ordered list: Agency → its Principals → Agents
    ordered_rows = []

    # Handle agencies + their principals
    for _, agency in df[df["type_norm"] == "AGENCY"].iterrows():
        agency_txn = agency["txn_str"]

        # Add agency first
        ordered_rows.append(agency)

        # Add its principals
        principals = df[
            (df["type_norm"] == "PRINCIPAL") &
            (df["txn_str"] == agency_txn)
        ]
        for _, p in principals.iterrows():
            ordered_rows.append(p)

    # 4️⃣ Add agents last (unaltered)
    agents = df[df["type_norm"] == "AGENT"]
    for _, a in agents.iterrows():
        ordered_rows.append(a)

    # Cleanup
    out = pd.DataFrame(ordered_rows)
    return out.drop(columns=["type_norm", "txn_str"], errors="ignore") \
              .reset_index(drop=True)


def _apply_global_rules(carrier_id: str):
    """
    Marks any record in queue that violates global rules (like missing NPN/Email)
    as Needs Attention (soft fail).
    """
    try:
        conn = get_postgres_connection()
        cur = conn.cursor()
        cur.execute("""
            UPDATE wpo.ops_acc_process_queue
               SET contract_status = 'Needs Attention',
                   status = 'Success',
                   updated_on = now() at time zone 'utc',
                   crm_note = 'RPA: Contract is missing either an NPN or an Email.'
             WHERE carrier_id = %s
               AND status IN ('Pending','Processing')
               AND (
                   npn IS NULL OR TRIM(BOTH npn) = '' OR
                   email IS NULL OR TRIM(BOTH email) = ''
               )
        """, (carrier_id,))
        affected = cur.rowcount or 0
        conn.commit()
        conn.close()
        if affected:
            print(f"⚠️ Global rule enforcement: {affected} record(s) flagged (missing NPN/Email).")
    except Exception as e:
        safe_log("GLOBAL_RULES", f"Global rule enforcement failed: {e}")

def run_caresource(carrier_row: pd.Series) -> dict:
    """
    CareSource Handler (Full debug version)
      1️⃣ Fetch CRM contracts and insert to queue
      2️⃣ Apply global rules
      3️⃣ Enrich agents and add principals
      4️⃣ Validate resident_state
      5️⃣ Mark orphan principals
      6️⃣ Order principals (if any)
      7️⃣ Return summary for post-processing
    """
    carrier_id = carrier_row["carrier_id"]
    carrier_name = carrier_row["carrier_name"]
    crm_filter = carrier_row.get("crm_filter")
    company_id = carrier_row.get("company_id")
    run_id = f"ACC_{carrier_id}"
    script = "ACC_RPA_CARESOURCE"

    print(f"\n▶️ CareSource handler started for {carrier_name} @ {now_cst():%I:%M %p %Z}")

    try:
        # ======================================================
        # 1️⃣ CRM FETCH
        # ======================================================
        log_phase_start("CRM_FETCH", carrier_name, run_id)

        contracts = zoho_utils.get_contracts(
            carrier_id=carrier_id,
            npn_list=config.TEST_NPNS if config.TEST_MODE else [],
            crm_filter=crm_filter,
            allow_full_fetch=True,
        )

        if contracts is None or contracts.empty:
            print(f"ℹ️ No contracts found for {carrier_name}")
            return {"carrier": carrier_name, "carrier_id": carrier_id, "success": True}

        # Postgres inbound dedupe
        #contracts = deduplicate_contracts(
        #    contracts, "carrier", "npn", "status_date", "product_type"
        #)

        # Zoho inbound dedupe
        contracts = deduplicate_contracts(
            contracts, "Carrier.id", "Agent.NPN", "Status_Date"
        )
        log_phase_success("CRM_FETCH", carrier_name, run_id)

        # ======================================================
        # 2️⃣ INSERT QUEUE
        # ======================================================
        log_phase_start("QUEUE_INSERT", carrier_name, run_id)

        # Postgres Payload
        """
        payloads = [
            {
                "carrier_id": carrier_id,
                "company_id": company_id,
                "npn": str(r.get("npn") or "").strip(),
                "agent_first_name": r.get("first_name"),
                "agent_last_name": r.get("last_name"),
                "email": r.get("email"),
                "contract_id": r.get("name"),
                "id": r.get("contract_id_crm"),
                "agent_id": r.get("agent_id_crm"),
                "contract_status": r.get("status"),
                "status": "Pending",
                "status_date": r.get("status_date"),
                "special_incl": 0,
                "orph_principal": 0,
                "pk_id": r.get("pk_id")
            }
            for _, r in contracts.iterrows()
            if str(r.get("npn") or "").strip()
        ]
        """

        # Zoho Payload
        payloads = [
            {
                "carrier_id": carrier_id,
                "company_id": company_id,
                "npn": str(r.get("Agent.NPN") or "").strip(),
                "agent_first_name": r.get("Agent.First_Name"),
                "agent_last_name": r.get("Agent.Last_Name"),
                "email": r.get("Agent.Email"),
                "contract_id": r.get("Name"),
                "id": r.get("Id"),
                "agent_id": r.get("Agent.id"),
                "contract_status": r.get("Status"),
                "status": "Pending",
                "status_date": r.get("Status_Date"),
                "special_incl": 0,
                "orph_principal": 0,
            }
            for _, r in contracts.iterrows()
            if str(r.get("Agent.NPN") or "").strip()
        ]

        queue_summary = insert_queue_records(payloads)
        log_phase_success("QUEUE_INSERT", carrier_name, run_id)

        # ======================================================
        # 3️⃣ ENRICH VALID AGENTS (Resident State + Agent Type)
        # ======================================================
        npns = [p["npn"] for p in payloads if p.get("npn")]

        def _clean_state(val):
            """Normalize Zoho state values: treat nan/None/blank as None."""
            if val is None:
                return None
            s = str(val).strip()
            if s.lower() in ("", "nan", "none", "null"):
                return None
            return s

        if npns:
            agents = zoho_utils.get_agents(npns)
            if not agents.empty:
                conn = get_postgres_connection()
                cur = conn.cursor()

                for _, a in agents.iterrows():
                    try:
                        # Postgres inbound
                        """mailing_state_raw = a.get("mailing_state")
                        resident_state_raw = a.get("resident_state")"""

                        # Zoho inbound
                        mailing_state_raw = a.get("Mailing_State")
                        resident_state_raw = a.get("Resident_State")

                        mailing_state = _clean_state(mailing_state_raw)
                        resident_state = _clean_state(resident_state_raw)
                        state_final = resident_state or mailing_state

                        cur.execute(
                            """
                            UPDATE wpo.ops_acc_process_queue
                               SET email=%s,
                                   resident_state=%s,
                                   agent_type=%s,
                                   updated_on=now() at time zone 'utc',
                                   status_date=CAST(now() at time zone 'utc' AS DATE)
                             WHERE carrier_id=%s AND npn=%s AND status IN ('Pending','Processing')
                            """,
                            (
                                # Zoho inbound
                                sanitize_sql_param(a.get("Email")),
                                sanitize_sql_param(state_final),
                                "Agency"
                                # Postgres inbound
                                #if str(a.get("type", "")).lower() == "firm"
                                # Zoho inbound
                                if str(a.get("Type", "")).lower() == "firm"
                                else "Agent",
                                carrier_id,
                                # Postgres inbound
                                #str(a.get("npn")),
                                # Zoho inbound
                                str(a.get("NPN")),
                            ),
                        )

                    except Exception as e:
                        safe_log(script, f"Agent enrichment failed for {a.get('npn')}: {e}")

                conn.commit()
                conn.close()

        # ======================================================
        # 4️⃣ ADD RESPONSIBLE AGENTS (Principals)
        # ======================================================
        q = fetch_queue(carrier_id=carrier_id)

        if isinstance(q, pd.DataFrame) and not q.empty and "agent_type" in q.columns:
            agencies = q[q["agent_type"].str.upper() == "AGENCY"]
            for _, agency in agencies.iterrows():
                try:
                    principal = zoho_utils.fetch_responsible_agent(agency.get("agent_id"))
                    if not principal:
                        # Mark this contract as 'needs attention'
                        print(
                            f"🟨 [DEBUG] No principal agent was found for the given contract."
                        )
                        conn = get_postgres_connection()
                        cur = conn.cursor()
                        cur.execute(
                            """
                            UPDATE wpo.ops_acc_process_queue
                               SET contract_status = 'Needs Attention',
                                   status = 'Success',
                                   updated_on = now() at time zone 'utc',
                                   crm_note = 'RPA: No principal agent was found for this contract.'
                             WHERE carrier_id=%s AND npn=%s AND status IN ('Pending','Processing','Success')
                            """,
                            (
                                carrier_id,
                                sanitize_sql_param(agency.get("npn")),
                            ),
                        )
                        conn.commit()
                        conn.close()
                        continue  # transformed, no new insert

                    # Zoho inbound
                    npn_principal = str(principal.get("NPN") or "").strip()
                    if not npn_principal:
                        # Mark this contract as 'needs attention'
                        print(
                            f"🟨 [DEBUG] Found a principal agent, but their NPN was not obtained."
                        )
                        conn = get_postgres_connection()
                        cur = conn.cursor()
                        cur.execute(
                            """
                            UPDATE wpo.ops_acc_process_queue
                               SET contract_status = 'Needs Attention',
                                   status = 'Success',
                                   updated_on = now() at time zone 'utc',
                                   crm_note = 'RPA: Principal agent was found, but their NPN is not present.'
                             WHERE carrier_id=%s AND npn=%s AND status IN ('Pending','Processing','Success')
                            """,
                            (
                                carrier_id,
                                sanitize_sql_param(agency.get("npn")),
                            ),
                        )
                        conn.commit()
                        conn.close()
                        continue  # transformed, no new insert

                    existing_q = fetch_queue(carrier_id=carrier_id)
                    existing_npns = (
                        existing_q["npn"].astype(str).tolist()
                        if isinstance(existing_q, pd.DataFrame)
                        else []
                    )

                    if npn_principal in existing_npns:
                        print(
                            f"🟨 [DEBUG] Principal {npn_principal} already exists in queue → transforming existing row instead."
                        )

                        conn = get_postgres_connection()
                        cur = conn.cursor()
                        cur.execute(
                            """
                            UPDATE wpo.ops_acc_process_queue
                               SET agent_type='Principal',
                                   email=%s,                   -- inherit agency email
                                   resident_state=%s,          -- inherit resident state
                                   mailing_state=%s,           -- inherit agency state
                                   txn_id=%s,                  -- same txn id as agency
                                   special_incl=1,            -- mark as special include
                                   updated_on=now() at time zone 'utc'
                             WHERE carrier_id=%s AND npn=%s AND status IN ('Pending','Processing','Success')
                            """,
                            (
                                sanitize_sql_param(agency.get("email")),
                                sanitize_sql_param(agency.get("resident_state")),
                                sanitize_sql_param(agency.get("mailing_state")),
                                sanitize_sql_param(agency.get("txn_id")),
                                carrier_id,
                                npn_principal,
                            ),
                        )
                        conn.commit()
                        conn.close()
                        continue  # transformed, no new insert

                    print(f"✅ [DEBUG] Inserting new principal {npn_principal}")
                    insert_queue_records(
                        [
                            {
                                "carrier_id": carrier_id,
                                "company_id": company_id,
                                "npn": npn_principal,
                                "agent_first_name": principal.get("First_Name"),
                                "agent_last_name": principal.get("Last_Name"),
                                "email": agency.get("email"),
                                "resident_state": agency.get("resident_state"),
                                "mailing_state": agency.get("mailing_state"),
                                "agent_type": "Principal",
                                "status": "Success",
                                "contract_status": "Sent to Agent",
                                "txn_id": agency["txn_id"],
                                "special_incl": 1,
                            }
                        ]
                    )

                except Exception as e:
                    safe_log(script, f"Principal link failed for {agency.get('npn')}: {e}")

        # ======================================================
        # 5️⃣ GLOBAL RULES
        # ======================================================
        _apply_global_rules(carrier_id)

        # ======================================================
        # 6️⃣ VALIDATE resident_state (Sent to Agent vs Needs Attention)
        # ======================================================
        q = fetch_queue(carrier_id=carrier_id)

        if not isinstance(q, pd.DataFrame) or q.empty:
            print(
                f"⚠️ fetch_queue returned {type(q)} for {carrier_name} — skipping state validation."
            )
        else:
            conn = get_postgres_connection()
            cur = conn.cursor()
            for _, r in q.iterrows():
                has_state = bool(str(r.get("resident_state") or "").strip())
                contract_status = "Sent to Agent" if has_state else "Needs Attention"
                crm_note = "" if has_state else "RPA: Agent's resident state was not detected."
                print(f"📬 [DEBUG] Updating NPN={r.get('npn')} → {contract_status}")
                cur.execute(
                    """
                    UPDATE wpo.ops_acc_process_queue
                       SET contract_status=%s, status=%s, updated_on=now() at time zone 'utc', crm_note=%s
                     WHERE carrier_id=%s AND npn=%s AND status IN ('Pending','Processing')
                    """,
                    (contract_status, "Success", crm_note, carrier_id, r["npn"]),
                )
            conn.commit()
            conn.close()

        # ======================================================
        # 7️⃣ MARK ORPHAN PRINCIPALS (orph_principal = 1 only for true orphans)
        # ======================================================
        q2 = fetch_queue(carrier_id=carrier_id)

        if isinstance(q2, pd.DataFrame) and not q2.empty:
            conn = get_postgres_connection()
            cur = conn.cursor()

            crm_success_status = (carrier_row.get("crm_success_status") or "Sent to Agent").lower()

            # Agencies that survived (Success + success contract_status)
            agencies_ok = q2[
                (q2["agent_type"].str.upper() == "AGENCY")
                & (q2["status"].astype(str).str.lower() == "success")
                & (q2["contract_status"].astype(str).str.lower() == crm_success_status)
            ]

            valid_agency_txn_ids = set(
                agencies_ok["txn_id"].astype(str).dropna().tolist()
            )

            principals = q2[q2["agent_type"].astype(str).str.upper() == "PRINCIPAL"]

            for _, row in principals.iterrows():
                txn = str(row.get("txn_id") or "")
                is_orphan = txn not in valid_agency_txn_ids

                cur.execute(
                    """
                    UPDATE wpo.ops_acc_process_queue
                       SET orph_principal = %s
                     WHERE carrier_id = %s
                       AND npn = %s
                    """,
                    (1 if is_orphan else 0, carrier_id, row["npn"]),
                )

            conn.commit()
            conn.close()

        # ======================================================
        # 8️⃣ APPLY PRINCIPAL ORDERING (Agency → Principal → Agent)
        # ======================================================
        q_final = fetch_queue(carrier_id=carrier_id, status_filter="Success")
        print(
            f"🧾 [DEBUG] fetch_queue(final) → type={type(q_final)}, "
            f"rows={len(q_final) if isinstance(q_final, pd.DataFrame) else 'N/A'}"
        )

        if not isinstance(q_final, pd.DataFrame) or q_final.empty:
            print(
                f"⚠️ fetch_queue returned {type(q_final)} for {carrier_name} — skipping final summary."
            )
            return {"carrier": carrier_name, "carrier_id": carrier_id, "success": True}

        if "agent_type" in q_final.columns:
            principal_count = (
                q_final["agent_type"].astype(str).str.upper() == "PRINCIPAL"
            ).sum()
            if principal_count > 0:
                print(
                    f"📊 [DEBUG] Found {principal_count} principal(s) — applying ordering."
                )
                ordered_df = _order_principal_below_agency(q_final)
            else:
                print(f"ℹ️ [DEBUG] No principals found — skipping ordering.")
                ordered_df = q_final
        else:
            print(f"⚠️ [DEBUG] agent_type column missing — skipping ordering.")
            ordered_df = q_final

        print(
            f"🟩 [DEBUG] CareSource handler completed successfully → returning summary."
        )

        # ======================================================
        # 9️⃣ SUMMARY + TEMPLATE/EMAIL CONFIG
        # ======================================================
        summary_data = _summarize_queue(carrier_id, carrier_name, carrier_row)

        template_mapping = {
            "Onboarding Type": "agent_type",
            "Associated Agency": "270681372",
            "First Name": "agent_first_name",
            "Last Name": "agent_last_name",
            "Email": "email",
            "NPN": "npn",
            "General Agency": " ",
            "Resident State of Agent (required)": "resident_state",
        }

        email_template = {
            "subject": f"Agility CareSource Contract Request – {datetime.now():%Y-%m-%d}",
            "body": (
                "Hello,\n\nPlease send onboarding invitation links to the agents in the attached spreadsheet. "
                "Please feel free to reach out with any questions that you may have. \n\n\n\n\n\nSincerely,\nDataOps"
            ),
            "email_to": carrier_row.get("email_to"),
            "email_cc": carrier_row.get("email_cc"),
        }

        # If/when you want CRM upload back, uncomment crm_mapping and add it to return.
        crm_mapping = [
            {
                "module": "wpo.lup_master_agents_contracts",
                "field_mapping": {
                    "contract.pk_id": "pk_id",
                    "contract.status": "contract_status",
                    "contract.status_date": "status_date",
                },
            }
        ]

        zoho_mapping = [
            {
                "module": "Agent_Contracts",
                "field_mapping": {
                    "id": "id",
                    "Status": "contract_status",
                    "Status_Date": "status_date",
                },
            }
        ]

        return {
            **summary_data,
            "carrier": carrier_name,
            "carrier_id": carrier_id,
            "success": True,
            "queue_summary": queue_summary,
            "ordered_df": ordered_df,
            "df_all": ordered_df,
            "df_queue": ordered_df,
            "requires_template": True,
            "template_name": carrier_row.get("carrier_template"),
            "template_mapping": template_mapping,
            "email_template": email_template,
            #"crm_mapping": crm_mapping,
            "crm_filter": {
                "status": ["Success"],
                "agent_type": ["Agent", "Agency", "Principal"],
                "special_incl": ["true"],  # principals converted via logic
            },
            "template_filter": {
                # ✅ Only successful rows, with success contract status,
                # ✅ and NOT orphan principals
                "status": ["Success"],
                "contract_status": [carrier_row.get("crm_success_status")],
                "orph_principal": ["false","0"],
            },
            "header_row": 1,
            "header_column": 0,
            "email_to": carrier_row.get("email_to"),
            "email_cc": carrier_row.get("email_cc"),
            "zoho_mapping": zoho_mapping,
        }

    except Exception as e:
        return handle_ops_fail(
            carrier_id,
            carrier_name,
            f"Unhandled exception: {e}",
            "GEN_001",
        )

def run_priority_health(carrier_row: pd.Series) -> dict:
    """
    Priority Health Handler:
      1️⃣ Fetch CRM contracts and insert to queue
      2️⃣ Enrich agent info (Contacts)
      3️⃣ Validate E&O, W9, Contract docs from Drive
      4️⃣ Update queue statuses accordingly
      5️⃣ Return metadata for CRM + email + unified post-processing
    """

    carrier_id   = carrier_row["carrier_id"]
    carrier_name = carrier_row["carrier_name"]
    company_id   = carrier_row.get("company_id")
    crm_filter   = carrier_row.get("crm_filter")
    run_id       = f"ACC_{carrier_id}"
    script       = "ACC_RPA_PRIORITY"

    print(f"\n▶️ Priority Health handler started for {carrier_name} @ {now_cst():%I:%M %p %Z}")

    try:
        # ======================================================
        # 1️⃣ Fetch Contracts (Zoho)
        # ======================================================
        #init_log_entry("ACC_RPA_PRIORITY", run_id=run_id)
        log_phase_start("CRM_FETCH", carrier_name, run_id)
        contracts_df = zoho_utils.get_contracts(
            carrier_id=carrier_id,
            npn_list=config.TEST_NPNS if config.TEST_MODE else [],
            crm_filter=crm_filter,
            allow_full_fetch=True
        )

        if contracts_df is None or contracts_df.empty:
            print(f"ℹ️ No contracts found for {carrier_name}")
            return {"carrier": carrier_name, "carrier_id": carrier_id, "success": True}

        payloads = []
        base_gdrive_url = carrier_row.get("base_gdrive_url") or "https://drive.google.com/drive/folders/"

        # Postgres inbound
        """for _, r in contracts_df.iterrows():
            npn = str(r.get("npn") or "").strip()
            if not npn:
                continue
            gdid = r.get("gdriveextension__drive_folder_id")
            drive_url = f"{base_gdrive_url}{gdid}" if gdid else None
            payloads.append({
                "carrier_id": carrier_id,
                "company_id": company_id,
                "npn": npn,
                "agent_first_name": r.get("first_name"),
                "agent_last_name": r.get("last_name"),
                "email": r.get("email"),
                "contract_id": r.get("name"),
                "id": r.get("contract_id_crm"),
                "agent_id": r.get("agent_id_crm"),
                "status": "Pending",
                "status_date": now_cst().date(),
                "drive_url": drive_url,
                "pk_id": r.get("pk_id")
            })"""

        # Zoho inbound
        for _, r in contracts_df.iterrows():
            npn = str(r.get("Agent.NPN") or "").strip()
            if not npn:
                continue
            gdid = r.get("Google_Drive_ID")
            drive_url = f"{base_gdrive_url}{gdid}" if gdid else None
            payloads.append({
                "carrier_id": carrier_id,
                "company_id": company_id,
                "npn": npn,
                "agent_first_name": r.get("Agent.First_Name"),
                "agent_last_name": r.get("Agent.Last_Name"),
                "email": r.get("Agent.Email"),
                "contract_id": r.get("Name"),
                "id": r.get("Id"),
                "agent_id": r.get("Agent.id"),
                "status": "Pending",
                "status_date": now_cst().date(),
                "drive_url": drive_url,
            })

        queue_summary = insert_queue_records(payloads)
        log_phase_success("CRM_FETCH", carrier_name, run_id)
        print(f"📥 Queue insert summary → inserted={queue_summary.get('inserted', 0)}")

        # ======================================================
        # 2️⃣ Enforce Global Rules (Missing NPN/Email)
        # ======================================================
        _apply_global_rules(carrier_id)

        # ======================================================
        # 3️⃣ Enrich Agent Info (CRM Contacts)
        # ======================================================
        q = fetch_queue(carrier_id=carrier_id)
        if q.empty:
            print(f"ℹ️ No queue data found for {carrier_name} after insert.")
            return {"carrier": carrier_name, "success": True}

        npns = [n for n in q["npn"].astype(str).tolist() if n]
        # Postgres inbound
        #agents_df = get_agents(npns, fields=["npn", "email", "mailing_state", "type", "expiration_date"])
        # Zoho inbound
        agents_df = zoho_utils.get_agents(npns, fields=["NPN", "Email", "Mailing_State", "Type", "Expiration_Date"])

        conn = get_postgres_connection()
        cur = conn.cursor()
        for _, a in agents_df.iterrows():
            try:
                cur.execute("""
                    UPDATE wpo.ops_acc_process_queue
                       SET email=%s, mailing_state=%s, agent_type=%s, eo_crm=%s,
                           updated_on=now() at time zone 'utc'
                     WHERE carrier_id=%s AND npn=%s AND status IN ('Pending','Processing')
                """, (
                    sanitize_sql_param(a.get("email")),
                    sanitize_sql_param(a.get("mailing_state")),
                    "Agency" if str(a.get("type") or "").lower() == "firm" else "Agent",
                    sanitize_sql_param(a.get("expiration_date")),
                    carrier_id,
                    str(a.get("npn")),
                ))
            except Exception as e:
                safe_log(script, f"CRM enrichment failed for {a.get('NPN')}: {e}")
        conn.commit()
        conn.close()
        print(f"✅ Enrichment complete for {len(agents_df)} agent(s)")

        # ======================================================
        # 4️⃣ Validate Docs (Drive → E&O, W9, Contract) — THREADED
        # ======================================================
        from concurrent.futures import ThreadPoolExecutor, as_completed

        crm_success_stat = str(carrier_row.get("crm_success_status") or "Submitted to Carrier").strip()
        download_path = carrier_row.get("download_path") or os.getenv("TEMP", "/tmp")

        def _process_doc_row(rec: pd.Series) -> dict:
            """Worker: validate docs + update queue for a single record."""
            npn_local = str(rec["npn"])
            drive_url_local = rec.get("drive_url")
            try:
                result = validate_priority_docs(
                    gdrive_url=drive_url_local,
                    npn=npn_local,
                    base_download_path=download_path,
                    carrier_name=carrier_name,
                )

                eo_path = result.get("eo_path")
                w9_path = result.get("w9_path")
                ctr_path = result.get("contract_path")
                eo_valid_until = result.get("eo_valid_until")
                eo_valid = bool(result.get("eo_valid"))

                # ✅ Decide final contract_status
                crm_note = ''
                if not ctr_path:
                    final_contract_status = "Needs Attention"
                    crm_note = "RPA: Contract could not be found in the drive."
                else:
                    if not eo_path or not eo_valid:
                        final_contract_status = "Pending - Need E&O" if w9_path else "Pending - Need E&O + W-9"
                        crm_note = "RPA: Agent E&O is missing, or the expiration date was not found."
                    elif not w9_path:
                        final_contract_status = "Pending - Need W-9"
                    else:
                        final_contract_status = crm_success_stat  # All good!

                update_queue_where({
                    "contract_status": final_contract_status,
                    "eo_path": eo_path,
                    "w9_path": w9_path,
                    "contract_path": ctr_path,
                    "eo_valid_until": eo_valid_until,
                    "status": "Success",
                    "status_date": now_cst().date(),
                    "crm_note": crm_note,
                }, {"carrier_id": carrier_id, "npn": npn_local})

                return {"npn": npn_local, "ok": True}
            except Exception as e:
                handle_ops_fail(carrier_id, carrier_name, f"Drive listing failed: {e}", "DRIVE_API_ERROR")
                update_queue_where({
                    "status": "Fail",
                    "contract_status": "Needs Attention",
                    "status_date": now_cst().date(),
                    "crm_note": 'RPA: The drive could not be accessed.'
                }, {"carrier_id": carrier_id, "npn": npn_local})
                return {"npn": npn_local, "ok": False, "error": str(e)}

        # Run validation in parallel (5 workers)
        futures = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            for _, rec in q.iterrows():
                futures.append(executor.submit(_process_doc_row, rec))

            for f in as_completed(futures):
                _ = f.result()  # we already updated DB inside worker; this is just to surface exceptions

        # ======================================================
        # 5️⃣ Prepare Data for Post-Processing
        # ======================================================
        q_final = fetch_queue(carrier_id=carrier_id, status_filter=["Success"])

        email_template = {
            "subject": "Priority Health New Agent under Agility (NPN: {npn})",
            "body": (
                "Good {greeting},\n\n"
                "Please see attached contracting forms for the below agent. "
                "Agent will be aligned under Agility Insurance Services.\n\n"
                "Agent Name: {agent_first_name} {agent_last_name}\n"
                "NPN: {npn}\n"
                "Email: {email}\n\n\n\n\n\n"
                "Thank you,\nData Ops"
            ),
            "attachments_source": "queue",
            "email_to": carrier_row.get("email_to"),
            "email_cc": carrier_row.get("email_cc"),
        }

        crm_mapping = [
            {
                "module": "wpo.lup_master_agents_contracts",
                "field_mapping": {
                    "contract.pk_id": "pk_id",
                    "contract.status": "contract_status",
                    "contract.status_date": "status_date",
                },
            }
        ]

        zoho_mapping = [
            {
                "module": "Agent_Contracts",
                "field_mapping": {
                    "id": "id",
                    "Status": "contract_status",
                    "Status_Date": "status_date",
                },
            }
        ]

        # ✅ Only include Contacts update if at least one valid E&O date exists
        if "eo_valid_until" in q_final.columns:
            has_valid_eo = (
                q_final["eo_valid_until"]
                    .dropna()
                    .astype(str)
                    .str.strip()
                    .ne("")
                    .any()
            )
            if has_valid_eo:
                crm_mapping.append({
                    "module": "Contacts",
                    "field_mapping": {
                        "NPN": "npn",
                        "Expiration_Date": "eo_valid_until",
                    },
                })
                print(f"🩺 CRM mapping includes Contacts update (E&O present).")
            else:
                print(f"ℹ️ Skipping Contacts update — no valid E&O dates found.")
        else:
            print(f"ℹ️ Column eo_valid_until not found — skipping Contacts mapping.")

        print(f"🏁 Priority Health handler completed → {carrier_name}")
        summary_data = _summarize_queue(carrier_id, carrier_name, carrier_row)
        return {
            **summary_data,
            "carrier": carrier_name,
            "carrier_id": carrier_id,
            "success": True,
            "queue_summary": queue_summary,
            "df_queue": q_final,
            "requires_template": False,  # No Excel template
            "email_template": email_template,
            #"crm_mapping": crm_mapping,
            "crm_filter": {"status": ["Success"], "agent_type": ["Agent", "Agency"]},
            "template_filter": None,
            "header_row": 1,
            "header_column": 0,
            "email_to": carrier_row.get("email_to"),
            "email_cc": carrier_row.get("email_cc"),
            "zoho_mapping": zoho_mapping,
        }

    except Exception as e:
        return handle_ops_fail(
            carrier_id, carrier_name, f"Unhandled exception: {e}", "GEN_001"
        )

def run_bcbsne(carrier_row: pd.Series) -> dict:
    """
    Blue Cross Blue Shield of Nebraska Handler (BCBSNE)
      1️⃣ Fetch CRM contracts and insert to queue
      2️⃣ Skip global rule enforcement
      3️⃣ Validate only Contract (no E&O/W9)
      4️⃣ Update queue statuses accordingly
      5️⃣ Return metadata for CRM + email + unified post-processing
    """
    carrier_id   = carrier_row["carrier_id"]
    carrier_name = carrier_row["carrier_name"]
    company_id   = carrier_row.get("company_id")
    crm_filter   = carrier_row.get("crm_filter")
    crm_success  = str(carrier_row.get("crm_success_status") or "Submitted to Carrier").strip()
    crm_fail     = str(carrier_row.get("crm_fail_status") or "Needs Attention").strip()
    run_id       = f"ACC_{carrier_id}"
    script       = "ACC_RPA_BCBSNE"

    print(f"\n▶️ BCBSNE handler started for {carrier_name} @ {now_cst():%I:%M %p %Z}")

    try:
        # ======================================================
        # 1️⃣ Fetch Contracts (Zoho)
        # ======================================================
        #init_log_entry("ACC_RPA_BCBSNE", run_id=run_id)
        log_phase_start("CRM_FETCH", carrier_name, run_id)
        contracts_df = zoho_utils.get_contracts(
            carrier_id=carrier_id,
            npn_list=config.TEST_NPNS if config.TEST_MODE else [],
            crm_filter=crm_filter,
            allow_full_fetch=True,
        )

        if contracts_df is None or contracts_df.empty:
            print(f"ℹ️ No contracts found for {carrier_name}")
            return {"carrier": carrier_name, "carrier_id": carrier_id, "success": True}

        payloads = []
        base_gdrive_url = carrier_row.get("base_gdrive_url") or "https://drive.google.com/drive/folders/"
        # Postgres inbound
        """for _, r in contracts_df.iterrows():
            npn = str(r.get("npn") or "").strip()
            if not npn:
                continue
            gdid = r.get("google_drive_id")
            drive_url = f"{base_gdrive_url}{gdid}" if gdid else None
            payloads.append({
                "carrier_id": carrier_id,
                "company_id": company_id,
                "npn": npn,
                "agent_first_name": r.get("first_name"),
                "agent_last_name": r.get("last_name"),
                "email": r.get("email"),
                "contract_id": r.get("name"),
                "id": r.get("contract_id_crm"),
                "agent_id": r.get("agent_id_crm"),
                "status": "Pending",
                "status_date": now_cst().date(),
                "drive_url": drive_url,
                "pk_id": r.get("pk_id")
            })"""

        # Zoho inbound
        for _, r in contracts_df.iterrows():
            npn = str(r.get("Agent.NPN") or "").strip()
            if not npn:
                continue
            gdid = r.get("Google_Drive_ID")
            drive_url = f"{base_gdrive_url}{gdid}" if gdid else None
            payloads.append({
                "carrier_id": carrier_id,
                "company_id": company_id,
                "npn": npn,
                "agent_first_name": r.get("Agent.First_Name"),
                "agent_last_name": r.get("Agent.Last_Name"),
                "email": r.get("Agent.Email"),
                "contract_id": r.get("Name"),
                "id": r.get("Id"),
                "agent_id": r.get("Agent.id"),
                "status": "Pending",
                "status_date": now_cst().date(),
                "drive_url": drive_url,
            })

        queue_summary = insert_queue_records(payloads)
        log_phase_success("CRM_FETCH", carrier_name, run_id)
        print(f"📥 Queue insert summary → inserted={queue_summary.get('inserted', 0)}")

        # ======================================================
        # 2️⃣ Validate Contract (Drive) — THREADED
        # ======================================================
        from concurrent.futures import ThreadPoolExecutor, as_completed

        download_path = carrier_row.get("download_path") or os.getenv("TEMP", "/tmp")
        q = fetch_queue(carrier_id=carrier_id)
        if q.empty:
            print(f"No queue data found for {carrier_name} after insert.")
            return {"carrier": carrier_name, "success": True}

        def _process_contract_row(rec: pd.Series) -> dict:
            """Worker: validate contract + update queue for a single record."""
            npn_local = str(rec["npn"])
            drive_url_local = rec.get("drive_url")
            try:
                # run standard validator but only use contract section
                result = validate_priority_docs(
                    gdrive_url=drive_url_local,
                    npn=npn_local,
                    base_download_path=download_path,
                    carrier_name=carrier_name,
                    skip_non_contract=True,
                )

                ctr_path = result.get("contract_path")

                # ✅ Mark success or fail depending on contract presence
                if ctr_path:
                    update_queue_where({
                        "contract_status": crm_success,
                        "contract_path": ctr_path,
                        "status": "Success",
                        "status_date": now_cst().date(),
                    }, {"carrier_id": carrier_id, "npn": npn_local})
                else:
                    update_queue_where({
                        "contract_status": crm_fail,
                        "status": "Success",
                        "crm_note": "RPA: Contract could not be found.",
                        "status_date": now_cst().date(),
                    }, {"carrier_id": carrier_id, "npn": npn_local})

                return {"npn": npn_local, "ok": bool(ctr_path)}
            except Exception as e:
                handle_ops_fail(carrier_id, carrier_name, f"Drive listing failed: {e}", "DRIVE_API_ERROR")
                update_queue_where({
                    "status": "Fail",
                    "contract_status": crm_fail,
                    "status_date": now_cst().date()
                }, {"carrier_id": carrier_id, "npn": npn_local})
                return {"npn": npn_local, "ok": False, "error": str(e)}

        futures = []
        with ThreadPoolExecutor(max_workers=5) as executor:
            for _, rec in q.iterrows():
                futures.append(executor.submit(_process_contract_row, rec))

            for f in as_completed(futures):
                _ = f.result()  # ensure any exceptions bubble up

        # ======================================================
        # 3️⃣ Prepare Data for Post-Processing
        # ======================================================
        q_final = fetch_queue(carrier_id=carrier_id)
        email_template = {
            "subject": "New Contract Request BCBS NE - {agent_first_name} {agent_last_name}",
            "body": (
                "Hello,\n\n"
                "This agent is currently requesting BCBS of NEBRASKA under Agility Insurance Services\n\n"
                "Agent Name: {agent_first_name} {agent_last_name}\n"
                "NPN: {npn}\n\n\n\n\n\n"
                "Sincerely,\nData Ops"
            ),
            "attachments_source": "queue",
            "email_to": carrier_row.get("email_to"),
            "email_cc": carrier_row.get("email_cc"),
        }

        crm_mapping = [{
            "module": "wpo.lup_master_agents_contracts",
            "field_mapping": {
                "contract.pk_id": "pk_id",
                "contract.status": "contract_status",
                "contract.status_date": "status_date"}
        }]

        zoho_mapping = [
            {
                "module": "Agent_Contracts",
                "field_mapping": {
                    "id": "id",
                    "Status": "contract_status",
                    "Status_Date": "status_date",
                },
            }
        ]

        summary_data = _summarize_queue(carrier_id, carrier_name, carrier_row)

        print(f"🏁 BCBSNE handler completed → {carrier_name}")
        return {
            **summary_data,
            "carrier": carrier_name,
            "carrier_id": carrier_id,
            "success": True,
            "queue_summary": queue_summary,
            "df_queue": q_final,
            "requires_template": False,
            "email_template": email_template,
            #"crm_mapping": crm_mapping,
            "crm_filter": {"status": ["Success"]},
            "template_filter": None,
            "header_row": 1,
            "header_column": 0,
            "email_to": carrier_row.get("email_to"),
            "email_cc": carrier_row.get("email_cc"),
            "zoho_mapping": zoho_mapping,
        }

    except Exception as e:
        return handle_ops_fail(
            carrier_id, carrier_name, f"Unhandled exception: {e}", "GEN_001"
        )

def run_solis(carrier_row: pd.Series) -> dict:
    """
    Solis Handler — identical structure to CareSource, with Solis-specific rules:
      • Phone is required (after enrichment)
      • Global rules apply (missing email/npn)
      • No mailing_state validation
    """

    carrier_id = carrier_row["carrier_id"]
    carrier_name = carrier_row["carrier_name"]
    crm_filter = carrier_row.get("crm_filter")
    company_id = carrier_row.get("company_id")
    run_id = f"ACC_{carrier_id}"
    script = "ACC_RPA_SOLIS"

    print(f"\n▶️ Solis handler started for {carrier_name} @ {now_cst():%I:%M %p %Z}")

    try:
        # ======================================================
        # 1️⃣ CRM FETCH — EXACT SAME CALL SIGNATURE AS CARESOURCE
        # ======================================================
        log_phase_start("CRM_FETCH", carrier_name, run_id)

        contracts = zoho_utils.get_contracts(
            carrier_id=carrier_id,
            npn_list=config.TEST_NPNS if config.TEST_MODE else [],
            crm_filter=crm_filter,
            allow_full_fetch=True,
        )

        if contracts is None or contracts.empty:
            print(f"ℹ️ No contracts found for {carrier_name}")
            return {"carrier": carrier_name, "carrier_id": carrier_id, "success": True}

        # Postgres dedupe
        """contracts = deduplicate_contracts(
            contracts, "carrier", "npn", "status_date"
        )"""

        # Zoho dedupe
        contracts = deduplicate_contracts(
            contracts, "Carrier.id", "Agent.NPN", "Status_Date"
        )

        log_phase_success("CRM_FETCH", carrier_name, run_id)

        # ======================================================
        # 2️⃣ INSERT QUEUE
        # ======================================================
        log_phase_start("QUEUE_INSERT", carrier_name, run_id)

        # Postgres inbound
        """
        def _first_phone(row):
            ""Use first nonblank from Zoho contract row.""
            for f in ["phone", "mobile", "other_phone"]:
                v = row.get(f)
                if v and str(v).strip() and str(v).lower() != "nan":
                    return str(v).strip()
            return None

        payloads = []
        for _, r in contracts.iterrows():
            npn = str(r.get("npn") or "").strip()
            if not npn:
                continue

            payloads.append({
                "carrier_id": carrier_id,
                "company_id": company_id,
                "npn": npn,
                "agent_first_name": r.get("first_name"),
                "agent_middle_name": r.get("middle_name"),
                "agent_last_name": r.get("last_name"),
                "email": r.get("email"),
                "phone": _first_phone(r),
                "contract_id": r.get("name"),
                "id": r.get("contract_id_crm"),
                "agent_id": r.get("agent_id_crm"),
                "contract_status": r.get("status"),
                "status": "Pending",
                "status_date": r.get("status_date"),
                "special_incl": 0,
                "pk_id": r.get("pk_id")
            })"""

        # Zoho inbound
        def _first_phone(row):
            """Use first nonblank from Zoho contract row."""
            for f in ["Agent.Phone", "Agent.Mobile", "Agent.Other_Phone"]:
                v = row.get(f)
                if v and str(v).strip() and str(v).lower() != "nan":
                    return str(v).strip()
            return None

        payloads = []
        for _, r in contracts.iterrows():
            npn = str(r.get("Agent.NPN") or "").strip()
            if not npn:
                continue

            payloads.append({
                "carrier_id": carrier_id,
                "company_id": company_id,
                "npn": npn,
                "agent_first_name": r.get("Agent.First_Name"),
                "agent_middle_name": r.get("Agent.Middle_Name"),
                "agent_last_name": r.get("Agent.Last_Name"),
                "email": r.get("Agent.Email"),
                "phone": _first_phone(r),
                "contract_id": r.get("Name"),
                "id": r.get("Id"),
                "agent_id": r.get("Agent.id"),
                "contract_status": r.get("Status"),
                "status": "Pending",
                "status_date": r.get("Status_Date"),
                "special_incl": 0,
            })

        queue_summary = insert_queue_records(payloads)
        log_phase_success("QUEUE_INSERT", carrier_name, run_id)

        # ======================================================
        # 3️⃣ ENRICH FROM CONTACTS — EXACT SAME PATTERN AS CARESOURCE
        # ======================================================
        npns = [p["npn"] for p in payloads if p.get("npn")]

        if npns:
            agents = zoho_utils.get_agents(npns)  # FULL FIELDS AUTOMATICALLY RETURNED

            if not agents.empty:
                conn = get_postgres_connection()
                cur = conn.cursor()

                # Postgres inbound
                """for _, a in agents.iterrows():
                    try:
                        # Phone fallback: Phone → Mobile → Other Phone
                        phone_final = ''
                        if not pd.isna(a.get('phone')):
                            phone_final = a.get('phone')
                        elif not pd.isna(a.get('home_phone')):
                            phone_final = a.get('home_phone')
                        elif not pd.isna(a.get('other_phone')):
                            phone_final = a.get('other_phone')

                        cur.execute(""
                            UPDATE wpo.ops_acc_process_queue
                               SET email=%s,
                                   phone=%s,
                                   agent_middle_name=%s,
                                   agent_type=%s,
                                   updated_on=now() at time zone 'utc',
                                   status_date=CAST(now() at time zone 'utc' AS DATE)
                             WHERE carrier_id=%s 
                               AND npn=%s 
                               AND status IN ('Pending','Processing','Success')
                        "", (
                            sanitize_sql_param(a.get("email")),
                            sanitize_sql_param(phone_final),
                            sanitize_sql_param(a.get("middle_name")),
                            "Agency" if str(a.get("type", "")).lower() == "firm" else "Agent",
                            carrier_id,
                            str(a.get("npn"))
                        ))

                    except Exception as e:
                        safe_log(script, f"Solis enrichment failed for {a.get('NPN')}: {e}")"""

                # Zoho inbound
                for _, a in agents.iterrows():
                    try:
                        # Phone fallback: Phone → Mobile → Other Phone
                        phone_final = ''
                        if not pd.isna(a.get('Phone')):
                            phone_final = a.get('Phone')
                        elif not pd.isna(a.get('Mobile')):
                            phone_final = a.get('Mobile')
                        elif not pd.isna(a.get('Other_Phone')):
                            phone_final = a.get('Other_Phone')

                        cur.execute("""
                            UPDATE wpo.ops_acc_process_queue
                               SET email=%s,
                                   phone=%s,
                                   agent_middle_name=%s,
                                   agent_type=%s,
                                   updated_on=now() at time zone 'utc',
                                   status_date=CAST(now() at time zone 'utc' AS DATE)
                             WHERE carrier_id=%s 
                               AND npn=%s 
                               AND status IN ('Pending','Processing','Success')
                        """, (
                            sanitize_sql_param(a.get("Email")),
                            sanitize_sql_param(phone_final),
                            sanitize_sql_param(a.get("Middle_Name")),
                            "Agency" if str(a.get("Type", "")).lower() == "firm" else "Agent",
                            carrier_id,
                            str(a.get("NPN"))
                        ))

                    except Exception as e:
                        safe_log(script, f"Solis enrichment failed for {a.get('NPN')}: {e}")
                conn.commit()
                conn.close()

        # ======================================================
        # FINISH PROCESSING AGENCIES
        # ======================================================

        try:
            conn = get_postgres_connection()
            cur = conn.cursor()
            cur.execute("""
                UPDATE wpo.ops_acc_process_queue
                   SET contract_status = 'Active - Reporting Only',
                       status = 'Success',
                       updated_on = now() at time zone 'utc'
                 WHERE carrier_id = %s
                   AND status IN ('Pending','Processing')
                   AND agent_type = 'Agency'
            """, (carrier_id,))
            affected = cur.rowcount or 0
            conn.commit()
            conn.close()
            if affected:
                print(f"⚠️ SOLIS agency handling: {affected} record(s) processed and marked as completed.")
        except Exception as e:
            safe_log(script, f"SOLIS agency handling failed: {e}")

        # ======================================================
        # 4️⃣ GLOBAL RULES — SAME AS CARESOURCE
        # ======================================================
        _apply_global_rules(carrier_id)

        # ======================================================
        # 5️⃣ SOLIS RULE — Phone is REQUIRED
        # ======================================================
        try:
            conn = get_postgres_connection()
            cur = conn.cursor()

            cur.execute("""
                UPDATE wpo.ops_acc_process_queue
                   SET contract_status='Needs Attention',
                       status='Success',
                       updated_on=now() at time zone 'utc',
                       crm_note = 'RPA: Agent phone number could not be found.'
                 WHERE carrier_id = %s
                   AND status IN ('Pending','Processing')
                   AND (phone IS NULL OR TRIM(BOTH phone) = '')
            """, (carrier_id,))
            flagged = cur.rowcount or 0

            cur.execute("""
                UPDATE wpo.ops_acc_process_queue
                   SET status='Success',
                       contract_status = %s,
                       updated_on=now() at time zone 'utc'
                 WHERE carrier_id = %s
                   AND status IN ('Pending','Processing')
                   AND (phone IS NOT NULL AND TRIM(BOTH phone) <> '')
            """, (
                carrier_row.get("crm_success_status"),  # normally "Sent to Agent"
                carrier_id
            ))
            success_marked = cur.rowcount or 0

            conn.commit()
            conn.close()

            if flagged:
                print(f"⚠️ Solis: {flagged} record(s) flagged — missing phone.")
            if success_marked:
                print(f"✅ Solis: {success_marked} record(s) marked as Success")

        except Exception as e:
            safe_log(script, f"Solis phone-rule failed: {e}")

        # ======================================================
        # 6️⃣ SUMMARY (using shared helper)
        # ======================================================
        summary_data = _summarize_queue(carrier_id, carrier_name, carrier_row)
        q_final = fetch_queue(carrier_id=carrier_id)

        # ======================================================
        # 7️⃣ TEMPLATE MAPPING
        # ======================================================
        template_mapping = {
            "Agent Type*": "Producer",
            "First Name*": "agent_first_name",
            "Middle Name": "agent_middle_name",
            "Last Name*": "agent_last_name",
            "Email*": "email",
            "Phone": "phone",
            "NPN*": "npn",
            "FMO Name*": "Agility Insurance Services",
        }

        # ======================================================
        # 8️⃣ EMAIL TEMPLATE
        # ======================================================
        email_template = {
            "subject": f"Solis Requests ({datetime.now():%Y-%m-%d})",
            "body": (
                "I have attached Agility’s contract requests for Solis from today. "
                "Please feel free to reach out with any questions you may have!\n\n"
                "Sincerely,\n\nData Ops"
            ),
            "email_to": carrier_row.get("email_to"),
            "email_cc": carrier_row.get("email_cc"),
        }

        # ======================================================
        # 9️⃣ CRM MAPPING
        # ======================================================
        crm_mapping = [{
            "module": "wpo.lup_master_agents_contracts",
            "field_mapping": {
                "contract.pk_id": "pk_id",
                "contract.status": "contract_status",
                "contract.status_date": "status_date",
            },
        }]

        zoho_mapping = [
            {
                "module": "Agent_Contracts",
                "field_mapping": {
                    "id": "id",
                    "Status": "contract_status",
                    "Status_Date": "status_date",
                },
            }
        ]

        # ======================================================
        # 🔟 RETURN
        # ======================================================
        return {
            **summary_data,
            "carrier": carrier_name,
            "carrier_id": carrier_id,
            "df_queue": q_final,
            "ordered_df": q_final,
            "success": True,
            "queue_summary": queue_summary,
            "requires_template": True,
            "template_name": carrier_row.get("carrier_template"),
            "template_mapping": template_mapping,
            "template_filter": {
                "status": ["Success"],
                "contract_status": [carrier_row.get("crm_success_status")],
                "agent_type": ["Agent"],
            },
            "email_template": email_template,
            #"crm_mapping": crm_mapping,
            "header_row": 1,
            "header_column": 0,
            "email_to": carrier_row.get("email_to"),
            "email_cc": carrier_row.get("email_cc"),
            "zoho_mapping": zoho_mapping,
        }

    except Exception as e:
        return handle_ops_fail(
            carrier_id, carrier_name, f"Solis handler exception: {e}", "GEN_001"
        )

def run_cigna(carrier_row: pd.Series) -> dict:
    """
        Cigna Handler:
          • Fetch files from sharepoint
          • Verify E&O, expiry date
          • Upload to drive
          • If expiry date on CRM is older or blank, replace with this expiry date.
        """

    carrier_id = carrier_row["carrier_id"]
    carrier_name = carrier_row["carrier_name"]
    crm_filter = carrier_row.get("crm_filter")
    company_id = carrier_row.get("company_id")
    run_id = f"ACC_{carrier_id}"
    script = "ACC_RPA_CIGNA"

    print(f"\n▶️ Cigna handler started for {carrier_name} @ {now_cst():%I:%M %p %Z}")


    