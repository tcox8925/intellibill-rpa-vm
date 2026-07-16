#!/usr/bin/env python3
"""
qa_validate.py
==============
QA validation for ACU and BOB pipeline results.

Validates:
  1. MAPPING ACCURACY — raw file column values match results via load matrix
  2. AGENT COMPLETENESS — every agent in raw file appears in results + exceptions

Usage:
    python qa_validate.py ^
        --raw-acu  C:\\Users\\poorn\\Microsoft\\Downloads\\ACUBOB\\ACU ^
        --raw-bob  C:\\Users\\poorn\\Microsoft\\Downloads\\ACUBOB\\BOB ^
        --results-acu  path\\to\\acu_results.csv ^
        --exceptions-acu  path\\to\\acu_exceptions.csv ^
        --results-bob  path\\to\\bob_results.csv ^
        --exceptions-bob  path\\to\\bob_exceptions.csv ^
        --rules  config\\ops_acu_bob_rules_matrix.csv ^
        --load-matrix  config\\ops_acu_bob_load_matrix.csv

    # Run just BOB:
    python qa_validate.py --bob-only --raw-bob ... --results-bob ... --exceptions-bob ...

    # Run just ACU:
    python qa_validate.py --acu-only --raw-acu ... --results-acu ... --exceptions-acu ...
"""

import os
import sys
import csv
import re
import io
import argparse
import warnings
from collections import defaultdict
from datetime import datetime

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore", category=UserWarning)

# ══════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════

DEFAULT_RULES = os.path.join(os.path.dirname(__file__), "config", "ops_acu_bob_rules_matrix.csv")
DEFAULT_LOAD = os.path.join(os.path.dirname(__file__), "config", "ops_acu_bob_load_matrix.csv")

_BLANK = {"", "nan", "none", "na", "nat", "null", "None"}


# ══════════════════════════════════════════════════════════
#  LOAD CONFIGS
# ══════════════════════════════════════════════════════════

def _clean(val):
    if val is None:
        return ""
    return str(val).strip().replace("'", "")


def load_rules(path):
    """Load rules matrix into a list of dicts."""
    rules = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rules.append({k: _clean(v) for k, v in row.items()})
    return rules


def load_mappings(path):
    """Load load matrix into a list of dicts."""
    mappings = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            mappings.append({k: _clean(v) for k, v in row.items()})
    return mappings


def get_carrier_rules(rules, process_type):
    """Get active rules for a process type."""
    return [r for r in rules
            if r.get("process_type") == process_type
            and r.get("active_flag", "").upper() == "Y"]


def get_carrier_mappings(mappings, carrier_name, process_type):
    """Get column mappings for a carrier."""
    return [m for m in mappings
            if m.get("carrier_name", "").strip() == carrier_name.strip()
            and m.get("process_type") == process_type
            and m.get("mapping", "").strip() not in ("", "NA", "nan", "None")
            and m.get("database_column", "").strip() not in ("", "NA", "nan", "None")]


# ══════════════════════════════════════════════════════════
#  FILE READING
# ══════════════════════════════════════════════════════════

def find_raw_file(raw_dir, file_pattern, file_format="csv"):
    """Find the raw file matching a carrier's file_naming_pattern."""
    if not raw_dir or not os.path.isdir(raw_dir):
        return None

    pattern_lower = file_pattern.lower()
    matches = []

    for fname in os.listdir(raw_dir):
        if fname.lower().startswith(pattern_lower):
            matches.append(os.path.join(raw_dir, fname))

    if not matches:
        return None

    # Return the most recently modified
    return max(matches, key=os.path.getmtime)


def read_raw_file(filepath, rule):
    """Read a raw file into a DataFrame based on rule config."""
    ext = os.path.splitext(filepath)[1].lower()
    skip_rows = int(rule.get("ignore_header_rows", 0) or 0)
    sheet_name = rule.get("sheet_name", "").strip()

    try:
        if ext in (".xlsx", ".xls"):
            xl = pd.ExcelFile(filepath)
            if sheet_name and sheet_name in xl.sheet_names:
                df = xl.parse(sheet_name, skiprows=skip_rows, dtype=str)
            elif sheet_name:
                # Fuzzy match
                matched = [s for s in xl.sheet_names if sheet_name.lower() in s.lower()]
                if matched:
                    df = xl.parse(matched[0], skiprows=skip_rows, dtype=str)
                else:
                    df = xl.parse(0, skiprows=skip_rows, dtype=str)
            else:
                df = xl.parse(0, skiprows=skip_rows, dtype=str)
        else:
            # CSV/TSV
            delim = "\t" if rule.get("file_delimiter") == "tab" else ","
            df = pd.read_csv(filepath, sep=delim, dtype=str, skiprows=skip_rows,
                             encoding=rule.get("file_encoding", "utf-8") or "utf-8",
                             on_bad_lines="skip")

        df.columns = df.columns.str.strip()
        df = df.dropna(how="all")
        return df

    except Exception as e:
        print(f"      ⚠️  Failed to read {os.path.basename(filepath)}: {e}")
        return None


