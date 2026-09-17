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
import sys
import zipfile
from pathlib import Path
from typing import Dict

from pf_sync_pkg.constants import (
    AZURE_STORAGE_CONNECTION_STRING,
    PF_RCM_FOLDER_STRUCTURE,
    RCM_ATTACHMENTS_CONTAINER,
)


def _trigger_pf_facesheet_processor():
    """Fires the Practice Fusion facesheet processor
    (myops/ehr/pf_facesheet_processor.py) right after a ZIP actually lands in
    `rcm-attachments` below -- event-driven, not a guessed cron delay after
    the scrape.

    BLOCKING, not fire-and-forget: the pull (this upload) and the processing
    that turns it into patient records are one unit of work, not two -- the
    old background-thread version returned immediately, so a caller here
    could report its own success while facesheet processing was still
    running afterward, invisible to whoever's watching this job (same class
    of bug fixed 2026-09-17 in ehr/zipbuild.py's Tebra equivalent). This now
    blocks until the processor actually finishes and raises if it failed;
    both call sites below already catch exceptions from this same line (see
    each one's own try/except), so the failure surfaces as this zip's own
    error instead of vanishing into a print() nobody's watching.

    pf_sync_v5_6 is otherwise deliberately self-contained (see this module's
    docstring) and normally wouldn't reach into myops -- but triggering the
    processor genuinely needs to call into it, so this looks up
    myops/server.py the same way ehr/zipbuild.py's own trigger does: reuse
    the already-loaded module ("tebra_server" when this VM is running the
    combined repo-root server.py) rather than a plain `import server`, which
    would re-exec myops/server.py as a second, disconnected module (its own
    FastAPI app, its own run-lock dict) instead of reaching the one actually
    serving requests. If neither this process's combined server.py nor a
    standalone myops/server.py has been loaded (e.g. pf_sync_v5_6 run fully
    standalone, its historical mode), this raises too -- a caller that can't
    tell whether processing happened at all should not treat that as quiet
    success either.
    """
    server_module = sys.modules.get("tebra_server") or sys.modules.get("server")
    if server_module is None:
        raise RuntimeError(
            "myops server module not loaded in this process -- cannot trigger PF "
            "facesheet processor (expected when pf_sync_v5_6 is run standalone, "
            "outside the combined server.py)"
        )
    server_module._run_pf_facesheet_processor_job(blocking=True, raise_on_error=True)


# Confirmed live 2026-09-17: calling _trigger_pf_facesheet_processor() directly
# from inside a callback that still holds a live Playwright session corrupts
# that session's cleanup -- `greenlet.error: cannot switch to a different
# thread (which happens to have exited)` at close_browser() time. Root cause:
# PF's Chrome isn't a throwaway Playwright-launched browser like Tebra's --
# it's a real Chrome subprocess with a persistent, reusable login profile,
# attached over CDP and torn down with an explicit playwright.stop() (see
# browser.py's build_browser/close_browser) specifically so the Practice
# Fusion session survives between runs. That manual CDP lifecycle is far more
# sensitive to other thread activity happening while it's still attached than
# Tebra's standard launch/`with` pattern -- and the facesheet processor spins
# up its own ThreadPoolExecutor (pf_facesheet_processor.py) to process PDFs,
# right in the middle of that window if called too early.
#
# The pull and the processing are still one unit of work (see
# _trigger_pf_facesheet_processor's own docstring) -- this just moves WHEN
# that blocking call happens, not whether it blocks: callers below only mark
# that a trigger is owed, and cli.py's browser_command_wrapper*/the
# standalone zip-upload command call run_pending_pf_facesheet_trigger() once
# Chrome has fully closed.
_pending_pf_facesheet_trigger = False


def _mark_pf_facesheet_trigger_pending() -> None:
    global _pending_pf_facesheet_trigger
    _pending_pf_facesheet_trigger = True


