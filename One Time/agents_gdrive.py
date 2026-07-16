# ==========================================================
#  migrate_all_agents.py
# ==========================================================
"""
Full migration: Google Drive agent folders → Azure Blob Storage

- Parallelized at the agent level (configurable workers)
- Skips agents already in blob storage
- Resume-safe via local completed_npns.txt
- Paginated DB reads (no 10M rows in memory)
- Thread-local Drive service instances
- Logs to file + console

Blob path: agilityagents/{NPN}/{category}/{filename}
"""

import io
import os
import json
import time
import logging
import threading
import argparse
import psycopg2
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient

# ==========================================================
#  CONFIG
# ==========================================================
KEY_VAULT_URL      = os.getenv("KEYVAULT_URL", "")

GDRIVE_SECRET_NAME = "gdrive-api-access"
DELEGATED_USER     = "dataops@834labs.com"
GDRIVE_SCOPES      = ["https://www.googleapis.com/auth/drive"]

POSTGRES_HOST      = os.getenv("DEFAULT834_DB_HOST", "")
POSTGRES_DB        = os.getenv("DEFAULT834_DB_NAME", "")
POSTGRES_USER      = os.getenv("DEFAULT834_DB_USER", "")

STORAGE_ACCOUNT    = "agilitydatadev001"
CONTAINER_NAME     = "agilityagents"

WORKERS            = 20          # parallel agent-level threads
DB_PAGE_SIZE       = 5000        # agents per DB fetch
COMPLETED_FILE     = "completed_npns.txt"  # resume tracker
LOG_FILE           = "migration.log"

EXPORT_MAP = {
    "application/vnd.google-apps.document":     ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet":  (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"
    ),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
}

# ==========================================================
#  LOGGING
# ==========================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(threadName)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

# Silence noisy SDK loggers
for name in [
    "azure", "azure.core", "azure.identity", "azure.storage",
    "google", "googleapiclient", "google_auth_httplib2",
    "urllib3", "msal",
]:
    logging.getLogger(name).setLevel(logging.WARNING)

# ==========================================================
#  AUTH
# ==========================================================
def get_sp_credentials():
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
    client_id     = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tenant_id     = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value
    return client_id, client_secret, tenant_id


def get_pg_conn(client_id, client_secret, tenant_id):
    sp = ClientSecretCredential(tenant_id, client_id, client_secret)
    token = sp.get_token("https://ossrdbms-aad.database.windows.net/.default").token
    return psycopg2.connect(
        host=POSTGRES_HOST, dbname=POSTGRES_DB,
        user=POSTGRES_USER, password=token, sslmode="require"
    )


def get_blob_container(client_id, client_secret, tenant_id):
    cred = ClientSecretCredential(tenant_id, client_id, client_secret)
    svc = BlobServiceClient(
        account_url=f"https://{STORAGE_ACCOUNT}.blob.core.windows.net",
        credential=cred,
        connection_pool_maxsize=WORKERS + 5,
    )
    return svc.get_container_client(CONTAINER_NAME)


# Thread-local Drive service (one per worker thread)
_thread_local = threading.local()
_drive_sa_json = None  # loaded once, shared across threads

def _load_drive_sa_json():
    global _drive_sa_json
    if _drive_sa_json is None:
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
        _drive_sa_json = json.loads(client.get_secret(GDRIVE_SECRET_NAME).value)

def get_thread_drive_service():
    """Return a Drive service unique to the current thread."""
    if not hasattr(_thread_local, "service"):
        creds = service_account.Credentials.from_service_account_info(
            _drive_sa_json, scopes=GDRIVE_SCOPES
        ).with_subject(DELEGATED_USER)
        _thread_local.service = build("drive", "v3", credentials=creds)
    return _thread_local.service


# ==========================================================
#  SKIP LOGIC — existing blobs
# ==========================================================
def load_existing_npns(container) -> set:
    """
    List top-level NPN prefixes already in blob.
    Uses delimiter='/' to get virtual directories efficiently.
    """
    existing = set()
    for prefix in container.walk_blobs(delimiter="/"):
        npn = prefix.name.rstrip("/")
        existing.add(npn)
    return existing


def load_completed_npns() -> set:
    """Load NPNs completed in previous runs (resume support)."""
    return _load_completed_file(COMPLETED_FILE)


