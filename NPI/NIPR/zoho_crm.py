import os
import time
import requests
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# ==========================================================
# CONFIG
# ==========================================================
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

ZBASE = "https://www.zohoapis.com/crm/v2"
ZACCOUNTS = "https://accounts.zoho.com/oauth/v2"
ZORG_ID = "658450569"

# TOKEN FILE (EXISTING LOCATION)
ZOHO_TOKEN_FILE = r"C:\Users\myopsadmin\Documents\ZohoCRMToken.txt"

# For Licenses Owner (new records)
LICENSE_OWNER_EMAIL = "dataops@834labs.com"

# ==========================================================
# STATE MAP (for Resident / Non-Resident fields in Contacts)
# ==========================================================
STATE_MAP = {
    "AL": "Alabama","AK": "Alaska","AZ": "Arizona","AR": "Arkansas","CA": "California",
    "CO": "Colorado","CT": "Connecticut","DE": "Delaware","FL": "Florida","GA": "Georgia",
    "HI": "Hawaii","ID": "Idaho","IL": "Illinois","IN": "Indiana","IA": "Iowa",
    "KS": "Kansas","KY": "Kentucky","LA": "Louisiana","ME": "Maine","MD": "Maryland",
    "MA": "Massachusetts","MI": "Michigan","MN": "Minnesota","MS": "Mississippi",
    "MO": "Missouri","MT": "Montana","NE": "Nebraska","NV": "Nevada","NH": "New Hampshire",
    "NJ": "New Jersey","NM": "New Mexico","NY": "New York","NC": "North Carolina",
    "ND": "North Dakota","OH": "Ohio","OK": "Oklahoma","OR": "Oregon","PA": "Pennsylvania",
    "RI": "Rhode Island","SC": "South Carolina","SD": "South Dakota","TN": "Tennessee",
    "TX": "Texas","UT": "Utah","VT": "Vermont","VA": "Virginia","WA": "Washington",
    "WV": "West Virginia","WI": "Wisconsin","WY": "Wyoming"
}


# ==========================================================
# FETCH SECRETS FROM KEY VAULT
# ==========================================================
def get_zoho_secrets():
    """Fetch Zoho OAuth secrets from Azure Key Vault."""
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
    return (
        client.get_secret("dataops-zoho-api-client-id").value,
        client.get_secret("dataops-zoho-api-client-secret").value,
        client.get_secret("dataops-zoho-api-refresh-token").value,
    )


ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN = get_zoho_secrets()


# ==========================================================
# TOKEN LOADING / SAVING
# ==========================================================
def load_token_from_txt(path):
    """
    Load Zoho access token stored in legacy 2-line TXT format:
    Line 1: access_token
    Line 2: expiry_epoch
    """
    try:
        with open(path, "r") as f:
            lines = f.read().strip().splitlines()

        if len(lines) < 2:
            raise ValueError("Token file is corrupted or missing timestamp")

        token = lines[0].strip()
        expiry = float(lines[1].strip())
        return token, expiry

    except Exception as e:
        raise RuntimeError(f"[Zoho] Failed to load token file: {e}")


def save_token_to_txt(path, token, expiry):
    """Save access token back into the 2-line legacy TXT format."""
    try:
        with open(path, "w") as f:
            f.write(f"{token}\n{expiry}")
    except Exception as e:
        raise RuntimeError(f"[Zoho] Failed to save token file: {e}")


# ==========================================================
# AUTO-REFRESH TOKEN FUNCTION
# ==========================================================
def get_access_token():
    """Return valid token, refreshing if needed."""
    token, expiry = load_token_from_txt(ZOHO_TOKEN_FILE)

    if time.time() < expiry - 60:
        return token  # still valid

    print("[Zoho] Token expired , refreshing...")

    url = f"{ZACCOUNTS}/token"
    payload = {
        "refresh_token": ZOHO_REFRESH_TOKEN,
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "grant_type": "refresh_token",
    }

    resp = requests.post(url, data=payload)
    data = resp.json()

    if "access_token" not in data:
        raise RuntimeError(f"[Zoho] Refresh failed: {data}")

    new_token = data["access_token"]
    new_expiry = time.time() + 3600

    save_token_to_txt(ZOHO_TOKEN_FILE, new_token, new_expiry)

    print("[Zoho] Token refreshed.")
    return new_token