def run_pending_pf_facesheet_trigger() -> dict | None:
    """Fires the deferred trigger if one is owed; returns None if nothing
    uploaded a ZIP this run. Caller must only invoke this after Chrome has
    fully closed. Still blocks until extraction genuinely finishes; catches
    (rather than raises) so a processing failure can't shadow a real
    exception from whatever ran before it in the same finally block --
    the failure is returned instead, for the caller to fold into its own
    result/exit status."""
    global _pending_pf_facesheet_trigger
    if not _pending_pf_facesheet_trigger:
        return None
    _pending_pf_facesheet_trigger = False
    try:
        _trigger_pf_facesheet_processor()
        return {"triggered": True}
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        print(f"[RCM-UPLOAD] PF facesheet processing FAILED (deferred trigger): {error}", flush=True)
        return {"triggered": True, "error": error}


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


def _delete_local_files(paths: list) -> Dict[str, object]:
    deleted = []
    errors = []
    for path in paths:
        try:
            os.remove(path)
            deleted.append(path)
        except FileNotFoundError:
            pass  # already gone -- not an error worth surfacing.
        except Exception as exc:
            errors.append(f"{path}: {type(exc).__name__}: {exc}")
    return {"deleted": deleted, "errors": errors}


def retry_orphaned_zips(downloads_dir: str, folder_structure: str = PF_RCM_FOLDER_STRUCTURE) -> Dict[str, object]:
    """Re-attempt delivery of any zip left over from a previous run whose upload
    failed. build_and_upload_zip only ever zips the CURRENT run's freshly-
    processed records -- a zip that fails to upload once is otherwise orphaned
    forever, since nothing re-scans for it (see this module's docstring: unlike
    Tebra's zipbuild.pass_zip, which re-queries `file_path IS NULL` every run
    and so self-heals from a failed delivery, pf_sync has no DB to re-query).
    Call this before building today's new zip so a stuck delivery gets retried
    on every subsequent run instead of sitting on disk untouched.

    A retried zip's PDFs are only deleted once THIS retry actually succeeds --
    same "never delete on a failed/skipped upload" rule as build_and_upload_zip.
    The zip's own manifest json entry (already embedded from the run that built
    it) travels with it, so no manifest_path is needed here.
    """
    directory = Path(downloads_dir)
    if not directory.is_dir():
        return {"retried": 0, "uploaded": 0, "failed": 0}

    stale_zips = sorted(directory.glob("pf_facesheets_*.zip"))
    retried = 0
    uploaded = 0
    failed = 0
    details = []
    for zip_path in stale_zips:
        retried += 1
        zip_name = zip_path.name
        print(f"[RCM-UPLOAD] Retrying orphaned zip {zip_name}", flush=True)
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                pdf_names = [n for n in z.namelist() if n.lower().endswith(".pdf")]
        except Exception as exc:
            failed += 1
            details.append({"zip_name": zip_name, "error": f"unreadable zip: {type(exc).__name__}: {exc}"})
            print(f"[RCM-UPLOAD] Orphaned zip {zip_name} is unreadable, leaving it in place: {exc}", flush=True)
            continue

        try:
            blob_path = upload_zip_to_rcm(str(zip_path), zip_name, folder_structure)
        except Exception as exc:
            failed += 1
            details.append({"zip_name": zip_name, "error": f"{type(exc).__name__}: {exc}"})
            print(f"[RCM-UPLOAD] Retry FAILED for orphaned zip {zip_name}: {type(exc).__name__}: {exc}", flush=True)
            continue

        uploaded += 1
        detail = {"zip_name": zip_name, "blob_path": blob_path}
        details.append(detail)
        # Deferred, not called directly -- see run_pending_pf_facesheet_trigger's
        # docstring for why this must wait until Chrome has fully closed.
        _mark_pf_facesheet_trigger_pending()
        local_paths = [str(directory / name) for name in pdf_names] + [str(zip_path)]
        cleanup = _delete_local_files(local_paths)
        if cleanup["errors"]:
            print(
                f"[RCM-UPLOAD] WARNING: retried upload of {zip_name} succeeded but failed to delete "
                f"{len(cleanup['errors'])} local file(s): {cleanup['errors']}",
                flush=True,
            )
        else:
            print(
                f"[RCM-UPLOAD] Retry succeeded for orphaned zip {zip_name} -- deleted "
                f"{len(cleanup['deleted'])} local file(s).",
                flush=True,
            )

    return {"retried": retried, "uploaded": uploaded, "failed": failed, "details": details}