# ══════════════════════════════════════════════════════════
#  VALIDATION 1: COLUMN MAPPING ACCURACY
# ══════════════════════════════════════════════════════════

def validate_mapping(raw_df, results_df, carrier_mappings, carrier_name):
    """
    For each mapping (file_column → db_column), verify that values
    in the results match what's in the raw file.

    Returns list of issues.
    """
    issues = []
    checks_passed = 0
    checks_total = 0

    raw_cols_lower = {c.lower(): c for c in raw_df.columns}

    for m in carrier_mappings:
        file_col = m["mapping"].strip()
        db_col = m["database_column"].strip()

        # Handle slash notation (e.g. "Sub Producer NPN/Producer NPN")
        if "/" in file_col:
            file_col = file_col.split("/")[0].strip()

        file_col_lower = file_col.lower()
        checks_total += 1

        # Check if file column exists in raw
        if file_col_lower not in raw_cols_lower:
            issues.append({
                "type": "MISSING_RAW_COLUMN",
                "db_col": db_col,
                "file_col": file_col,
                "detail": f"Column '{file_col}' not found in raw file"
            })
            continue

        # Check if db column exists in results
        if db_col not in results_df.columns:
            issues.append({
                "type": "MISSING_RESULT_COLUMN",
                "db_col": db_col,
                "file_col": file_col,
                "detail": f"Column '{db_col}' not in results"
            })
            continue

        # Compare value sets (non-null values)
        actual_raw_col = raw_cols_lower[file_col_lower]
        raw_vals = set(raw_df[actual_raw_col].dropna().astype(str).str.strip()) - _BLANK
        result_vals = set(results_df[db_col].dropna().astype(str).str.strip()) - _BLANK

        if not raw_vals:
            checks_passed += 1
            continue

        # Check overlap: result values should be a subset of raw values
        # (some raw values may be filtered out, but results shouldn't invent values)
        invented = result_vals - raw_vals
        # Allow for normalization differences (case, whitespace)
        raw_vals_normalized = {v.lower().strip() for v in raw_vals}
        invented_real = {v for v in invented
                         if v.lower().strip() not in raw_vals_normalized}

        if invented_real and len(invented_real) > 5:
            issues.append({
                "type": "VALUE_MISMATCH",
                "db_col": db_col,
                "file_col": file_col,
                "detail": f"{len(invented_real)} values in results not in raw (sample: {list(invented_real)[:3]})"
            })
        else:
            checks_passed += 1

    return issues, checks_passed, checks_total


# ══════════════════════════════════════════════════════════
#  VALIDATION 2: AGENT COMPLETENESS
# ══════════════════════════════════════════════════════════

def validate_completeness(raw_df, results_df, exceptions_df, rule, carrier_mappings):
    """
    Every agent identity in the raw file should appear in
    results + exceptions. No silent drops.

    Returns dict with counts and any missing agents.
    """
    # Determine which column in the raw file holds the identity
    pif = rule.get("primary_identity_field", "NPN").upper()
    fif = rule.get("fallback_identity_field", "").upper()

    # Find the raw file column for the identity
    identity_file_col = None
    identity_db_col = None

    if pif == "NPN":
        identity_db_col = "agent_npn"
    elif pif == "WR":
        identity_db_col = "agent_writing_num"
    elif pif == "NAME":
        identity_db_col = "carrier_agent_name"
    else:
        identity_db_col = "agent_npn"

    # Find what raw column maps to the identity db column
    for m in carrier_mappings:
        if m["database_column"].strip() == identity_db_col:
            identity_file_col = m["mapping"].strip()
            if "/" in identity_file_col:
                identity_file_col = identity_file_col.split("/")[0].strip()
            break

    if not identity_file_col:
        return {
            "status": "SKIP",
            "reason": f"No mapping found for identity column '{identity_db_col}'",
            "raw_agents": 0, "result_agents": 0, "exception_agents": 0, "missing": []
        }

    # Find the column in raw (case-insensitive)
    raw_cols_lower = {c.lower(): c for c in raw_df.columns}
    actual_col = raw_cols_lower.get(identity_file_col.lower())

    if actual_col is None:
        return {
            "status": "SKIP",
            "reason": f"Column '{identity_file_col}' not in raw file",
            "raw_agents": 0, "result_agents": 0, "exception_agents": 0, "missing": []
        }

    # Extract unique agent identities from raw
    raw_agents = set(
        raw_df[actual_col].dropna().astype(str).str.strip().str.lower()
    ) - {v.lower() for v in _BLANK}

    # Extract from results
    carrier_name = rule.get("carrier_name", "")
    carrier_id = rule.get("carrier_id", "")

    result_agents = set()
    if results_df is not None and not results_df.empty:
        # Filter results to this carrier
        cr = results_df[
            (results_df["carrier_name"].astype(str).str.strip() == carrier_name) |
            (results_df["carrier_id"].astype(str).str.strip().str.replace("'", "") == carrier_id)
        ]
        if identity_db_col in cr.columns:
            result_agents = set(
                cr[identity_db_col].dropna().astype(str).str.strip().str.lower()
            ) - {v.lower() for v in _BLANK}

    # Extract from exceptions
    exception_agents = set()
    if exceptions_df is not None and not exceptions_df.empty:
        ce = exceptions_df[
            (exceptions_df["carrier_name"].astype(str).str.strip() == carrier_name) |
            (exceptions_df["carrier_id"].astype(str).str.strip().str.replace("'", "") == carrier_id)
        ]
        if identity_db_col in ce.columns:
            exception_agents = set(
                ce[identity_db_col].dropna().astype(str).str.strip().str.lower()
            ) - {v.lower() for v in _BLANK}

    # Find missing: in raw but not in results or exceptions
    accounted = result_agents | exception_agents
    missing = raw_agents - accounted

    return {
        "status": "PASS" if not missing else "FAIL",
        "raw_agents": len(raw_agents),
        "result_agents": len(result_agents),
        "exception_agents": len(exception_agents),
        "accounted": len(accounted),
        "missing_count": len(missing),
        "missing": sorted(list(missing))[:20],  # cap at 20 samples
        "identity_field": f"{pif} ({identity_file_col} → {identity_db_col})",
    }