def _load_completed_file(path: str) -> set:
    if os.path.exists(path):
        with open(path, "r") as f:
            return set(line.strip() for line in f if line.strip())
    return set()


def mark_completed(npn: str, path: str = COMPLETED_FILE):
    """Append an NPN to the completed file (thread-safe via append mode)."""
    with open(path, "a") as f:
        f.write(f"{npn}\n")


# ==========================================================
#  DRIVE WALK + TRANSFER
# ==========================================================
def sanitize_blob_path(path: str) -> str:
    """Clean up file/folder names for Azure Blob Storage URI compatibility."""
    import re
    parts = path.split("/")

    # Check if original filename (last part) is valid before sanitizing
    original_filename = parts[-1].strip() if parts else ""
    if not original_filename or original_filename in (".", ".."):
        return None  # no valid filename

    clean = []
    for p in parts:
        p = p.strip()
        p = re.sub(r'[\x00-\x1f\x7f]', '', p)
        p = re.sub(r'[\\:#\?\*"<>|]', '_', p)
        if p and p not in (".", ".."):
            clean.append(p)

    if len(clean) < 2:
        return None
    return "/".join(clean)
def list_children(service, folder_id):
    items, token = [], None
    while True:
        resp = service.files().list(
            q=f"'{folder_id}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType, size)",
            pageSize=1000, pageToken=token,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        items.extend(resp.get("files", []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return items


def collect_files(service, folder_id, prefix=""):
    tasks = []
    for item in list_children(service, folder_id):
        path = f"{prefix}/{item['name']}" if prefix else item["name"]
        if item["mimeType"] == "application/vnd.google-apps.folder":
            tasks.extend(collect_files(service, item["id"], path))
        else:
            tasks.append({
                "id": item["id"],
                "name": item["name"],
                "mime": item["mimeType"],
                "blob_path": path,
            })
    return tasks


def transfer_file(service, container, file_info, npn):
    """Download from Drive → upload to Blob. Returns bytes transferred."""
    fid       = file_info["id"]
    mime      = file_info["mime"]
    name      = file_info["name"]
    blob_path = sanitize_blob_path(f"{npn}/{file_info['blob_path']}")
    if not blob_path:
        return 0  # skip unsanitizable filenames

    buf = io.BytesIO()

    if mime in EXPORT_MAP:
        export_mime, ext = EXPORT_MAP[mime]
        request = service.files().export_media(fileId=fid, mimeType=export_mime)
        if not name.endswith(ext):
            blob_path = blob_path.rsplit("/", 1)[0] + "/" + name + ext
    elif mime.startswith("application/vnd.google-apps."):
        return 0  # skip unsupported native types
    else:
        request = service.files().get_media(fileId=fid, supportsAllDrives=True)

    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    buf.seek(0)
    size = buf.getbuffer().nbytes
    blob_client = container.get_blob_client(blob_path)
    try:
        blob_client.upload_blob(buf, overwrite=True)
    except Exception:
        buf.seek(0)
        try:
            blob_client.upload_blob(buf)
        except Exception:
            pass  # already exists, move on
    return size


def migrate_one_agent(container, npn, drive_id, completed_file=COMPLETED_FILE):
    """Migrate all files for a single agent. Returns summary dict."""
    t0 = time.time()
    try:
        service = get_thread_drive_service()
        files = collect_files(service, drive_id)

        if not files:
            mark_completed(npn, completed_file)
            return {"npn": npn, "status": "empty", "files": 0, "bytes": 0, "time": 0}

        total_bytes = 0
        for f in files:
            total_bytes += transfer_file(service, container, f, npn)

        elapsed = time.time() - t0
        mark_completed(npn, completed_file)
        return {"npn": npn, "status": "ok", "files": len(files), "bytes": total_bytes, "time": elapsed}

    except Exception as e:
        elapsed = time.time() - t0
        log.error(f"FAILED NPN {npn}: {e}")
        return {"npn": npn, "status": "failed", "files": 0, "bytes": 0, "time": elapsed, "error": str(e)}


# ==========================================================
#  PAGINATED DB READER
# ==========================================================
def iter_agents(conn, skip_npns: set, partition: int = None, total_partitions: int = None):
    """
    Yield (npn, drive_id) in pages, skipping already-done NPNs.
    If partitioned, only yields rows where npn hash % total_partitions == partition.
    """
    conn.autocommit = False
    cursor_name = "agent_cursor"
    with conn.cursor(name=cursor_name) as cur:
        cur.itersize = DB_PAGE_SIZE

        if partition is not None and total_partitions:
            cur.execute("""
                SELECT DISTINCT npn, google_drive_id
                FROM wpo.lup_agents_contracts
                WHERE google_drive_id IS NOT NULL
                  AND TRIM(google_drive_id) != ''
                  AND MOD(ABS(hashtext(npn::text)), %s) = %s
                ORDER BY npn
            """, (total_partitions, partition))
        else:
            cur.execute("""
                SELECT DISTINCT npn, google_drive_id
                FROM wpo.lup_agents_contracts
                WHERE google_drive_id IS NOT NULL
                  AND TRIM(google_drive_id) != ''
                ORDER BY npn
            """)

        for row in cur:
            npn = str(row[0]).strip()
            drive_id = str(row[1]).strip()
            if npn not in skip_npns:
                yield npn, drive_id


# ==========================================================
#  MAIN
# ==========================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--partition", type=str, default=None,
                        help="Partition as 'N/M' e.g. '0/10' = partition 0 of 10")
    parser.add_argument("--workers", type=int, default=WORKERS,
                        help=f"Number of parallel workers (default: {WORKERS})")
    args = parser.parse_args()

    partition, total_partitions = None, None
    suffix = ""
    if args.partition:
        partition, total_partitions = map(int, args.partition.split("/"))
        suffix = f"_p{partition}"
        log.info(f"Partition {partition}/{total_partitions}")

    completed_file = f"completed_npns{suffix}.txt"
    workers = args.workers
    overall_start = time.time()

    # Auth
    client_id, client_secret, tenant_id = get_sp_credentials()
    conn = get_pg_conn(client_id, client_secret, tenant_id)
    container = get_blob_container(client_id, client_secret, tenant_id)
    _load_drive_sa_json()

    # Build skip set
    existing_npns = load_existing_npns(container)
    completed_npns = _load_completed_file(completed_file)
    skip_npns = existing_npns | completed_npns
    log.info(f"Skipping {len(skip_npns)} NPNs (blob: {len(existing_npns)}, resume file: {len(completed_npns)})")

    total_ok, total_failed, total_empty = 0, 0, 0
    total_bytes, total_files = 0, 0

    log.info(f"Starting migration with {workers} workers...")

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="w") as pool:
        futures = {}
        agent_iter = iter_agents(conn, skip_npns, partition, total_partitions)

        # Seed the pool
        for npn, drive_id in agent_iter:
            fut = pool.submit(migrate_one_agent, container, npn, drive_id, completed_file)
            futures[fut] = npn
            if len(futures) >= workers:
                break

        # Continuous feed
        while futures:
            done_futures = {f for f in futures if f.done()}
            if not done_futures:
                time.sleep(0.1)
                continue

            for fut in done_futures:
                r = fut.result()
                del futures[fut]

                if r["status"] == "ok":
                    total_ok += 1
                    total_bytes += r["bytes"]
                    total_files += r["files"]
                elif r["status"] == "failed":
                    total_failed += 1
                elif r["status"] == "empty":
                    total_empty += 1

                # Refill the slot
                try:
                    npn, drive_id = next(agent_iter)
                    fut_new = pool.submit(migrate_one_agent, container, npn, drive_id, completed_file)
                    futures[fut_new] = npn
                except StopIteration:
                    pass

                # Progress log
                processed = total_ok + total_failed + total_empty
                if processed % 200 == 0 and processed > 0:
                    elapsed = time.time() - overall_start
                    rate = processed / elapsed * 3600 if elapsed > 0 else 0
                    log.info(
                        f"Progress: {total_ok} ok, {total_failed} failed | "
                        f"{total_files} files, {total_bytes / 1e9:.2f} GB | "
                        f"{rate:.0f} agents/hr"
                    )

    conn.close()
    elapsed = time.time() - overall_start

    log.info(
        f"DONE | ok: {total_ok}, failed: {total_failed}, empty: {total_empty}, skipped: {len(skip_npns)} | "
        f"{total_files} files, {total_bytes / 1e9:.2f} GB | {elapsed / 60:.1f} min"
    )
    if total_ok > 0:
        log.info(f"Avg: {elapsed / total_ok:.2f}s/agent | Rate: {total_ok / elapsed * 3600:.0f} agents/hr")


if __name__ == "__main__":
    main()