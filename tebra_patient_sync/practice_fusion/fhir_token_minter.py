#!/usr/bin/env python3
"""
fhir_token_minter.py — compatibility shim.

The SMART Backend Services token-minting logic (auth, JWT signing, Key Vault
credential selection, --selftest) now lives in practice_fusion_full_export.py
so the full-export pipeline is a single self-contained file instead of
depending on this separate module.

This shim just re-exports those names so fhir_bulk_probe.py and
fhir_sample_to_json.py (which still `import fhir_token_minter as mint`) keep
working unchanged, without a second copy of the auth logic to drift out of
sync. New code should import practice_fusion_full_export directly.

    python fhir_token_minter.py --selftest   # still works, forwards to the
                                              # merged implementation
    python fhir_token_minter.py              # mints and prints a live token
"""

import sys

from practice_fusion_full_export import (  # noqa: F401 (re-exported for callers)
    ASSERTION_TTL,
    ASSERTION_TYPE,
    BASE_URL,
    CLIENT_ID,
    JWKS_URL,
    KEY_NAME,
    KID,
    SCOPES,
    VAULT_URL,
    build_signed_assertion,
    discover_token_endpoint,
    get_access_token,
    selftest,
)

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print(get_access_token())