# ══════════════════════════════════════════════════════════
#  MAIN QA RUNNER
# ══════════════════════════════════════════════════════════

def run_qa(process_type, raw_dir, results_path, exceptions_path,
           rules, all_mappings, skip_multi_sheet=False):
    """Run QA for one process type (ACU or BOB)."""

    carrier_rules = get_carrier_rules(rules, process_type)

    print(f"\n{'='*70}")
    print(f"  QA VALIDATION — {process_type}")
    print(f"  Raw files:   {raw_dir}")
    print(f"  Results:     {results_path}")
    print(f"  Exceptions:  {exceptions_path}")
    print(f"  Carriers:    {len(carrier_rules)} active rules")
    print(f"{'='*70}")

    # Load results and exceptions
    results_df = None
    if results_path and os.path.exists(results_path):
        results_df = pd.read_csv(results_path, dtype=str, on_bad_lines="skip")
        print(f"  📊 Results: {len(results_df):,} rows, {len(results_df.columns)} cols")
    else:
        print(f"  ⚠️  Results file not found: {results_path}")

    exceptions_df = None
    if exceptions_path and os.path.exists(exceptions_path):
        exceptions_df = pd.read_csv(exceptions_path, dtype=str, on_bad_lines="skip")
        print(f"  📊 Exceptions: {len(exceptions_df):,} rows")
    else:
        print(f"  ⚠️  Exceptions file not found: {exceptions_path}")

    # Track overall results
    total_carriers = 0
    mapping_pass = 0
    mapping_fail = 0
    completeness_pass = 0
    completeness_fail = 0
    completeness_skip = 0
    all_issues = []

    # Skip multi-sheet sub-carriers (SMA, HCSC) — they share one file
    seen_patterns = set()

    for rule in sorted(carrier_rules, key=lambda r: r.get("carrier_name", "")):
        carrier_name = rule.get("carrier_name", "")
        pattern = rule.get("file_naming_pattern", "")
        custom_reader = rule.get("custom_reader_name", "").strip()

        # Skip multi-sheet sub-carriers (they need the reader to split)
        if custom_reader in ("read_sma_bob", "read_sma", "read_hcsc_bob", "read_hcsc"):
            if pattern in seen_patterns:
                continue  # Already processed parent
            seen_patterns.add(pattern)
            # Can still validate the parent file for basic checks

        # Find raw file
        raw_path = find_raw_file(raw_dir, pattern)
        if not raw_path:
            print(f"\n  ❌ {carrier_name}: raw file not found (pattern: {pattern})")
            continue

        total_carriers += 1
        raw_df = read_raw_file(raw_path, rule)
        if raw_df is None or raw_df.empty:
            print(f"\n  ⚠️  {carrier_name}: raw file empty or unreadable")
            continue

        carrier_maps = get_carrier_mappings(all_mappings, carrier_name, process_type)

        print(f"\n  {'─'*60}")
        print(f"  {carrier_name}")
        print(f"  File: {os.path.basename(raw_path)} ({len(raw_df):,} rows, {len(raw_df.columns)} cols)")
        print(f"  Mappings: {len(carrier_maps)} columns")

        # ── TEST 1: Mapping Accuracy ──
        if results_df is not None and carrier_maps:
            issues, passed, total = validate_mapping(raw_df, results_df, carrier_maps, carrier_name)

            if issues:
                mapping_fail += 1
                print(f"  📋 Mapping: {passed}/{total} passed")
                for iss in issues:
                    print(f"     ⚠️  [{iss['type']}] {iss['db_col']} ← {iss['file_col']}: {iss['detail']}")
                all_issues.append({"carrier": carrier_name, "test": "mapping", "issues": issues})
            else:
                mapping_pass += 1
                print(f"  📋 Mapping: ✅ {passed}/{total} passed")

        # ── TEST 2: Agent Completeness ──
        comp = validate_completeness(raw_df, results_df, exceptions_df, rule, carrier_maps)

        if comp["status"] == "SKIP":
            completeness_skip += 1
            print(f"  🔗 Completeness: ⏭️  {comp['reason']}")
        elif comp["status"] == "PASS":
            completeness_pass += 1
            print(f"  🔗 Completeness: ✅ {comp['raw_agents']} agents → "
                  f"{comp['result_agents']} results + {comp['exception_agents']} exceptions = "
                  f"{comp['accounted']} accounted ({comp['identity_field']})")
        else:
            completeness_fail += 1
            print(f"  🔗 Completeness: ❌ {comp['missing_count']} MISSING agents out of {comp['raw_agents']}")
            print(f"     {comp['result_agents']} in results + {comp['exception_agents']} in exceptions = "
                  f"{comp['accounted']} accounted ({comp['identity_field']})")
            if comp["missing"]:
                sample = comp["missing"][:10]
                print(f"     Missing sample: {sample}")
            all_issues.append({
                "carrier": carrier_name, "test": "completeness",
                "missing_count": comp["missing_count"],
                "missing_sample": comp["missing"][:10]
            })

    # ── Summary ──
    print(f"\n{'='*70}")
    print(f"  {process_type} QA SUMMARY")
    print(f"{'='*70}")
    print(f"  Carriers tested:      {total_carriers}")
    print(f"  Mapping:              {mapping_pass} pass, {mapping_fail} fail")
    print(f"  Completeness:         {completeness_pass} pass, {completeness_fail} fail, {completeness_skip} skip")

    if all_issues:
        print(f"\n  ⚠️  {len(all_issues)} ISSUES FOUND:")
        for iss in all_issues:
            if iss["test"] == "mapping":
                print(f"     {iss['carrier']}: {len(iss['issues'])} mapping issues")
            else:
                print(f"     {iss['carrier']}: {iss['missing_count']} missing agents")
    else:
        print(f"\n  ✅ ALL CHECKS PASSED")

    return all_issues


