import threading
from utils.config import (
    get_postgres_db_secrets,
    DB_CONFIG_POSTGRES,
    POSTGRES_TARGETS,
)
from azure.identity import ClientSecretCredential
import psycopg2

# =============================================================================
# Active Postgres "source" (per-thread).
#
# Each /trigger job runs start-to-finish on a single worker thread, so a
# thread-local is the correct (race-free) place to stash which DB this job
# should write to. server.py sets it as the FIRST line of _run_one(), before
# any DB call (including log_start). All zero-arg callers of
# get_postgres_connection() — upload_utils' resolver, log_utils, dea_checker,
# deactivation_check — then route automatically with no signature changes.
#
# Valid values: "myops" (default) and "rcm". Anything else falls back to myops.
# =============================================================================
_VALID_SOURCES = set(POSTGRES_TARGETS.keys())  # {"myops", "rcm"}
_tls = threading.local()


def set_db_source(source: str | None) -> str:
    """Set the active Postgres source for the current thread. Returns the normalized value."""
    src = (source or "myops").strip().lower()
    if src not in _VALID_SOURCES:
        src = "myops"
    _tls.source = src
    return src


def get_db_source() -> str:
    """Active Postgres source for the current thread (defaults to 'myops')."""
    return getattr(_tls, "source", "myops")


def get_postgres_connection(source: str | None = None):
    """
    Open a Postgres connection to the target selected by `source`.

    - source is None  -> use the per-thread active source (set_db_source), default 'myops'
    - source == 'myops' -> database 'postgres'   (current behavior)
    - source == 'rcm'   -> database '834rcmdev'
    - anything else     -> falls back to 'myops'

    Same server, login, and AAD token for every target; only the database differs.
    All existing call sites pass no argument, so they resolve via the thread-local.
    """
    src = (source or get_db_source() or "myops").strip().lower()
    cfg = POSTGRES_TARGETS.get(src, DB_CONFIG_POSTGRES)

    client_id, client_secret, tenant_id = get_postgres_db_secrets()

    # Generate AAD token for PostgreSQL (same audience for every database).
    credential = ClientSecretCredential(tenant_id, client_id, client_secret)
    token = credential.get_token("https://ossrdbms-aad.database.windows.net/.default").token

    conn = psycopg2.connect(
        host=cfg['server'],
        dbname=cfg['database'],
        user=cfg['user'],
        password=token,
        sslmode="require",
    )
    return conn