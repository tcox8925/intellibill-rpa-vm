import json, re
from typing import Optional, Tuple, Dict, Any
from openai import AzureOpenAI, NotFoundError
from azure.identity import DefaultAzureCredential
from insert_npi_registry_row import get_npi_registry_data

# === Azure OpenAI Client Config ===
endpoint = "https://powerbi-chat.cognitiveservices.azure.com/"
model = "gpt-4.1"
model_name = "data-validator"
api_version = "2024-12-01-preview"

_credential = DefaultAzureCredential()
def _get_oai_client() -> AzureOpenAI:
    """
    Creates a fresh AzureOpenAI client using a current MI token.
    Do this per call or per request to avoid token expiry issues.
    """
    token = _credential.get_token("https://cognitiveservices.azure.com/.default")
    return AzureOpenAI(
        api_version=api_version,
        azure_endpoint=endpoint,
        azure_ad_token=token.token,
    )
# === PECOS ID Lookup ===
def _format_nppes_name(basic: Dict[str, Any]) -> str:
    org = (basic or {}).get("organization_name")
    if org:
        return " ".join(org.split()).upper()
    parts = [
        (basic or {}).get("last_name"),
        (basic or {}).get("first_name"),
        (basic or {}).get("middle_name"),
        (basic or {}).get("name_suffix"),
    ]
    name = " ".join([p for p in parts if p])
    return " ".join(name.split()).upper()

def _nppes_city_state(profile: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    for a in profile.get("addresses", []):
        if a.get("address_purpose") == "LOCATION":
            return a.get("city"), a.get("state")
    if profile.get("addresses"):
        a = profile["addresses"][0]
        return a.get("city"), a.get("state")
    return None, None

def _strict_json_or_none(s: str) -> Optional[str]:
    s = s.strip()
    m = re.fullmatch(r'\{\s*"pecos_id"\s*:\s*"(\d{10})"\s*\}', s)
    if m:
        return m.group(1)
    if re.fullmatch(r'\{\s*"pecos_id"\s*:\s*null\s*\}', s):
        return None
    return None

def get_pecos_id(npi: str, cms_pac: Optional[str] = None) -> Optional[str]:
    # 1) Trust deterministic CMS PAC if present and valid
    if cms_pac and re.fullmatch(r"\d{10}", str(cms_pac)):
        return str(cms_pac)

    # 2) Pull NPPES profile for grounding
    try:
        profile = get_npi_registry_data(npi)
    except Exception:
        profile = {}

    basic = profile.get("basic", {}) or {}
    entity_type = "Individual" if profile.get("enumeration_type") == "NPI-1" else "Organization"
    exact_name = _format_nppes_name(basic) if basic else ""
    city, state = _nppes_city_state(profile)

    sys_msg = (
        "You are an expert in U.S. Medicare provider lookups. "
        "Your ONLY task: given NPI and the exact NPPES name/location, return ONLY the 10-digit PECOS PAC ID as strict JSON. "
        'If a unique 10-digit PAC cannot be confirmed to match the NPI holder, return {"pecos_id":null}. '
        "Never guess, never return the NPI, never add any extra text."
    )
    user_msg = (
        "NPI: {npi}\n"
        "Exact NPPES name: {name}\n"
        '{{"pecos_id":"##########"}} or {{"pecos_id":null}}'
    ).format(npi=npi, etype=entity_type, name=exact_name, city=city or "", state=state or "")

    try:
        resp = _get_oai_client().chat.completions.create(
        model=model_name,  # deployment name in Azure OpenAI
        temperature=0,
        top_p=1,
        max_tokens=20,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user_msg},
        ],
)
        content = (resp.choices[0].message.content or "").strip()
    except Exception:
        return None

    pac = _strict_json_or_none(content)
    if pac and pac != str(npi):
        return pac
    return None


