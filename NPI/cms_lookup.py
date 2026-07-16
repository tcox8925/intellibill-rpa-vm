# cms_lookup.py
import requests
from typing import Any, Dict, List, Optional

# --- CMS DAC (Doctors and Clinicians) dataset ---
CMS_DATASET_ID = "mj5m-pzi6"
CMS_BASE = "https://data.cms.gov/provider-data/api/1"

# Primary: stable dataset/index endpoint (no resource UUID needed)
CMS_STABLE_URL = f"{CMS_BASE}/datastore/query/{CMS_DATASET_ID}/0"

# Fallback: classic query endpoint (needs a resource UUID)
CMS_QUERY_URL = f"{CMS_BASE}/datastore/query"

# Metadata endpoint to discover current resource UUID if needed
CMS_META_URL = f"{CMS_BASE}/metastore/schemas/dataset/items/{CMS_DATASET_ID}"

# Cache the discovered resource ID so we only look it up once per process
_cached_resource_id: Optional[str] = None


def _discover_resource_id(timeout: int = 15) -> Optional[str]:
    """
    Hit the CMS metadata endpoint to find the current distribution resource ID
    for the DAC dataset. Returns the UUID or None.
    """
    global _cached_resource_id
    if _cached_resource_id:
        return _cached_resource_id

    try:
        r = requests.get(
            f"{CMS_META_URL}?show-reference-ids",
            timeout=timeout,
        )
        r.raise_for_status()
        meta = r.json()
        for dist in meta.get("distribution", []):
            rid = dist.get("identifier")
            if rid:
                _cached_resource_id = rid
                print(f"[CMS] Discovered resource ID: {rid}")
                return rid

    except Exception as e:
        print(f"[CMS] Resource ID discovery failed: {e}")

    return None


def _valid_pac(s: Optional[str]) -> bool:
    return bool(s and str(s).isdigit() and len(str(s)) == 10)


def _parse_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Extract PAC ID and CCNs from CMS result rows.
    """
    if not results:
        return {"pac_id": None, "cert_ids": [], "raw": {"results": []}, "source": "CMS"}

    # PAC ID
    raw_pac = results[0].get("ind_pac_id")
    pac_id = str(raw_pac).strip() if _valid_pac(str(raw_pac).strip()) else None

    # CCNs
    certs: List[str] = []
    for row in results:
        cert_number = row.get("facility_affiliations_certification_number")
        if cert_number:
            s = str(cert_number).strip()
            if s.isdigit():
                certs.append(s)

    # unique + stable order
    seen = set()
    cert_ids = []
    for c in certs:
        if c not in seen:
            seen.add(c)
            cert_ids.append(c)

    return {
        "pac_id": pac_id,
        "cert_ids": cert_ids,
        "raw": {"results": results},
        "source": "CMS",
    }


def _query_stable(npi: str, timeout: int) -> Optional[Dict[str, Any]]:
    """
    Try the stable dataset/index endpoint (no resource UUID needed).
    Returns parsed result dict or None on failure.
    """
    payload = {
        "conditions": [
            {"property": "npi", "operator": "=", "value": npi}
        ],
        "limit": 50,
    }
    try:
        r = requests.post(CMS_STABLE_URL, json=payload, timeout=timeout)
        r.raise_for_status()
        results = r.json().get("results", []) or []
        return _parse_results(results)
    except Exception as e:
        print(f"[CMS] Stable endpoint failed for NPI {npi}: {e}")
        return None


def _query_by_resource_id(npi: str, resource_id: str, timeout: int) -> Optional[Dict[str, Any]]:
    """
    Fallback: query using a discovered resource UUID.
    """
    payload = {
        "conditions": [
            {"resource": "t", "property": "npi", "operator": "=", "value": npi}
        ],
        "limit": 50,
        "resources": [{"id": resource_id, "alias": "t"}],
    }
    try:
        r = requests.post(CMS_QUERY_URL, json=payload, timeout=timeout)
        r.raise_for_status()
        results = r.json().get("results", []) or []
        return _parse_results(results)
    except Exception as e:
        print(f"[CMS] Resource ID query failed for NPI {npi}: {e}")
        return None


def fetch_cms_data(npi: str, timeout: int = 30) -> Dict[str, Any]:
    """
    Fetch PECOS PAC ID and facility CCNs for a given NPI from CMS.

    Strategy:
      1. Try stable dataset/index endpoint (no UUID, won't break on rotation)
      2. If that fails, discover current resource UUID from metadata API
      3. Retry with discovered UUID
      4. If all fail, return empty (pipeline continues without CMS data)

    Returns:
      {
        "pac_id": "##########" | None,
        "cert_ids": ["######", ...],
        "raw": { "results": [...] },
        "source": "CMS"
      }
    """
    empty = {"pac_id": None, "cert_ids": [], "raw": {"results": []}, "source": "CMS"}

    # --- Attempt 1: stable endpoint ---
    result = _query_stable(npi, timeout)
    if result is not None:
        return result

    # --- Attempt 2: discover resource ID and retry ---
    print("[CMS] Falling back to resource ID discovery...")
    resource_id = _discover_resource_id(timeout)
    if resource_id:
        result = _query_by_resource_id(npi, resource_id, timeout)
        if result is not None:
            return result

    print(f"[CMS] All attempts failed for NPI {npi}")
    return empty