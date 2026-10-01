# graph_auth.py
import os
from pathlib import Path

from azure.identity import ClientSecretCredential
from dotenv import load_dotenv

ROOT_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ROOT_ENV_FILE, override=False)

GRAPH_SCOPE = "https://graph.microsoft.com/.default"

def get_graph_access_token() -> str:
    """
    Returns a Graph access token for reading the Tebra OTP mailbox.

    Confirmed live 2026-10-01: this used to read the shared AZURE_TENANT_ID/
    AZURE_CLIENT_ID/AZURE_CLIENT_SECRET (falling back to the SynapseAccess
    Key Vault secrets) -- those creds are for the 834labs/"AHG Enterprise"
    tenant, used app-wide (Azure OpenAI, communication services, several
    callAI modules, blob storage auth -- see app/core/config.py and its many
    callers). The Tebra OTP mailbox (support@intellibillrcm.com) lives in a
    COMPLETELY SEPARATE Microsoft 365 tenant that AHG Enterprise has no
    access to -- Graph app-only auth is always scoped to one tenant, so
    every lookup 404'd with ErrorInvalidUser no matter what. This function
    is only ever called from email_read.py (nothing else uses graph_auth.py),
    so it gets its own dedicated, differently-named env vars instead,
    pointing at an app registration created IN the intellibillrcm.com
    tenant -- never reuse the shared AZURE_* vars here again.
    """
    tenant_id = os.environ.get("TEBRA_OTP_TENANT_ID", "").strip()
    client_id = os.environ.get("TEBRA_OTP_CLIENT_ID", "").strip()
    client_secret = os.environ.get("TEBRA_OTP_CLIENT_SECRET", "").strip()

    if not (tenant_id and client_id and client_secret):
        raise RuntimeError(
            "TEBRA_OTP_TENANT_ID / TEBRA_OTP_CLIENT_ID / TEBRA_OTP_CLIENT_SECRET "
            "must be set in .env -- these are a dedicated app registration in "
            "the intellibillrcm.com tenant (the one that owns the Tebra OTP "
            "mailbox), separate from this app's shared AZURE_* credentials."
        )

    sp = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
    )

    return sp.get_token(GRAPH_SCOPE).token