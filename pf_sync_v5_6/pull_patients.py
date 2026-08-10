"""Backward-compatible entry point for the patient roster discover/scrape tool.

The full implementation now lives in pf_sync_pkg/patient_scraper.py (merged in from
the standalone "Practice Fusion RPA" tool, with its Patient list report selectors
fixed to match what this account's UI actually renders, and TEST_PF_USERNAME/
TEST_PF_PASSWORD removed in favor of PF_USERNAME/PF_PASSWORD from the repo-root .env).
This file is kept as the runnable entry point at the repo root:

    python pull_patients.py --attach --debug-port 9222 \\
        --practice "NWARK Internal Medicine" --mode discover \\
        --out practice_fusion_patients.csv

See pf_sync_v5_6/RPA_Scraper_Implementation.md for the queue/DB design notes.
"""

from pf_sync_pkg.patient_scraper import main

if __name__ == "__main__":
    raise SystemExit(main())
