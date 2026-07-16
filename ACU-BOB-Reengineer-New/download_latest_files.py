#!/usr/bin/env python3
"""
download_latest_files.py
========================
Downloads the most recent ACU and BOB file for each active carrier
from Azure Blob Storage.

Scans TWO storage locations:
  Primary:  agilitydatadev001 / agilityops
            ACU: raw/agent_contract_update/acu_new_process/**
            BOB: raw/production_report/**

  Fallback: 834analyticsdatalake / 834analytics-dev
            ACU: raw/agent_contract_update/**
            (for carriers not found in primary)

Usage:
    python download_latest_files.py              # download all active carriers
    python download_latest_files.py --acu-only   # ACU only
    python download_latest_files.py --bob-only   # BOB only
    python download_latest_files.py --dry-run    # list what would be downloaded
"""

import os
import sys
import csv
import re
import argparse
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient

# ══════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════

RULES_CSV = os.path.join(os.path.dirname(__file__), "config", "ops_acu_bob_rules_matrix.csv")

KEYVAULT_NAME = os.getenv("KEY_VAULT_NAME", "")

# Primary: reengineered platform storage
PRIMARY_STORAGE = "834analyticsdatalake"
PRIMARY_CONTAINER = "834analytics-dev"
PRIMARY_PREFIXES = {
    "ACU": "raw/agent_contract_update/acu_new_process/",
    "BOB": "raw/production_report/",
}

# Fallback: legacy Synapse ADLS (for ACU files not in primary)
FALLBACK_STORAGE = "834analyticsdatalake"
FALLBACK_CONTAINER = "834analytics-dev"
FALLBACK_PREFIXES = {
    "ACU": "raw/agent_contract_update/",
}

SKIP_PATH_PATTERNS = ["results/", "exceptions/"]

LOCAL_BASE = r"C:\Users\poorn\Microsoft\Downloads\ACUBOB"
LOCAL_DIRS = {
    "ACU": os.path.join(LOCAL_BASE, "ACU"),
    "BOB": os.path.join(LOCAL_BASE, "BOB"),
}


# ══════════════════════════════════════════════════════════
#  AZURE AUTH
# ══════════════════════════════════════════════════════════

def get_blob_client(storage_account):
    """Authenticate to a storage account via Key Vault SP credentials."""
    kv_url = os.getenv("KEYVAULT_URL", "")
    account_url = f"https://{storage_account}.blob.core.windows.net"

    print(f"  🔐 Connecting to {storage_account}...")
    secret_client = SecretClient(vault_url=kv_url, credential=DefaultAzureCredential())
    cid = secret_client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    csecret = secret_client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tid = secret_client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value

    cred = ClientSecretCredential(tid, cid, csecret)
    client = BlobServiceClient(account_url=account_url, credential=cred)
    print(f"  ✅ Authenticated to {storage_account}")
    return client


# ══════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════

_BLANK = {"", "nan", "None", "NA", "none", "na"}

def _clean(val):
    if val is None:
        return ""
    return str(val).strip().replace("'", "")


def load_rules(csv_path):
    """Load active carrier rules from the CSV matrix."""
    rules = []
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            af = _clean(row.get("active_flag", ""))
            if af.upper() != "Y":
                continue
            pt = _clean(row.get("process_type", ""))
            if pt not in ("ACU", "BOB"):
                continue
            pattern = _clean(row.get("file_naming_pattern", ""))
            if not pattern:
                continue
            rules.append({
                "carrier_name": _clean(row.get("carrier_name", "")),
                "process_type": pt,
                "contract_type": _clean(row.get("contract_type", "")),
                "file_naming_pattern": pattern.lower(),
                "date_format": _clean(row.get("date_format", "MMDDYYYY")),
                "carrier_id": _clean(row.get("carrier_id", "")),
            })
    return rules