# ══════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="QA validation for ACU/BOB pipeline results")

    parser.add_argument("--raw-acu", help="Directory containing raw ACU carrier files")
    parser.add_argument("--raw-bob", help="Directory containing raw BOB carrier files")
    parser.add_argument("--results-acu", help="Path to ACU results CSV")
    parser.add_argument("--results-bob", help="Path to BOB results CSV")
    parser.add_argument("--exceptions-acu", help="Path to ACU exceptions CSV")
    parser.add_argument("--exceptions-bob", help="Path to BOB exceptions CSV")
    parser.add_argument("--rules", default=DEFAULT_RULES, help="Path to rules matrix CSV")
    parser.add_argument("--load-matrix", default=DEFAULT_LOAD, help="Path to load matrix CSV")
    parser.add_argument("--acu-only", action="store_true")
    parser.add_argument("--bob-only", action="store_true")

    args = parser.parse_args()

    print(f"\n{'='*70}")
    print(f"  ACU/BOB QA Validation")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")

    # Load configs
    rules = load_rules(args.rules)
    all_mappings = load_mappings(args.load_matrix)
    print(f"  📋 Rules: {len(rules)} total")
    print(f"  📋 Mappings: {len(all_mappings)} total")

    all_issues = []

    # Run ACU
    if not args.bob_only and args.raw_acu:
        issues = run_qa(
            "ACU", args.raw_acu,
            args.results_acu, args.exceptions_acu,
            rules, all_mappings
        )
        all_issues.extend(issues)

    # Run BOB
    if not args.acu_only and args.raw_bob:
        issues = run_qa(
            "BOB", args.raw_bob,
            args.results_bob, args.exceptions_bob,
            rules, all_mappings
        )
        all_issues.extend(issues)

    # Final verdict
    print(f"\n{'='*70}")
    if all_issues:
        print(f"  ❌ TOTAL: {len(all_issues)} carriers with issues")
    else:
        print(f"  ✅ ALL QA CHECKS PASSED")
    print(f"{'='*70}\n")

    return 1 if all_issues else 0


if __name__ == "__main__":
    sys.exit(main())
