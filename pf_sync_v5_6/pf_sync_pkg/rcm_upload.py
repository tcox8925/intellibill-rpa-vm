"""ZIP build + Azure `rcm-attachments` delivery for Practice Fusion PDFs.

Deliberately mirrors myops/ehr/zipbuild.py's Tebra delivery pattern (same
container, same env vars, same "one ZIP: manifest JSON + every referenced PDF,
uploaded as a single blob" shape) so both EHR integrations land in the same
place the same way. Not imported from myops directly -- pf_sync_v5_6 is a
separate, self-contained project (see README: "no Practice Fusion API is
used"; same spirit applies to not reaching across projects for a few dozen
lines) -- so the small generic pieces (practice abbreviation, random suffix,
path sanitizing, the upload call itself) are ported here rather than shared.

Key difference from Tebra: there, `folder_structure` arrives per-request from
an external caller (myops/server.py's TebraRequest). Practice Fusion has no
such caller today -- this is a single-practice deployment -- so the
destination folder is the fixed PF_RCM_FOLDER_STRUCTURE constant instead of
being threaded through from anywhere.
"""

import json
import os
import random
import re
import string
import zipfile
from pathlib import Path
from typing import Dict

from pf_sync_pkg.constants import (
    AZURE_STORAGE_CONNECTION_STRING,
    PF_RCM_FOLDER_STRUCTURE,
    RCM_ATTACHMENTS_CONTAINER,
)


def get_practice_abbr(practice_name: str) -> str:
    """Same rule as myops/ehr/zipbuild.py's get_practice_abbr: +, -, /, & count
    as word separators alongside spaces, so spacing around them doesn't change
    the abbreviation."""
    words = re.split(r"[\s+\-/&]+", practice_name or "")
    return "".join(w[0].upper() for w in words if w and w[0].isalpha())


def generate_random_suffix(length: int = 4) -> str:
    return "".join(random.choices(string.digits, k=length))


def _safe_segment(value: str) -> str:
    """Sanitize a blob path segment so it can't introduce accidental nested paths."""
    text = str(value or "").strip()
    return text.replace("/", "-").replace("\\", "-")


def _resolve_inbound_folder(folder_structure: str) -> str:
    folder_root = _safe_segment(folder_structure)
    if not folder_root:
        raise RuntimeError("folder_structure is required for upload path resolution")
    return f"{folder_root}/Exchange/Medical Extraction/INBOUND"


def upload_zip_to_rcm(local_zip_path: str, zip_name: str, folder_structure: str = PF_RCM_FOLDER_STRUCTURE) -> str:
    """Uploads a local zip file to Azure Blob at
    rcm-attachments/<folder_structure>/Exchange/Medical Extraction/INBOUND/<zip_name>.
    Returns the blob path uploaded to. Raises on a missing connection string
    rather than silently no-op'ing -- a delivery step that appears to succeed
    but never actually uploaded would be worse than a clear failure.
    """
    if not AZURE_STORAGE_CONNECTION_STRING:
        raise RuntimeError(
            "AZURE_STORAGE_CONNECTION_STRING is not set (repo-root .env) -- cannot upload to Azure."
        )
    from azure.storage.blob import BlobServiceClient

    print(f"[RCM-UPLOAD] Upload start container={RCM_ATTACHMENTS_CONTAINER}", flush=True)
    service = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
    container = service.get_container_client(RCM_ATTACHMENTS_CONTAINER)
    try:
        container.create_container()
        print(f"[RCM-UPLOAD] Created container {RCM_ATTACHMENTS_CONTAINER}", flush=True)
    except Exception:
        pass  # already exists -- expected on every run after the first.

    inbound_folder = _resolve_inbound_folder(folder_structure)
    blob_path = f"{inbound_folder}/{zip_name}"
    with open(local_zip_path, "rb") as f:
        container.upload_blob(blob_path, f, overwrite=True)
    print(f"[RCM-UPLOAD] Upload success zip={zip_name} path={blob_path}", flush=True)
    return blob_path


def build_and_upload_zip(
    manifest_path: str,
    downloads_dir: str,
    practice_name: str,
    folder_structure: str = PF_RCM_FOLDER_STRUCTURE,
    no_upload: bool = False,
) -> Dict[str, object]:
    """Reads a PF appointments manifest (written by
    pdf_pipeline.write_appointments_metadata_json), zips it together with every
    unique PDF it references, and uploads that single zip to rcm-attachments.

    Mirrors myops/ehr/zipbuild.py's pass_zip loop: dedup PDFs by filename
    before zipping (a manifest can list the same PDF more than once when
    several appointments landed on one combined chart print), drop-and-report
    anything whose local PDF is missing rather than let zipfile silently skip
    it, and never raise past a missing manifest/empty appointment list --
    return a clear {"error": ...}/{"skipped": ...} instead, since this runs as
    the last stage of a longer pipeline (run_full_sync_by_date) where a hard
    crash here shouldn't erase everything the earlier stages already did.
    """
    if not manifest_path or not os.path.exists(manifest_path):
        return {"skipped": True, "reason": "no manifest produced this run (no PDFs generated)"}

    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": f"could not read manifest {manifest_path}: {type(exc).__name__}: {exc}"}

    records = manifest.get("appointments") or []
    if not records:
        return {"skipped": True, "reason": "manifest has no appointments"}

    unique_pdfs = sorted({r.get("pdf_file", "") for r in records if r.get("pdf_file")})
    missing = [name for name in unique_pdfs if not os.path.exists(os.path.join(downloads_dir, name))]
    present = [name for name in unique_pdfs if name not in missing]
    if missing:
        print(f"[RCM-UPLOAD] WARNING: {len(missing)} PDF(s) referenced by the manifest are missing on disk, "
              f"zipping without them: {missing}", flush=True)
    if not present:
        return {"error": "every PDF referenced by the manifest is missing on disk; nothing to zip"}

    practice_abbr = get_practice_abbr(practice_name)
    suffix = generate_random_suffix()
    # Date-stamped from the manifest's own appointment dates when available, so
    # the zip name reflects what's inside it rather than "today" (this can run
    # well after the appointments it's delivering, e.g. a delayed retry).
    dates = sorted({r.get("appt_date", "") for r in records if r.get("appt_date")})
    date_stamp = dates[0].replace("-", "") if dates else "unknown"
    base_name = f"pf_facesheets_{practice_abbr}_{date_stamp}_{suffix}"
    json_name = f"{base_name}.json"
    zip_name = f"{base_name}.zip"
    zip_path = os.path.join(downloads_dir, zip_name)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for pdf_filename in present:
            z.write(os.path.join(downloads_dir, pdf_filename), pdf_filename)
        z.writestr(json_name, json.dumps(manifest, indent=2, ensure_ascii=False))

    result: Dict[str, object] = {
        "zip_name": zip_name,
        "zip_path": zip_path,
        "pdf_count": len(present),
        "missing_pdfs": missing,
        "appointment_count": len(records),
    }

    if no_upload:
        result["uploaded"] = False
        result["note"] = "no_upload=True -- zip built locally, not uploaded"
        return result

    try:
        result["blob_path"] = upload_zip_to_rcm(zip_path, zip_name, folder_structure)
        result["container"] = RCM_ATTACHMENTS_CONTAINER
        result["uploaded"] = True
    except Exception as exc:
        result["uploaded"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"

    return result