# ==========================================================
# HELPER: Zoho API Wrapper
# ==========================================================
def zoho_get(url, params=None):
    token = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}

    r = requests.get(url, headers=headers, params=params)
    if r.status_code != 200:
        raise RuntimeError(f"[Zoho GET] {r.text}")
    return r.json()


def zoho_put(url, payload):
    token = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}

    r = requests.put(url, headers=headers, json=payload)
    try:
        return r.json()
    except Exception:
        raise RuntimeError(f"Invalid Zoho JSON response: {r.text}")


def zoho_post(url, payload):
    """Simple POST wrapper for inserts (e.g., Licenses)."""
    token = get_access_token()
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}

    r = requests.post(url, headers=headers, json=payload)
    try:
        return r.json()
    except Exception:
        raise RuntimeError(f"Invalid Zoho JSON response: {r.text}")


# ==========================================================
# BASIC FIND Contact BY NPN (existing)
# ==========================================================
def find_contact_by_npn(npn: str):
    """
    Returns: { id, first_name, last_name, resident_state, dob }
    """
    url = f"{ZBASE}/Contacts/search"
    params = {"criteria": f"(NPN:equals:{npn})"}

    resp = zoho_get(url, params=params)

    records = resp.get("data")
    if not records:
        return None

    r = records[0]
    return {
        "id": r.get("id"),
        "first_name": r.get("First_Name"),
        "last_name": r.get("Last_Name"),
        "resident_state": r.get("Resident_State"),
        "dob": r.get("Date_of_Birth"),
    }


# ==========================================================
# FULL CONTACT PULL BY NPN (raw record)
# ==========================================================
def get_full_contact_by_npn(npn: str):
    """
    Returns the full Contacts record (raw Zoho dict) for a given NPN.
    """
    url = f"{ZBASE}/Contacts/search"
    params = {"criteria": f"(NPN:equals:{npn})"}

    resp = zoho_get(url, params=params)
    records = resp.get("data", [])
    if not records:
        return None

    return records[0]


def update_agent_status_only(npn: str, status: str):

    print(f"[Zoho] Updating ONLY Status for NPN {npn}  {status}")

    #Fetch existing contact by NPN
    contact = get_full_contact_by_npn(npn)

    if not contact:
        return {
            "success": False,
            "error": f"No Zoho Contact found for NPN {npn}"
        }

    contact_id = contact.get("id")

    if not contact_id:
        return {
            "success": False,
            "error": f"Zoho Contact ID missing for NPN {npn}"
        }

    #Build minimal safe payload (ID + Status ONLY)
    payload = {
        "data": [
            {
                "id": contact_id,
                "Status": status
            }
        ]
    }

    #Send update
    url = f"{ZBASE}/Contacts"
    resp = zoho_put(url, payload)

    ok = "data" in resp and resp["data"][0].get("status") == "success"

    return {
        "success": ok,
        "contact_id": contact_id,
        "updated_status": status,
        "zoho_response": resp
    }



# ==========================================================
# LICENSES PULL
# ==========================================================
def get_licenses_by_contact_id(contact_id: str):
    """
    Fetches license records from Licenses module using Agent lookup = Contact ID.
    If Zoho GET fails  return [] and allow inserts.
    """
    url = f"{ZBASE}/Licenses/search"
    params = {"criteria": f"(Agent.id:equals:{contact_id})"}

    try:
        resp = zoho_get(url, params=params)
        return resp.get("data", []) or []

    except Exception as e:
        print(f"[Zoho] GET Licenses failed for contact {contact_id}. Treating as NO licenses.")
        print(f"[Zoho Error]: {e}")
        return []   # CRITICAL: allows INSERTS to proceed



def get_licenses_by_npn(npn: str):
    """
    Convenience wrapper:
      1. Find contact by NPN
      2. Use its ID to fetch Licenses
    """
    contact = get_full_contact_by_npn(npn)
    if not contact:
        return []
    contact_id = contact.get("id")
    if not contact_id:
        return []
    return get_licenses_by_contact_id(contact_id)


