#!/usr/bin/env python3
"""
fhir_pipeline.py — pull Practice Fusion FHIR data to flattened CSVs.

Two modes (initial load + incremental), sharing auth, flattening, and adaptive
rate handling.

  BULK (initial full load) — uses $export; fewest requests:
      python fhir_pipeline.py bulk
      python fhir_pipeline.py bulk --type Patient,Coverage,Encounter
      python fhir_pipeline.py bulk --since 2026-07-01T00:00:00Z

  CRAWL (incremental deltas / targeted) — paginated REST with _lastUpdated:
      python fhir_pipeline.py crawl                 # full crawl (uses watermark if present)
      python fhir_pipeline.py crawl --since 2026-07-01T00:00:00Z
      python fhir_pipeline.py crawl --type Patient,Coverage
      python fhir_pipeline.py crawl --patient <id>  # targeted, one patient

Output: ./fhir_out/<Resource>.csv  (+ _watermark.json, _run_log.txt)

Auth/rate:
  - Reuses fhir_token_minter (run from its folder; .env supplies client id/base).
  - Adaptive: honors HTTP 429 + Retry-After, exponential backoff on 429/503,
    a gentle default pace between calls, and refreshes the token on 401.
  - Rate limits aren't documented by Practice Fusion; this self-tunes. If they
    give you a number, set PACE_SECONDS accordingly.

Deps:  pip install requests pandas
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests
import pandas as pd

import fhir_token_minter as mint

BASE = mint.BASE_URL
OUT_DIR = os.getenv("PF_OUT_DIR", "fhir_out")
PACE_SECONDS = float(os.getenv("PF_PACE", "0.2"))   # ~5 req/sec default
MAX_RETRIES = 6
PAGE_SIZE = int(os.getenv("PF_PAGE", "200"))
TIMEOUT = 120

# Resources our app is scoped for (returned data in the survey).
SCOPED = [
    "Patient", "Coverage", "Encounter", "Condition", "Procedure", "Observation",
    "MedicationRequest", "MedicationDispense", "DiagnosticReport",
    "DocumentReference", "AllergyIntolerance", "Immunization", "CarePlan",
    "CareTeam", "Goal", "Device", "ServiceRequest", "Provenance",
    "RelatedPerson", "Organization", "Location", "Practitioner",
]

_session = requests.Session()


# --------------------------------------------------------------------------- #
# auth + adaptive HTTP
# --------------------------------------------------------------------------- #
def _headers(accept="application/fhir+json", extra=None, force=False):
    h = {"Authorization": f"Bearer {mint.get_access_token(force=force)}",
         "Accept": accept}
    if extra:
        h.update(extra)
    return h


def http(method, url, *, params=None, accept="application/fhir+json",
         extra_headers=None, stream=False):
    """One request with adaptive rate/error handling. Returns Response."""
    delay = 1.0
    for attempt in range(MAX_RETRIES):
        time.sleep(PACE_SECONDS)  # gentle default pace
        resp = _session.request(
            method, url, params=params, stream=stream, timeout=TIMEOUT,
            headers=_headers(accept, extra_headers, force=(attempt > 0 and
                             _last_status == 401)))
        globals()["_last_status"] = resp.status_code

        if resp.status_code == 429:
            wait = float(resp.headers.get("Retry-After", delay))
            _log(f"429 rate-limited; sleeping {wait:.0f}s")
            time.sleep(wait)
            delay = min(delay * 2, 60)
            continue
        if resp.status_code in (500, 502, 503, 504):
            _log(f"{resp.status_code} server error; backoff {delay:.0f}s")
            time.sleep(delay)
            delay = min(delay * 2, 60)
            continue
        if resp.status_code == 401 and attempt == 0:
            _log("401; refreshing token and retrying")
            continue
        return resp
    return resp  # last response after retries exhausted


_last_status = None


# --------------------------------------------------------------------------- #
# flattening + output
# --------------------------------------------------------------------------- #
def flatten(records):
    if not records:
        return pd.DataFrame()
    df = pd.json_normalize(records, max_level=3)
    # promote id to a leading _pk column for clarity
    for col in df.columns:
        df[col] = df[col].apply(
            lambda v: json.dumps(v, ensure_ascii=False)
            if isinstance(v, (dict, list)) else v)
    if "id" in df.columns:
        df.insert(0, "_pk", df["id"])
    return df


def write_csv(rtype, records):
    os.makedirs(OUT_DIR, exist_ok=True)
    df = flatten(records)
    path = os.path.join(OUT_DIR, f"{rtype}.csv")
    df.to_csv(path, index=False)
    return path, len(df)


def max_last_updated(records):
    best = None
    for r in records:
        lu = (r.get("meta") or {}).get("lastUpdated")
        if lu and (best is None or lu > best):
            best = lu
    return best


def _log(msg):
    os.makedirs(OUT_DIR, exist_ok=True)
    line = f"{datetime.now(timezone.utc).isoformat()}  {msg}"
    print("   " + msg)
    with open(os.path.join(OUT_DIR, "_run_log.txt"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_watermarks():
    p = os.path.join(OUT_DIR, "_watermark.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    return {}


def save_watermarks(wm):
    os.makedirs(OUT_DIR, exist_ok=True)
    json.dump(wm, open(os.path.join(OUT_DIR, "_watermark.json"), "w",
                       encoding="utf-8"), indent=2)


# --------------------------------------------------------------------------- #
# BULK  ($export -> poll -> download NDJSON -> csv)
# --------------------------------------------------------------------------- #
def run_bulk(types, since, limit=None):
    params = {}
    if types:
        params["_type"] = ",".join(types)
    if since:
        params["_since"] = since

    _log(f"kicking off $export  params={params or '(all compartment types)'}")
    kick = http("GET", f"{BASE}/Patient/$export", params=params,
                extra_headers={"Prefer": "respond-async"})
    if kick.status_code != 202 or "Content-Location" not in kick.headers:
        _log(f"export not accepted: HTTP {kick.status_code}: {kick.text[:300]}")
        return
    status_url = kick.headers["Content-Location"]
    _log(f"job accepted: {status_url}")

    # poll until 200 with manifest
    while True:
        poll = http("GET", status_url, accept="application/json")
        if poll.status_code == 202:
            prog = poll.headers.get("X-Progress", "in progress")
            wait = float(poll.headers.get("Retry-After", 5))
            _log(f"  ...{prog}; poll again in {wait:.0f}s")
            time.sleep(wait)
            continue
        if poll.status_code == 200:
            manifest = poll.json()
            break
        _log(f"poll failed HTTP {poll.status_code}: {poll.text[:300]}")
        return

    requires_token = manifest.get("requiresAccessToken", True)
    outputs = manifest.get("output", [])
    errors = manifest.get("error", [])
    _log(f"manifest ready: {len(outputs)} file(s), {len(errors)} error file(s)")

    # group output files by resource type, download + accumulate
    by_type = {}
    for item in outputs:
        by_type.setdefault(item.get("type", "Unknown"), []).append(item["url"])

    for rtype, urls in by_type.items():
        records = []
        for url in urls:
            if limit and len(records) >= limit:
                break
            r = http("GET", url, accept="application/fhir+ndjson", stream=True)
            if r.status_code != 200:
                _log(f"  {rtype}: file HTTP {r.status_code}, skipping")
                continue
            for line in r.iter_lines():
                if line:
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        pass
                if limit and len(records) >= limit:
                    r.close()          # stop the stream early
                    break
        path, n = write_csv(rtype, records)
        _log(f"  {rtype}: {n} rows -> {path}")

    # cancel/clean the completed job
    http("DELETE", status_url)
    _log("bulk load complete; job cleaned up")


# --------------------------------------------------------------------------- #
# CRAWL  (paginated REST, _lastUpdated deltas, or --patient targeted)
# --------------------------------------------------------------------------- #
def crawl_resource(rtype, since=None, patient=None, limit=None):
    params = {"_count": min(PAGE_SIZE, limit) if limit else PAGE_SIZE}
    if since:
        params["_lastUpdated"] = f"gt{since}"
    if patient and rtype != "Patient":
        params["patient"] = patient
    url = f"{BASE}/{rtype}"
    records, page = [], 0
    while url:
        r = http("GET", url, params=params if page == 0 else None)
        if r.status_code != 200:
            _log(f"  {rtype}: HTTP {r.status_code} ({r.text[:120]})")
            break
        bundle = r.json()
        records.extend(e.get("resource", e) for e in (bundle.get("entry") or []))
        # follow the server's own next link verbatim
        if limit and len(records) >= limit:
            records = records[:limit]
            break
        nxt = next((l["url"] for l in (bundle.get("link") or [])
                    if l.get("relation") == "next"), None)
        url, params, page = nxt, None, page + 1
    return records


def run_crawl(types, since, patient, limit=None):
    wm = load_watermarks()
    for rtype in types:
        eff_since = since or (wm.get(rtype) if not patient else None)
        recs = crawl_resource(rtype, since=eff_since, patient=patient, limit=limit)
        path, n = write_csv(rtype, recs)
        newest = max_last_updated(recs)
        if newest and not patient:
            wm[rtype] = newest
        tag = f"since {eff_since}" if eff_since else ("patient " + patient if patient else "full")
        _log(f"  {rtype}: {n} rows ({tag}) -> {path}")
    if not patient:
        save_watermarks(wm)
        _log("watermarks updated")


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="mode", required=True)

    b = sub.add_parser("bulk", help="initial full load via $export")
    b.add_argument("--type", help="comma list; default = all compartment types")
    b.add_argument("--since", help="_since ISO timestamp (incremental bulk)")
    b.add_argument("--limit", type=int, help="TESTING: cap rows written per resource (client-side)")

    c = sub.add_parser("crawl", help="paginated REST; incremental/targeted")
    c.add_argument("--type", help="comma list; default = all scoped resources")
    c.add_argument("--since", help="_lastUpdated gt ISO timestamp")
    c.add_argument("--patient", help="targeted: one patient id")
    c.add_argument("--limit", type=int, help="TESTING: cap rows per resource")

    args = ap.parse_args()
    print(f"Base URL: {BASE}\nOutput dir: {OUT_DIR}\n")

    if args.mode == "bulk":
        types = [t.strip() for t in args.type.split(",")] if args.type else None
        run_bulk(types, args.since, args.limit)
    else:
        types = [t.strip() for t in args.type.split(",")] if args.type else SCOPED
        run_crawl(types, args.since, args.patient, args.limit)

    print("\nDone.")


if __name__ == "__main__":
    sys.exit(main())