# === Education Consolidation & Validation ===
def consolidate_education(
    npi: str,
    tmb_entries: list,
    npiprofile_entries: list,
    care_entries: list,
    use_gpt_normalization: bool = False,
    client=None,
    deployment: str = None,
) -> list:
    import re, json

    INSTITUTION_HINTS = ("university", "college", "school", "institute", "hospital", "medical center", "centre")

    def clean_year(val):
        if val is None:
            return None
        s = str(val)
        m = re.search(r"(19|20)\d{2}", s)
        return m.group(0) if m else None

    def norm_space(s):
        return re.sub(r"\s+", " ", s or "").strip()

    def looks_institution(name: str) -> bool:
        txt = (name or "").lower()
        return any(w in txt for w in INSTITUTION_HINTS)

    def is_specialty_only_local(program_name: str, typ: str) -> bool:
        # Drop obvious junk like "ORS" or empty, or residency with no institution words
        name = (program_name or "").strip()
        if not name:
            return True  # empty program name → junk
        # extremely short all-caps tokens (e.g., "ORS")
        if len(name) <= 4 and name.isupper() and name.isalpha():
            return True
        # If it's residency/fellowship/GME and has no institution hints, it's likely junk
        t = (typ or "").lower()
        if any(k in t for k in ("residency", "fellowship", "graduate medical education")) and not looks_institution(name):
            return True
        # Non-Medical Education entries with no institution hints and 1–2 words → likely specialty label
        if not looks_institution(name) and len(name.split()) <= 2 and t != "medical education":
            return True
        return False

    def coerce(entry):
        if not isinstance(entry, dict):
            return None
        program = norm_space(entry.get("program_name") or entry.get("school_program_name") or "")
        grad = clean_year(entry.get("grad_year"))
        etype = norm_space(entry.get("type") or ("Medical Education" if "residency" not in program.lower() else "RESIDENCY"))
        location = norm_space(entry.get("location") or "")
        specialty = norm_space(entry.get("specialty") or "")

        if not program and not grad:
            return None

        out = {
            "program_name": program,
            "type": etype or "Medical Education",
            "grad_year": grad,
            "location": location
        }
        if specialty:
            out["specialty"] = specialty
        return out

    # 1) Local normalization + combine
    combined = []
    for src in (tmb_entries or []):
        n = coerce(src)
        if n: combined.append(n)
    for src in (npiprofile_entries or []):
        n = coerce(src)
        if n: combined.append(n)
    for src in (care_entries or []):
        n = coerce(src)
        if n: combined.append(n)

    # 1a) Local specialty-only filter BEFORE dedupe (so ORS dies even in DRY RUN)
    combined = [e for e in combined if not is_specialty_only_local(e.get("program_name"), e.get("type"))]

    # 2) Deduplicate (program_name, type, grad_year, location)
    seen = set()
    deduped = []
    for e in combined:
        key = (
            norm_space(e.get("program_name", "")).lower(),
            norm_space(e.get("type", "")).lower(),
            e.get("grad_year") or "",
            norm_space(e.get("location", "")).lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)

    # 3) GPT normalization + evaluation (kept as you wrote; just adding post-filter too)
    if use_gpt_normalization and deduped:
        prompt = """
You are a strict validator for physician education data.

Input: list of education entries from trusted sources (TMB, NPIProfile, Care.Healthline).

Tasks:
1. Expand institution abbreviations (e.g., 'KCCOM' -> 'Kansas City University College of Osteopathic Medicine').
2. Normalize city abbreviations (KC -> Kansas City).
3. Ensure grad_year is 4-digit or empty.
4. Mark is_specialty_only = true if the entry is only a specialty (e.g., 'ORS', 'Internal Medicine') without a valid institution name.
5. Keep type as one of: Medical Education, Residency, Fellowship, Graduate Medical Education.
6. Keep specialty empty for Medical Education unless provided.

Return ONLY JSON array with:
[
  {
    "program_name": "...",
    "type": "...",
    "grad_year": "YYYY or empty",
    "location": "City, State or City, Country",
    "specialty": "...",
    "is_specialty_only": true/false
  }
]
"""
        try:
            _cli = client or _get_oai_client()
            resp = _cli.chat.completions.create(
                model=deployment or model_name,  # prefer provided deployment; else fallback
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(deduped)}
                ],
                max_tokens=1300,
                temperature=0
            )
            content = (resp.choices[0].message.content or "").strip()
            polished = json.loads(content)

            if isinstance(polished, list) and polished:
                # GPT-driven filter
                polished = [e for e in polished if not e.get("is_specialty_only", False)]
                # Re-run minimal coerce/dedupe to be safe
                post = []
                for e in polished:
                    n = coerce(e)
                    if n: post.append(n)
                if post:
                    seen2, final = set(), []
                    for e in post:
                        key = (
                            norm_space(e.get("program_name", "")).lower(),
                            norm_space(e.get("type", "")).lower(),
                            e.get("grad_year") or "",
                            norm_space(e.get("location", "")).lower(),
                        )
                        if key in seen2:
                            continue
                        seen2.add(key)
                        final.append(e)
                    return final
        except Exception as e:
            print(f"[WARN] GPT normalization failed: {e}")

    return deduped