# ==========================================================
#  Update Contact from Scraped Data (existing single-field version)
# ==========================================================
def update_contact_from_scrape(npn: str, resident_state: str, dob: str):
    print("Updating Zoho CRM with scraped values...")

    # Fetch Zoho Contact
    record = find_contact_by_npn(npn)
    if not record:
        return {"success": False, "error": "Contact not found in Zoho"}

    contact_id = record["id"]

    # Normalize DOB only when needed
    dob_iso = dob
    if "/" in dob:  # handle MM/DD/YYYY
        try:
            m, d, y = dob.split("/")
            dob_iso = f"{y}-{m.zfill(2)}-{d.zfill(2)}"
        except Exception:
            return {"success": False, "error": f"Invalid DOB format: {dob}"}

    payload = {
        "data": [
            {
                "id": contact_id,
                "Resident_State": resident_state,
                "Date_of_Birth": dob_iso,
            }
        ]
    }

    url = f"{ZBASE}/Contacts"
    resp = zoho_put(url, payload)

    if "data" in resp and resp["data"][0]["status"] == "success":
        return {
            "success": True,
            "contact_id": contact_id,
            "updated_resident_state": resident_state,
            "updated_dob": dob_iso,
            "zoho_response": resp,
        }

    return {"success": False, "error": f"Zoho update error: {resp}"}


# ==========================================================
# HELPERS FOR FULL NIPR UPDATE
# ==========================================================
def is_blank(val):
    """
    Treat None, empty, 'NA', 'N/A', 'Not Applicable' (case-insensitive)
    as blank.
    """
    if val is None:
        return True
    if isinstance(val, str):
        s = val.strip().lower()
        return s == "" or s in {"na", "n/a", "not applicable"}
    return False


def normalize_mmddyyyy_to_iso(date_str: str):
    """
    'MM/DD/YYYY' -> 'YYYY-MM-DD'
    Pass-through if already looks like YYYY-MM-DD.
    """
    if not date_str:
        return None

    date_str = date_str.strip()
    if "-" in date_str and len(date_str) == 10 and date_str[4] == "-":
        # Already ISO-ish 'YYYY-MM-DD'
        return date_str

    try:
        m, d, y = date_str.split("/")
        return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    except Exception:
        return date_str  # fallback, don't explode


