import os
import requests
from uuid import uuid4
from datetime import datetime
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# ---------- CONFIGURATION ----------
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")

# ---------- AUTH & CONNECTION ----------
def get_azure_secrets():
    credential = DefaultAzureCredential()
    secret_client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
    client_id = secret_client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = secret_client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    return client_id, client_secret

# ---------- API LOOKUP ----------
def get_npi_registry_data(npi):
    url = f"https://npiregistry.cms.hhs.gov/api/?number={npi}&version=2.1"
    response = requests.get(url)
    response.raise_for_status()
    data = response.json()
    if not data.get("results"):
        raise Exception(f"NPI {npi} not found in registry.")
    return data["results"][0]

# ---------- DATA EXTRACTION ----------
def extract_npi_registry_payload(profile: dict, txn_id_provider: str) -> dict:
    basic = profile.get("basic", {})
    addresses = profile.get("addresses", [])
    taxonomies = profile.get("taxonomies", [])
    identifiers = profile.get("identifiers", [])
    enumeration_type = profile.get("enumeration_type")
    provider_type = "Individual" if enumeration_type == "NPI-1" else "Organization"

    location = next((a for a in addresses if a.get("address_purpose") == "LOCATION"), {})

    provider_info = {
        "primary_address_1": location.get("address_1"),
        "primary_address_2": location.get("address_2"),
        "city": location.get("city"),
        "state": location.get("state"),
        "zip": location.get("postal_code"),
        "gender": basic.get("sex") if enumeration_type == "NPI-1" else None,
        "primary_speciality": taxonomies[0].get("desc") if taxonomies else None,
        "secondary_speciality": "; ".join(
            tax.get("desc") for tax in taxonomies[1:] if tax.get("desc")
        ),
        "professional_degree": basic.get("credential") if enumeration_type == "NPI-1"
                              else basic.get("authorized_official_credential"),
        "type": provider_type,
        "updated_on": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    }

    locations = [
        {
            "txn_id": str(uuid4()),
            "source": "npi_registry",
            "type": addr.get("address_purpose"),
            "location_name": addr.get("address_purpose"),
            "contact": addr.get("telephone_number"),
            "fax": addr.get("fax_number"),
            "address_1": addr.get("address_1"),
            "address_2": addr.get("address_2"),
            "city": addr.get("city"),
            "state": addr.get("state"),
            "zip": addr.get("postal_code"),
            "txn_id_provider": txn_id_provider
        }
        for addr in addresses
    ]

    identifier_rows = [
        {
            "txn_id": str(uuid4()),
            "status": "Lead",
            "id_type": ident.get("code"),
            "id_issuer": ident.get("issuer"),
            "id_type_value": ident.get("identifier"),
            "id_description": ident.get("desc"),
            "id_issue_date": None,
            "id_state": ident.get("state"),
            "txn_id_provider": txn_id_provider
        }
        for ident in identifiers
    ]

    # Append license numbers from taxonomies as identifiers
    for tax in taxonomies:
        if tax.get("license"):
            identifier_rows.append({
                "txn_id": str(uuid4()),
                "status": "Lead",
                "id_type": "License Number",
                "id_issuer": tax.get("state"),
                "id_type_value": tax.get("license"),
                "id_description": "License Number",
                "id_issue_date": None,
                "id_state": tax.get("state"),
                "txn_id_provider": txn_id_provider
            })

    taxonomy_records = [
        {
            "code": tax.get("code"),
            "desc": tax.get("desc"),
            "state": tax.get("state"),
            "license": tax.get("license"),
            "primary": tax.get("primary")
        }
        for tax in taxonomies
    ]

    return {
        "provider_info": provider_info,
        "locations": locations,
        "identifiers": identifier_rows,
        "taxonomies": taxonomy_records
    }

# ---------- WRAPPER ----------
def run_npi_registry_scrape(npi: str, txn_id_provider: str) -> dict:
    profile = get_npi_registry_data(npi)
    return extract_npi_registry_payload(profile, txn_id_provider)

# ---------- LICENSE HELPER ----------
def extract_tx_license_from_npi(profile):
    for t in profile.get("taxonomies", []):
        if t.get("state") == "TX" and t.get("license"):
            return t["license"]
    return None