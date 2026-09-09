#!/usr/bin/env python3
"""
practice_fusion_full_export.py

Full latest Practice Fusion FHIR snapshot -> one JSON file per FHIR resource.

This is a self-contained pipeline: SMART Backend Services authentication
(private_key_jwt, Key Vault signing) and the full-snapshot REST crawl live in
this one file. There is no separate token-minter module to keep in sync.

Run manually:
    python practice_fusion_full_export.py
    python practice_fusion_full_export.py --selftest   # verify auth only, no PF call

Output:
    ./fhir_out/Patient.json
    ./fhir_out/Coverage.json
    ./fhir_out/Encounter.json
    ...
    ./fhir_out/_run_log.txt

Authentication (2-legged client_credentials with private_key_jwt):
  1. Discover the token endpoint from {PF_BASE_URL}/.well-known/smart-configuration.
  2. Build a JWT client assertion (iss=sub=client_id, aud=token endpoint, unique
     jti, short exp; header alg=RS384, kid=<thumbprint>).
  3. Sign it with the Key Vault key `fhir-token` via RS384 -- the PRIVATE KEY NEVER
     LEAVES THE VAULT (we hash locally, Key Vault signs the digest).
  4. POST it to /token -> bearer access token. Cache until just before expiry.

Key Vault auth -- who is allowed to ask Key Vault to sign:
  - Managed identity (default, production): leave PF_AUTH_MODE unset (or
    "managed_identity"). Uses DefaultAzureCredential. If more than one
    subscription/identity is reachable from where this runs, pin the exact one
    Key Vault should trust by setting PF_MANAGED_IDENTITY_CLIENT_ID (the
    user-assigned managed identity's Client ID -- NOT the subscription ID;
    subscription ID doesn't gate Key Vault auth). See guide.md for how to look
    these up with `az`.
  - Client secret (interim, buildout only): set PF_AUTH_MODE=client_secret plus
    PF_KV_TENANT_ID / PF_KV_CLIENT_ID / PF_KV_CLIENT_SECRET. This uses an
    IntelliBill app registration that has GET + SIGN on `fhir-token`.

SELF-TEST (run this before approval, or any time you touch Key Vault config):
    python practice_fusion_full_export.py --selftest
It signs a JWT via Key Vault and verifies it against the published jwks.json
URL. If it passes, key + signing + published JWKS are all correct and the
only remaining unknown is the client_id. No Practice Fusion call is made.

Important:
    - This script always pulls a fresh complete REST snapshot.
    - Existing JSON files are replaced only after a resource has been fetched
      and successfully written to a temporary file.
    - Pagination follows Practice Fusion's returned `link[relation=next]`
      URL verbatim.
    - 429 / 5xx responses are retried with backoff.
    - A 401 causes the access token to be refreshed once and retried.

Dependencies:
    pip install requests python-dotenv "PyJWT[crypto]" azure-identity azure-keyvault-keys
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin

import jwt  # PyJWT, for local signature verification in --selftest
import requests
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from azure.keyvault.keys.crypto import CryptographyClient, SignatureAlgorithm

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Practice Fusion / Key Vault authentication config (defaults are the
# known-good values; override via env / .env)
# ---------------------------------------------------------------------------
VAULT_URL = os.getenv("PF_KV_VAULT_URL")
KEY_NAME = os.getenv("PF_KV_KEY_NAME")
KID = os.getenv("PF_KID")
JWKS_URL = os.getenv("PF_JWKS_URL")
CLIENT_ID = os.getenv("PF_CLIENT_ID")
BASE_URL = os.getenv("PF_BASE_URL")

# Scopes granted on the application (SMART v2 .rs syntax). Trim if you narrow.
SCOPES = os.getenv("PF_SCOPES", " ".join([
    "system/Patient.rs", "system/Encounter.rs", "system/Condition.rs",
    "system/Procedure.rs", "system/Coverage.rs", "system/Observation.rs",
    "system/MedicationRequest.rs", "system/MedicationDispense.rs",
    "system/DiagnosticReport.rs", "system/Practitioner.rs",
    "system/Organization.rs", "system/Location.rs", "system/Provenance.rs",
    "system/DocumentReference.rs", "system/AllergyIntolerance.rs",
    "system/CarePlan.rs", "system/CareTeam.rs", "system/Device.rs",
    "system/Goal.rs", "system/Immunization.rs", "system/RelatedPerson.rs",
    "system/ServiceRequest.rs", "system/Specimen.rs", "system/Group.rs",
]))

ASSERTION_TYPE = os.getenv(
    "PF_ASSERTION_TYPE",
    "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
)
ASSERTION_TTL = 300  # seconds; SMART BSS requires a short-lived assertion


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _get_credential():
    """Which identity is allowed to ask Key Vault to sign.

    PF_AUTH_MODE is the only source of truth ("managed_identity", the default
    when unset, or "client_secret") -- no more magic "1" flag. Any other value
    is a hard error: we never want to silently fall through to the wrong
    credential just because of a typo.
    """
    auth_mode = os.getenv("PF_AUTH_MODE", "managed_identity").strip().lower()

    if auth_mode not in {"managed_identity", "client_secret"}:
        raise RuntimeError(
            f"PF_AUTH_MODE must be 'managed_identity' or 'client_secret', "
            f"got {auth_mode!r}"
        )

    if auth_mode == "client_secret":
        return ClientSecretCredential(
            tenant_id=os.environ["PF_KV_TENANT_ID"],
            client_id=os.environ["PF_KV_CLIENT_ID"],
            client_secret=os.environ["PF_KV_CLIENT_SECRET"],
        )

    # managed_identity: DefaultAzureCredential. If more than one identity or
    # subscription can be reached from this host/box, pin the exact
    # user-assigned managed identity by Client ID so DefaultAzureCredential
    # can't pick the wrong one as more identities get added over time.
    managed_identity_client_id = os.getenv("PF_MANAGED_IDENTITY_CLIENT_ID")
    kwargs = {}
    if managed_identity_client_id:
        kwargs["managed_identity_client_id"] = managed_identity_client_id
    return DefaultAzureCredential(**kwargs)


def _crypto_client() -> CryptographyClient:
    key_id = f"{VAULT_URL.rstrip('/')}/keys/{KEY_NAME}"
    return CryptographyClient(key_id, credential=_get_credential())


def build_signed_assertion(client_id: str, audience: str) -> str:
    """Assemble a JWT and sign the digest with Key Vault (RS384)."""
    now = int(time.time())
    header = {"alg": "RS384", "typ": "JWT", "kid": KID}
    payload = {
        "iss": client_id,
        "sub": client_id,
        "aud": audience,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "exp": now + ASSERTION_TTL,
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(payload, separators=(",", ":")).encode())
    ).encode("ascii")

    digest = hashlib.sha384(signing_input).digest()
    sig = _crypto_client().sign(SignatureAlgorithm.rs384, digest).signature
    return signing_input.decode("ascii") + "." + _b64url(sig)


def discover_token_endpoint(base_url: str) -> str:
    url = f"{base_url.rstrip('/')}/.well-known/smart-configuration"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    endpoint = r.json().get("token_endpoint")
    if not endpoint:
        raise RuntimeError(f"No token_endpoint in {url}")
    return endpoint


# ---- simple in-memory token cache -------------------------------------------
_token_cache = {"token": None, "expires_at": 0.0}


def get_access_token(force: bool = False) -> str:
    if not CLIENT_ID or not BASE_URL:
        raise RuntimeError(
            "PF_CLIENT_ID and PF_BASE_URL must be set (available after approval)."
        )
    if not force and _token_cache["token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["token"]

    token_endpoint = discover_token_endpoint(BASE_URL)
    assertion = build_signed_assertion(CLIENT_ID, token_endpoint)

    resp = requests.post(
        token_endpoint,
        data={
            "grant_type": "client_credentials",
            "client_assertion_type": ASSERTION_TYPE,
            "client_assertion": assertion,
            "scope": SCOPES,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Token request failed {resp.status_code}: {resp.text}")

    body = resp.json()
    _token_cache["token"] = body["access_token"]
    _token_cache["expires_at"] = time.time() + int(body.get("expires_in", 300)) - 60
    return _token_cache["token"]


def selftest() -> int:
    """Sign a JWT via Key Vault and verify it against the published JWKS URL.
    Proves key + signing + JWKS publishing are correct, without Practice Fusion."""
    print("Self-test: signing a JWT via Key Vault and verifying against JWKS URL")
    print(f"  vault    : {VAULT_URL}")
    print(f"  key      : {KEY_NAME}")
    print(f"  kid      : {KID}")
    print(f"  jwks url : {JWKS_URL}")

    dummy_aud = "https://selftest.local/token"
    dummy_client = CLIENT_ID or "selftest-client-id"

    token = build_signed_assertion(dummy_client, dummy_aud)
    print("  -> signed OK via Key Vault (private key never left the vault)")

    # Verify the signature against the LIVE published JWKS (also confirms the
    # kid resolves there and the URL serves correctly).
    signing_key = jwt.PyJWKClient(JWKS_URL).get_signing_key_from_jwt(token)
    claims = jwt.decode(token, signing_key.key, algorithms=["RS384"],
                        audience=dummy_aud)
    print("  -> verified OK against published JWKS")
    print(f"  -> kid in header matches a key in {JWKS_URL}")
    print(f"  -> claims: iss={claims['iss']} aud={claims['aud']} jti={claims['jti'][:8]}…")
    print("\nPASS. Signing chain and JWKS are correct. "
          "Only PF_CLIENT_ID + PF_BASE_URL remain (issued on approval).")
    return 0

# Resources verified in the supplied EHR integration project.
RESOURCES = [
    # "Patient",
    # "Coverage",
    # "RelatedPerson",
    "Encounter",
    # "Condition",
    # "Procedure",
    # "Observation",
    # "MedicationRequest",
    # "MedicationDispense",
    # "DiagnosticReport",
    # "DocumentReference",
    # "AllergyIntolerance",
    # "Immunization",
    # "CarePlan",
    # "CareTeam",
    # "Goal",
    # "Device",
    # "ServiceRequest",
    # "Provenance",
    # "Organization",
    # "Location",
    # "Practitioner",
]

OUT_DIR = Path(os.getenv("PF_OUT_DIR", "fhir_out"))
PAGE_SIZE = int(os.getenv("PF_PAGE_SIZE", "200"))
PACE_SECONDS = float(os.getenv("PF_PACE_SECONDS", "0.2"))
MAX_RETRIES = int(os.getenv("PF_MAX_RETRIES", "6"))
TIMEOUT_SECONDS = int(os.getenv("PF_TIMEOUT_SECONDS", "120"))


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
RUN_LOG = OUT_DIR / "_run_log.txt"


def log(message: str) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now(timezone.utc).isoformat()}  {message}"
    print(line, flush=True)
    with RUN_LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


# ---------------------------------------------------------------------------
# HTTP client with rate handling
# ---------------------------------------------------------------------------
_session = requests.Session()


def request_fhir(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    accept: str = "application/fhir+json",
    stream: bool = False,
) -> requests.Response:
    """Make a FHIR request with token refresh, retry and backoff."""
    backoff = 1.0
    force_refresh = False

    for attempt in range(1, MAX_RETRIES + 1):
        if PACE_SECONDS > 0:
            time.sleep(PACE_SECONDS)

        token = get_access_token(force=force_refresh)
        force_refresh = False

        response = _session.request(
            method,
            url,
            params=params,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": accept,
            },
            timeout=TIMEOUT_SECONDS,
            stream=stream,
        )

        if response.status_code == 401 and attempt < MAX_RETRIES:
            log("HTTP 401 received; refreshing Practice Fusion access token")
            force_refresh = True
            continue

        if response.status_code == 429 and attempt < MAX_RETRIES:
            retry_after = response.headers.get("Retry-After")
            try:
                wait = float(retry_after) if retry_after else backoff
            except ValueError:
                wait = backoff
            wait = min(max(wait, 1.0), 120.0)
            log(f"HTTP 429 rate limited; waiting {wait:.1f}s before retry")
            time.sleep(wait)
            backoff = min(backoff * 2, 60.0)
            continue

        if response.status_code in {500, 502, 503, 504} and attempt < MAX_RETRIES:
            wait = min(backoff, 60.0)
            log(
                f"HTTP {response.status_code} server error; "
                f"waiting {wait:.1f}s before retry"
            )
            time.sleep(wait)
            backoff = min(backoff * 2, 60.0)
            continue

        return response

    return response


# ---------------------------------------------------------------------------
# FHIR retrieval
# ---------------------------------------------------------------------------
def extract_resource(entry: dict[str, Any]) -> dict[str, Any]:
    resource = entry.get("resource")
    if isinstance(resource, dict):
        return resource
    return entry


def fetch_resource(resource_type: str) -> list[dict[str, Any]]:
    """Fetch every page for one FHIR resource type."""
    url = f"{BASE_URL.rstrip('/')}/{resource_type}"
    params: dict[str, Any] | None = {"_count": PAGE_SIZE}
    records: list[dict[str, Any]] = []
    page_number = 0

    while url:
        page_number += 1
        log(f"{resource_type}: fetching page {page_number}")

        response = request_fhir("GET", url, params=params)

        if response.status_code != 200:
            body = response.text[:1000]
            raise RuntimeError(
                f"{resource_type}: API request failed with HTTP "
                f"{response.status_code}: {body}"
            )

        try:
            bundle = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"{resource_type}: Practice Fusion returned invalid JSON"
            ) from exc

        entries = bundle.get("entry") or []
        page_records = [
            extract_resource(entry)
            for entry in entries
            if isinstance(entry, dict)
        ]
        records.extend(page_records)

        next_url = None
        for link in bundle.get("link") or []:
            if not isinstance(link, dict):
                continue
            if link.get("relation") == "next" and link.get("url"):
                next_url = str(link["url"])
                break

        # Practice Fusion's returned next URL is the source of truth.
        # If it is relative, resolve it without reconstructing pagination.
        url = urljoin(url, next_url) if next_url else None
        params = None

        log(
            f"{resource_type}: received {len(page_records)} records "
            f"(total so far: {len(records)})"
        )

    return records


def fetch_patient_by_id(patient_id: str) -> dict[str, Any]:
    """FHIR read for a single Patient by id (not a search - no bundle/paging,
    just GET {BASE_URL}/Patient/{id}). Raises RuntimeError on any non-200,
    including 404 (patient id doesn't exist / isn't visible to this client)."""
    patient_id = (patient_id or "").strip()
    if not patient_id:
        raise ValueError("patient_id is required")

    url = f"{BASE_URL.rstrip('/')}/Patient/{patient_id}"
    log(f"Patient: fetching single record {patient_id}")

    response = request_fhir("GET", url)

    if response.status_code != 200:
        body = response.text[:1000]
        raise RuntimeError(
            f"Patient/{patient_id}: API request failed with HTTP "
            f"{response.status_code}: {body}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Patient/{patient_id}: Practice Fusion returned invalid JSON"
        ) from exc


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------
def write_json_atomically(resource_type: str, records: list[dict[str, Any]]) -> tuple[Path, int]:
    """Write the raw FHIR records for one resource type as a JSON array."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    destination = OUT_DIR / f"{resource_type}.json"

    # Write to a temporary file in the same directory and replace the old JSON
    # file only after the complete resource snapshot has been serialized.
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{resource_type}.",
        suffix=".tmp",
        dir=OUT_DIR,
        text=True,
    )
    os.close(fd)
    temp_path = Path(temp_name)

    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(records, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, destination)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)

    return destination, len(records)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run() -> int:
    started = datetime.now(timezone.utc)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    log("=" * 80)
    log("Starting Practice Fusion full latest-data export")
    log(f"FHIR base URL: {BASE_URL}")
    log(f"Output directory: {OUT_DIR.resolve()}")
    log(f"Resources: {len(RESOURCES)}")

    # Authenticate before starting the potentially long extraction.
    log("Authenticating with Practice Fusion")
    get_access_token()
    log("Authentication successful")

    successful = 0
    failed = 0
    total_records = 0
    failures: list[str] = []

    for resource_type in RESOURCES:
        log("-" * 80)
        log(f"Starting resource: {resource_type}")

        try:
            records = fetch_resource(resource_type)
            path, count = write_json_atomically(resource_type, records)
            successful += 1
            total_records += count
            log(f"{resource_type}: SUCCESS - {count} records -> {path}")
        except Exception as exc:
            failed += 1
            failures.append(resource_type)
            log(f"{resource_type}: FAILED - {type(exc).__name__}: {exc}")
            # Continue with remaining resources so one failed endpoint does not
            # prevent unrelated resource snapshots from being refreshed.

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()

    log("=" * 80)
    log(
        f"Export finished: successful={successful}, failed={failed}, "
        f"total_records={total_records}, elapsed_seconds={elapsed:.1f}"
    )

    if failures:
        log(f"Failed resources: {', '.join(failures)}")
        log("Export completed with errors. Review _run_log.txt before using all JSON files.")
        return 1

    log("All resources exported successfully.")
    return 0


if __name__ == "__main__":
    try:
        if "--selftest" in sys.argv:
            raise SystemExit(selftest())
        raise SystemExit(run())
    except KeyboardInterrupt:
        print("\nExport cancelled by user.", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:
        print(f"Fatal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)
