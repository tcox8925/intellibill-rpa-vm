"""Backward-compatible entry point shim.

The full implementation now lives in the pf_sync_pkg package (see pf_sync_pkg/cli.py).
This file is kept so existing callers -- notably run_pf_sync_tests_v5_6.ps1, which
invokes `python pf_soap_sync_v5_16.py <command> ...` directly -- keep working unchanged.
"""

from pf_sync_pkg.cli import main
from pf_sync_pkg.constants import BUILD_ID  # noqa: F401  (some callers import BUILD_ID from here)

if __name__ == "__main__":
    raise SystemExit(main())
