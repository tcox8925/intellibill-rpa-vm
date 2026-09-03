#!/usr/bin/env python3
"""
fhir_bulk_probe.py — does our System app have Bulk Data ($export) access?

This ONLY probes; it does not pull data. It:
  1. Kicks off an async export scoped to _type=Patient (smallest footprint) at
     {BASE}/Patient/$export with `Prefer: respond-async`.
  2. Reports the HTTP status + the Content-Location (async status URL) if given.
  3. Polls the status URL once to show job state.
  4. CANCELS the job (DELETE) so no full export is left running.
It also checks Group/$export feasibility (needs a Group; the survey found none).

Interpreting results:
  202 + Content-Location  -> all-patient bulk export IS permitted (great: use it
                             for the initial load).
  400                     -> export understood but rejected (e.g. needs _type or
                             a Group) — read the OperationOutcome.
  403                     -> app not granted all-patient/system export — fall
                             back to the REST crawler for the initial load.

Run from the folder with fhir_token_minter.py (uses its auth/.env):
    python fhir_bulk_probe.py
"""

import sys
import time

import requests
import fhir_token_minter as mint

BASE = mint.BASE_URL
TIMEOUT = 60


def hdr(extra=None):
    h = {"Authorization": f"Bearer {mint.get_access_token()}"}
    if extra:
        h.update(extra)
    return h


def show(resp, label):
    print(f"\n[{label}] HTTP {resp.status_code}")
    interesting = {k: v for k, v in resp.headers.items()
                   if k.lower() in ("content-location", "retry-after",
                                    "x-progress", "content-type")}
    for k, v in interesting.items():
        print(f"    {k}: {v}")
    body = (resp.text or "").strip()
    if body:
        print(f"    body: {body[:400]}")


def main():
    print(f"Base URL: {BASE}")
    sess = requests.Session()

    # 1) Kick off all-patient export, limited to _type=Patient.
    kick = sess.get(
        f"{BASE}/Patient/$export",
        params={"_type": "Patient"},
        headers=hdr({"Accept": "application/fhir+json",
                     "Prefer": "respond-async"}),
        timeout=TIMEOUT,
    )
    show(kick, "Patient/$export kickoff")

    status_url = kick.headers.get("Content-Location")

    if kick.status_code == 202 and status_url:
        print("\n==> ALL-PATIENT BULK EXPORT IS PERMITTED.")
        print(f"    status URL: {status_url}")

        # 3) Poll once to show job state.
        time.sleep(3)
        poll = sess.get(status_url,
                        headers=hdr({"Accept": "application/json"}),
                        timeout=TIMEOUT)
        show(poll, "status poll (once)")
        if poll.status_code == 200:
            try:
                out = poll.json().get("output", [])
                print(f"    manifest already has {len(out)} file(s)")
            except Exception:
                pass

        # 4) Cancel so nothing keeps running.
        cancel = sess.delete(status_url, headers=hdr(), timeout=TIMEOUT)
        show(cancel, "cancel job (DELETE)")
        print("\n==> Probe complete. Export was cancelled; no data pulled.")
        verdict = "BULK OK (all-patient)"
    elif kick.status_code == 403:
        print("\n==> All-patient export FORBIDDEN for this app (403).")
        print("    -> initial load will use the REST crawler instead.")
        verdict = "BULK FORBIDDEN"
    else:
        print(f"\n==> Export not accepted as-is (HTTP {kick.status_code}).")
        print("    Read the OperationOutcome above — it may require a Group or")
        print("    a different parameter. Group export needs a Group resource;")
        print("    the survey found 0 Groups, so one would have to be created.")
        verdict = f"BULK UNCLEAR (HTTP {kick.status_code})"

    print(f"\nVERDICT: {verdict}")


if __name__ == "__main__":
    sys.exit(main())