def enrich_carriers(carrier_names: list) -> list:
    """
    Returns a list of dicts shaped for upload_utils:
    [
      {
        "carrier_name": "...",
        "carrier_niac_number": "#####" or None,
        "group_number": "...",               # optional
        "state_of_domicile": "...",          # optional
        "headquarters_location": "City, ST", # optional
        "network_status": None               # keep None unless you add logic
      },
      ...
    ]
    If GPT can’t map NAIC, we still return the item with carrier_niac_number=None.
    """
    if not carrier_names:
        return []

    try:
        sys = (
            "Read ONLY the official NAIC companies listing. "
            "Return strict JSON as {\"items\":[{\"carrier_name\":\"...\","
            "\"carrier_niac_number\":\"...\",\"group_number\":\"...\","
            "\"state_of_domicile\":\"...\",\"headquarters_location\":\"City, State\"}...]}. "
            "If not found, include the carrier with null values."
        )
        user = json.dumps({"carrier_names": carrier_names})

        resp = _get_oai_client().chat.completions.create(
        model=model_name,                 # Azure deployment name
        temperature=0,
        max_tokens=1800,
        response_format={"type": "json_object"},
        messages=[
        {"role": "system", "content": sys},
        {"role": "user", "content": user}
    ],
)

        data = json.loads(resp.choices[0].message.content)
        items = data.get("items", data if isinstance(data, list) else [])
    except Exception as e:
        print(f"[GPT][carriers] fallback due to error: {e}")
        items = []

    # Ensure we at least return names
    fallback_set = {n.strip() for n in (carrier_names or []) if n and n.strip()}
    out = []
    seen = set()
    for it in items:
        name = (it.get("carrier_name") or "").strip()
        niac = (it.get("carrier_niac_number") or it.get("naic_number") or None)
        if name:
            seen.add(name.lower())
            out.append({
                "carrier_name": name,
                "carrier_niac_number": niac,
                "group_number": it.get("group_number"),
                "state_of_domicile": it.get("state_of_domicile"),
                "headquarters_location": it.get("headquarters_location"),
                "network_status": it.get("network_status")
            })

    # Add any missing names with null NAIC
    for name in sorted(fallback_set):
        if name.lower() not in seen:
            out.append({
                "carrier_name": name,
                "carrier_niac_number": None,
                "group_number": None,
                "state_of_domicile": None,
                "headquarters_location": None,
                "network_status": None
            })
    return out




def enrich_affiliations(cms_cert_nums: list = None, care_names: list = None) -> list:
    """
    Returns:
      - If cms_cert_nums provided: [{cert_number, affiliate_name, location}]
      - Else: [{affiliate_name, location}]
    If GPT can’t map, we still return the input items with null location (and keep cert_number when given).
    """
    if cms_cert_nums:
        sys = (
            "Map each CMS certification number to facility name and location. "
            "Return strict JSON as {\"items\":[{\"cert_number\":\"...\",\"affiliate_name\":\"...\",\"location\":\"City, State\"}...]}. "
            "If unknown, include the cert_number with nulls."
        )
        user_payload = {"cert_numbers": cms_cert_nums}
    else:
        sys = (
            "Map each affiliation name to its location (City, State). "
            "Return strict JSON as {\"items\":[{\"affiliate_name\":\"...\",\"location\":\"City, State\"}...]}. "
            "If unknown, include the name with null location."
        )
        user_payload = {"affiliations": care_names or []}

    items = []
    try:
        resp = _get_oai_client().chat.completions.create(
            model=model_name,  # Azure deployment name
            temperature=0,
            max_tokens=800,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": sys},
                {"role": "user", "content": json.dumps(user_payload)}
            ],
        )

        data = json.loads(resp.choices[0].message.content)
        items = data.get("items", data if isinstance(data, list) else [])
    except Exception as e:
        print(f"[GPT][affiliations] fallback due to error: {e}")
        items = []

    out = []
    seen_keys = set()

    if cms_cert_nums:
        # normalize and fallback per cert
        for it in items:
            cert = it.get("cert_number")
            name = it.get("affiliate_name") or it.get("name")
            loc  = it.get("location")
            if cert:
                seen_keys.add(cert)
                out.append({"cert_number": cert, "affiliate_name": name, "location": loc})

        for cert in cms_cert_nums:
            if cert not in seen_keys:
                out.append({"cert_number": cert, "affiliate_name": None, "location": None})
    else:
        # names-only mode
        in_names = [n for n in (care_names or []) if n]
        norm_in = {n.strip().lower(): n.strip() for n in in_names}
        for it in items:
            name = (it.get("affiliate_name") or it.get("name") or "").strip()
            loc  = it.get("location")
            if name:
                seen_keys.add(name.lower())
                out.append({"affiliate_name": name, "location": loc})
        for key, orig in norm_in.items():
            if key not in seen_keys:
                out.append({"affiliate_name": orig, "location": None})
    return out