def build_and_upload_zip(
    manifest_path: str,
    downloads_dir: str,
    practice_name: str,
    folder_structure: str = PF_RCM_FOLDER_STRUCTURE,
    no_upload: bool = False,
    delete_local_after_upload: bool = True,
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

    delete_local_after_upload=True (the default): once the zip has actually
    landed in Azure, the source PDFs and the local zip itself are deleted from
    downloads_dir -- Azure is the only place this PHI should persist once
    delivery is confirmed, not the VM's local disk. Nothing is deleted on a
    failed/skipped upload (no_upload=True included) since that's the only
    local copy of the work in that case. pdf_path on the queue rows is left
    as-is on purpose -- it's only ever used as a "was a PDF produced" marker
    (see chart_ui.has_prior_chart_pdf), never checked against the filesystem,
    so a stale path after cleanup doesn't break anything downstream.
    """
    if not manifest_path or not os.path.exists(manifest_path):
        return {"skipped": True, "reason": "no manifest produced this run (no PDFs generated)"}

    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8-sig"))
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
        # Previously this failure was only visible in the JSON response body --
        # a console-only view of a run (e.g. server.log) showed "Upload start"
        # and then nothing, with no way to tell an upload failure apart from a
        # slow-but-fine upload. Print it so it shows up wherever the run is
        # actually being watched, not just in a response body someone may not
        # be looking at.
        print(f"[RCM-UPLOAD] Upload FAILED zip={zip_name}: {result['error']}", flush=True)
        # Upload failed -- the zip/PDFs are the only copy of this work, so leave
        # them on disk for a retry instead of deleting anything.
        return result

    # Deferred, not called directly -- see run_pending_pf_facesheet_trigger's
    # docstring for why this must wait until Chrome has fully closed.
    _mark_pf_facesheet_trigger_pending()

    if delete_local_after_upload:
        # manifest_path (the appointments_*.json this function reads its
        # records from, written earlier by process_records_on_page into
        # pf_encounter_pdfs/) is included here too -- its content is already
        # fully embedded in the zip above (z.writestr(json_name, ...)) and
        # already uploaded, so the standalone copy on disk serves no purpose
        # after this point. It was never in this cleanup list before, so one
        # of these was left behind in pf_encounter_pdfs/ on every single
        # successful run, forever, with nothing ever deleting them -- same
        # class of bug already fixed 2026-09-17 in myops/ehr/zipbuild.py's
        # Tebra equivalent (json_path there).
        local_paths = [os.path.join(downloads_dir, name) for name in present] + [zip_path, manifest_path]
        cleanup = _delete_local_files(local_paths)
        result["local_cleanup"] = {
            "deleted_count": len(cleanup["deleted"]),
            "errors": cleanup["errors"],
        }
        if cleanup["errors"]:
            print(
                f"[RCM-UPLOAD] WARNING: uploaded OK but failed to delete "
                f"{len(cleanup['errors'])} local file(s): {cleanup['errors']}",
                flush=True,
            )
        else:
            print(
                f"[RCM-UPLOAD] Deleted {len(cleanup['deleted'])} local file(s) "
                f"({len(present)} PDF(s) + the zip + the manifest) after a confirmed upload.",
                flush=True,
            )

    return result