# ==========================================================
# BUILD CONTACT UPDATE FROM FULL NIPR PARSED DATA
# ==========================================================
def build_contact_update_from_full_nipr(npn: str, parsed: dict):
    """
    Build a Contacts update payload using full NIPR parsed data.
    Safe behavior:
      • Add address only if CRM block is blank
      • Fill missing fields only when NIPR matches the SAME location
      • Never overwrite anything
      • Prevent wrong county updates
    """
    contact = get_full_contact_by_npn(npn)
    if not contact:
        raise RuntimeError(f"No contact found in Zoho for NPN={npn}")

    contact_id = contact.get("id")
    if not contact_id:
        raise RuntimeError("Contact record has no 'id'")

    update_fields = {"id": contact_id}
    updated_keys = []

    addr = parsed.get("addresses", {})
    dates = addr.get("date_updated", {}) if isinstance(addr.get("date_updated"), dict) else {}

    # ------------------------------------------------------------------
    # ADDRESS HELPERS
    # ------------------------------------------------------------------

    def crm_block(prefix):
        """Extract a consistent CRM block for comparison."""
        return {
            "street": (contact.get(f"{prefix}_Street") or "").strip(),
            "street2": (contact.get(f"{prefix}_Street_2") or "").strip(),
            "city": (contact.get(f"{prefix}_City") or "").strip(),
            "state": (contact.get(f"{prefix}_State") or "").strip(),
            "zip": (contact.get(f"{prefix}_Zip") or "").strip(),
        }

    def crm_blank(block):
        """CRM address block is blank ONLY when street + city + state empty."""
        return (not block["street"] and not block["city"] and not block["state"])

    def same_location(crm_block, nipr_block):
        """Match rule:
             ZIP matches OR (City+State match)
        """
        if not crm_block["zip"] or not nipr_block.get("zip"):
            return False  # cannot compare meaningfully

        zip_match = crm_block["zip"] == nipr_block.get("zip")
        city_state_match = (
            crm_block["city"].upper() == (nipr_block.get("city") or "").upper()
            and crm_block["state"].upper() == (nipr_block.get("state") or "").upper()
        )
        return zip_match or city_state_match

    def apply_address(prefix, crm_block_data, nipr_data, verified_key):
        """
        Only update fields according to rules:
          - If CRM empty  fill ALL
          - If CRM filled  fill missing ONLY when same_location=True
        """
        if not nipr_data:
            return

        nipr_block = {
            "street": nipr_data.get("street"),
            "street2": nipr_data.get("street2"),
            "city": nipr_data.get("city"),
            "state": nipr_data.get("state"),
            "zip": nipr_data.get("zip"),
        }

        # CASE 1: CRM block is empty  add entire NIPR address
        if crm_blank(crm_block_data):
            for key, val in nipr_block.items():
                if val:
                    update_fields[f"{prefix}_{key.capitalize().replace('Street', 'Street') }"] = val
                    updated_keys.append(f"{prefix}_{key.capitalize()}")
            # Verified Date
            du = dates.get(verified_key)
            if du:
                iso = normalize_mmddyyyy_to_iso(du)
                update_fields[f"{prefix}_Verified_Date"] = iso
                updated_keys.append(f"{prefix}_Verified_Date")
            return

        # CASE 2: CRM block not empty  update ONLY if same location
        if not same_location(crm_block_data, nipr_block):
            return  # IGNORE – prevents wrong county / bad merges

        # CASE 3: same location  fill missing values ONLY
        def fill_if_blank(field_suffix, value):
            if is_blank(contact.get(f"{prefix}_{field_suffix}")) and value:
                update_fields[f"{prefix}_{field_suffix}"] = value
                updated_keys.append(f"{prefix}_{field_suffix}")

        fill_if_blank("Street_2", nipr_block["street2"])
        # County specifically
        if nipr_data.get("county") and is_blank(contact.get(f"{prefix}_County")):
            update_fields[f"{prefix}_County"] = nipr_data["county"]
            updated_keys.append(f"{prefix}_County")

        # Verified Date
        du = dates.get(verified_key)
        if du and is_blank(contact.get(f"{prefix}_Verified_Date")):
            iso = normalize_mmddyyyy_to_iso(du)
            update_fields[f"{prefix}_Verified_Date"] = iso
            updated_keys.append(f"{prefix}_Verified_Date")

    # ------------------------------------------------------------------
    # APPLY ADDRESS LOGIC
    # ------------------------------------------------------------------

    # Mailing
    apply_address("Mailing", crm_block("Mailing"), addr.get("mailing"), "mailing")

    # Business  Retail_Address_
    apply_address("Retail_Address", crm_block("Retail_Address"), addr.get("business"), "business")

    # Residence
    apply_address("Residence", crm_block("Residence"), addr.get("residence"), "residence")

    # ------------------------------------------------------------------
    # PHONE / FAX / EMAIL — Same as before
    # ------------------------------------------------------------------
    business_phone = addr.get("business_phone")
    if business_phone:
        for pf in ["Phone", "Mobile", "Other_Phone", "Home_Phone", "Asst_Phone"]:
            if is_blank(contact.get(pf)):
                update_fields[pf] = business_phone
                updated_keys.append(pf)
                break

    fax_val = addr.get("fax")
    if fax_val and is_blank(contact.get("Fax")):
        update_fields["Fax"] = fax_val
        updated_keys.append("Fax")

    business_email = addr.get("business_email")
    if business_email:
        for ef in ["Email", "Secondary_Email"]:
            if is_blank(contact.get(ef)):
                update_fields[ef] = business_email
                updated_keys.append(ef)
                break

    # ------------------------------------------------------------------
    # DOB + Resident_State
    # ------------------------------------------------------------------
    dob = parsed.get("dob")
    if dob and is_blank(contact.get("Date_of_Birth")):
        update_fields["Date_of_Birth"] = normalize_mmddyyyy_to_iso(dob)
        updated_keys.append("Date_of_Birth")

    active_res = parsed.get("active_resident_states") or []
    all_res = parsed.get("resident_states") or []
    primary = active_res[0] if active_res else (all_res[0] if all_res else None)
    if primary and is_blank(contact.get("Resident_State")):
        update_fields["Resident_State"] = primary
        updated_keys.append("Resident_State")

    # ------------------------------------------------------------------
    # STATE FLAGS (TX, TN, etc.)
    # ------------------------------------------------------------------
    state_role_map = {}
    for lic in parsed.get("licenses", []):
        st = lic.get("state")
        residency = (lic.get("residency") or "").upper()
        full = STATE_MAP.get(st)
        if not full:
            continue

        role = "Resident" if residency.startswith("R") else "Non-Resident"
        if full not in state_role_map or (state_role_map[full] == "Non-Resident" and role == "Resident"):
            state_role_map[full] = role

    for full_state, role in state_role_map.items():
        if is_blank(contact.get(full_state)):
            update_fields[full_state] = role
            updated_keys.append(full_state)

    # ------------------------------------------------------------------
    # NOTHING TO UPDATE?
    # ------------------------------------------------------------------
    if len(update_fields) == 1:
        return contact_id, None, []

    return contact_id, update_fields, updated_keys