def extract_date_from_filename(filename, date_format_hint="MMDDYYYY"):
    """Extract a date from a filename. Returns datetime or None."""
    base = os.path.splitext(os.path.basename(filename))[0]

    for m in re.findall(r'(\d{8})', base):
        if date_format_hint == "MMDDYYYY":
            formats = ["%m%d%Y", "%Y%m%d"]
        else:
            formats = ["%Y%m%d", "%m%d%Y"]
        for fmt in formats:
            try:
                dt = datetime.strptime(m, fmt)
                if 2020 <= dt.year <= 2030:
                    return dt
            except ValueError:
                continue

    for m in re.findall(r'(\d{2}-\d{2}-\d{4})', base):
        try:
            dt = datetime.strptime(m, "%m-%d-%Y")
            if 2020 <= dt.year <= 2030:
                return dt
        except ValueError:
            continue

    return None


def scan_blobs(blob_client, container_name, prefix):
    """List all blobs under a prefix. Returns list of dicts."""
    container = blob_client.get_container_client(container_name)
    files = []
    for blob in container.list_blobs(name_starts_with=prefix):
        path = blob.name
        if path.endswith("/") or path.endswith(".zip") or path.endswith(".log"):
            continue
        if any(skip in path for skip in SKIP_PATH_PATTERNS):
            continue
        fname = os.path.basename(path)
        if "." not in fname:
            continue
        files.append({
            "blob_path": path,
            "file_name": fname.lower(),
            "file_name_original": fname,
            "size": blob.size,
            "last_modified": blob.last_modified,
        })
    return files


def pick_latest(file_list):
    """Pick the most recent file. Prefer filename date, fall back to last_modified."""
    if not file_list:
        return None
    def sort_key(f):
        fd = f.get("file_date") or datetime.min
        lm = f.get("last_modified") or datetime.min
        if hasattr(lm, "tzinfo") and lm.tzinfo is not None:
            lm = lm.replace(tzinfo=None)
        return (fd, lm)
    return max(file_list, key=sort_key)


def download_blob(blob_client, container_name, blob_path, local_path):
    """Download a single blob to a local file."""
    container = blob_client.get_container_client(container_name)
    bc = container.get_blob_client(blob_path)
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    with open(local_path, "wb") as f:
        data = bc.download_blob()
        data.readinto(f)