def apply_contact_update_from_full_nipr(npn: str, parsed: dict):
    """
    Calls build_contact_update_from_full_nipr, and if there is something
    to update, sends a PUT /Contacts call.
    """
    contact_id, fields, updated_keys = build_contact_update_from_full_nipr(npn, parsed)
    if not fields:
        print(f"[Contacts] No blank fields to update for NPN={npn}")
        return {
            "success": True,
            "contact_id": contact_id,
            "updated_fields": [],
            "zoho_response": None,
        }

    payload = {"data": [fields]}
    url = f"{ZBASE}/Contacts"
    resp = zoho_put(url, payload)

    ok = "data" in resp and resp["data"][0].get("status") == "success"
    return {
        "success": ok,
        "contact_id": contact_id,
        "updated_fields": updated_keys,
        "zoho_response": resp,
    }


# ==========================================================
# LICENSES UPSERT FROM FULL NIPR PARSED DATA
# ==========================================================
def upsert_licenses_from_full_nipr(npn: str, parsed: dict):

    contact = get_full_contact_by_npn(npn)
    if not contact:
        raise RuntimeError(f"No contact found for NPN={npn} (Licenses upsert)")

    contact_id = contact.get("id")
    if not contact_id:
        raise RuntimeError("Contact record has no 'id' (Licenses upsert)")

    existing = get_licenses_by_contact_id(contact_id) or []

    existing_index = {}
    for rec in existing:
        st = rec.get("License_State")
        num = rec.get("License_Number")
        if st and num:
            existing_index[(st, num)] = rec

    to_insert = []
    to_update = []

    for lic in parsed.get("licenses", []):

        if not lic.get("active", False):
            continue   # ONLY ACTIVE LICENSES

        st = lic.get("state")
        num = lic.get("license_number")
        if not st or not num:
            continue

        key = (st, num)

        issue_iso = normalize_mmddyyyy_to_iso(lic.get("issue_date"))
        exp_iso = normalize_mmddyyyy_to_iso(lic.get("expiration_date"))
        residency = (lic.get("residency") or "").upper()
        ltype = "Resident" if residency.startswith("R") else "Non-Resident"
        market = lic.get("market")

        if key in existing_index:
            rec = existing_index[key]
            rec_id = rec.get("id")

            changes = {}
            if issue_iso and issue_iso != rec.get("License_Date"):
                changes["License_Date"] = issue_iso
            if exp_iso and exp_iso != rec.get("License_Expiration"):
                changes["License_Expiration"] = exp_iso
            if ltype and ltype != rec.get("License_Type"):
                changes["License_Type"] = ltype
            if market and market != rec.get("License_Market"):
                changes["License_Market"] = market

            if changes:
                changes["id"] = rec_id
                to_update.append(changes)

        else:
            new_rec = {
                "Name": lic.get("internal_id") or f"{st}{num}",
                "Agent": {"id": contact_id},
                "License_State": st,
                "License_Number": num,
                "License_Date": issue_iso,
                "License_Expiration": exp_iso,
                "License_Type": ltype,
                "License_Market": market,
                "Owner": {"email": LICENSE_OWNER_EMAIL},
            }
            to_insert.append(new_rec)

    insert_resp = None
    update_resp = None

    if to_insert:
        insert_resp = zoho_post(f"{ZBASE}/Licenses", {"data": to_insert})

    if to_update:
        update_resp = zoho_put(f"{ZBASE}/Licenses", {"data": to_update})

    return {
        "success": True,
        "contact_id": contact_id,
        "insert_payload_count": len(to_insert),
        "update_payload_count": len(to_update),
        "insert_response": insert_resp,
        "update_response": update_resp,
    }