# ══════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Download latest ACU/BOB files per carrier")
    parser.add_argument("--acu-only", action="store_true")
    parser.add_argument("--bob-only", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="List files without downloading")
    parser.add_argument("--rules", default=RULES_CSV)
    args = parser.parse_args()

    process_types = ["ACU", "BOB"]
    if args.acu_only:
        process_types = ["ACU"]
    elif args.bob_only:
        process_types = ["BOB"]

    # ── Load rules ──
    print(f"\n{'='*65}")
    print(f"  Download Latest ACU/BOB Files")
    print(f"{'='*65}")

    all_rules = load_rules(args.rules)
    rules = [r for r in all_rules if r["process_type"] in process_types]
    print(f"  📋 Loaded {len(rules)} active rules")
    for pt in process_types:
        print(f"     {pt}: {sum(1 for r in rules if r['process_type'] == pt)} carriers")

    # ── Connect to Azure ──
    primary_client = get_blob_client(PRIMARY_STORAGE)

    # Only connect to fallback if we need ACU
    fallback_client = None
    if "ACU" in process_types:
        try:
            fallback_client = get_blob_client(FALLBACK_STORAGE)
        except Exception as e:
            print(f"  ⚠️  Fallback storage ({FALLBACK_STORAGE}) not available: {e}")

    # ── Scan primary storage ──
    primary_blobs = {}
    for pt in process_types:
        prefix = PRIMARY_PREFIXES[pt]
        print(f"\n  📁 Scanning primary ({PRIMARY_STORAGE}/{PRIMARY_CONTAINER}): {prefix}")
        files = scan_blobs(primary_client, PRIMARY_CONTAINER, prefix)
        primary_blobs[pt] = files
        print(f"     Found {len(files)} files")

    # ── Scan fallback storage (ACU only, for missing carriers) ──
    fallback_blobs = {}
    if fallback_client and "ACU" in process_types:
        prefix = FALLBACK_PREFIXES.get("ACU", "")
        if prefix:
            print(f"\n  📁 Scanning fallback ({FALLBACK_STORAGE}/{FALLBACK_CONTAINER}): {prefix}")
            files = scan_blobs(fallback_client, FALLBACK_CONTAINER, prefix)
            fallback_blobs["ACU"] = files
            print(f"     Found {len(files)} files")

    # ── Match and download ──
    for pt in process_types:
        pt_rules = sorted(
            [r for r in rules if r["process_type"] == pt],
            key=lambda r: r["carrier_name"]
        )
        local_dir = LOCAL_DIRS[pt]

        print(f"\n{'─'*65}")
        print(f"  {pt} — {len(pt_rules)} carriers → {local_dir}")
        print(f"{'─'*65}")

        found = 0
        missing = 0
        downloaded = 0
        already_downloaded = set()  # deduplicate multi-sheet carriers

        for rule in pt_rules:
            name = rule["carrier_name"]
            pattern = rule["file_naming_pattern"]
            date_fmt = rule["date_format"]

            # Search primary
            matches = []
            for blob in primary_blobs.get(pt, []):
                if blob["file_name"].startswith(pattern):
                    fd = extract_date_from_filename(blob["file_name_original"], date_fmt)
                    matches.append({**blob, "file_date": fd, "_source": "primary"})

            # If not found, search fallback
            source_label = ""
            if not matches and pt in fallback_blobs:
                for blob in fallback_blobs[pt]:
                    if blob["file_name"].startswith(pattern):
                        fd = extract_date_from_filename(blob["file_name_original"], date_fmt)
                        matches.append({**blob, "file_date": fd, "_source": "fallback"})
                if matches:
                    source_label = " [ADLS]"

            if not matches:
                print(f"    ❌ {name:<35} — no files found (pattern: {pattern})")
                missing += 1
                continue

            latest = pick_latest(matches)
            found += 1

            date_str = ""
            if latest.get("file_date"):
                date_str = latest["file_date"].strftime("%Y-%m-%d")
            elif latest.get("last_modified"):
                date_str = latest["last_modified"].strftime("%Y-%m-%d")

            size_kb = (latest.get("size", 0) or 0) / 1024
            fname = latest["file_name_original"]

            if args.dry_run:
                print(f"    ✅ {name:<35} → {fname} ({date_str}, {size_kb:,.0f} KB){source_label}")
            else:
                local_path = os.path.join(local_dir, fname)

                # Deduplicate: multi-sheet carriers (SMA, HCSC) share one file
                blob_key = (latest["blob_path"], latest["_source"])
                if blob_key in already_downloaded:
                    print(f"    ✅ {name:<35} → {fname} (already downloaded){source_label}")
                    continue

                try:
                    if latest["_source"] == "fallback":
                        download_blob(fallback_client, FALLBACK_CONTAINER,
                                      latest["blob_path"], local_path)
                    else:
                        download_blob(primary_client, PRIMARY_CONTAINER,
                                      latest["blob_path"], local_path)
                    downloaded += 1
                    already_downloaded.add(blob_key)
                    print(f"    ✅ {name:<35} → {fname} ({date_str}, {size_kb:,.0f} KB){source_label}")
                except Exception as e:
                    print(f"    ⚠️  {name:<35} → FAILED: {e}")

        print(f"\n  {pt} summary: {found} found, {missing} missing", end="")
        if not args.dry_run:
            print(f", {downloaded} unique files downloaded")
        else:
            print(" (dry run)")

    print(f"\n{'='*65}")
    print(f"  Done!")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    main()
