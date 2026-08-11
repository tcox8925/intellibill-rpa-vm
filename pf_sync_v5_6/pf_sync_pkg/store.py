"""Postgres-backed queue store: RCM DB, ehr schema, replaces the JSON queue
file as the system of record for success/failure/error state.

Public API is unchanged on purpose -- load_store/save_store/store_rows/
append_run/finish_run/empty_store keep the exact same signatures they had when
this module wrote to a JSON file, so every caller (cli.py, server.py,
matching.py, ingest.py, pdf_pipeline.py, queue_admin.py, selftest.py) keeps
working without modification. The `path` argument callers already pass (a
queue_json file path, e.g. "pf_appointment_queue.json") becomes the logical
`queue_key` -- its basename -- so the existing separation between queue files
is preserved as rows sharing one table instead of colliding.

Why: the JSON file lived on local disk with no locking across concurrent
readers/writers, no query surface for "show me everything that failed", and
no durability beyond that one disk. See migrations/001_create_pf_sync_queue_tables.sql
for the table definitions (ehr.ehr_pf_queue_rows / _runs / _meta) and
migrations/run_migration.py to (re)apply them.

atomic_write_json is kept as-is -- it is still used for plain config files
(report_config_json, etc.) that are not part of the queue store.
"""

import json
import os
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import psycopg2
import psycopg2.extras
from dotenv import find_dotenv, load_dotenv

from pf_sync_pkg.models import QueueRecord
from pf_sync_pkg.utils import clean, now_iso

load_dotenv(find_dotenv(usecwd=False))

SCHEMA = "ehr"
ROWS_TABLE = f"{SCHEMA}.ehr_pf_queue_rows"
RUNS_TABLE = f"{SCHEMA}.ehr_pf_queue_runs"
META_TABLE = f"{SCHEMA}.ehr_pf_queue_meta"

# QueueRecord's field order doubles as the DB column list for ehr_pf_queue_rows
# (queue_key is the only column not on the dataclass). Keeping this derived
# rather than hand-typed means a new QueueRecord field automatically gets a
# matching DB column the next time the migration is extended.
_RECORD_FIELDS: List[str] = list(QueueRecord.__dataclass_fields__.keys())
_TIMESTAMP_FIELDS = {
    "created_at",
    "updated_at",
    "first_ready_at",
    "processing_started_at",
    "last_checked_at",
    "processed_at",
}
_JSON_FIELDS = {"patient_candidates", "source_row_json", "selected_sections"}
_DEFAULTS: Dict[str, Any] = asdict(QueueRecord(row_id=""))


def _resolve_db_password() -> str:
    password = os.environ.get("RCM_DB_PASSWORD", "").strip()
    if password:
        return password
    raise RuntimeError(
        "RCM_DB_PASSWORD is required for the pf-sync queue store. Set it in .env."
    )


def _connect():
    host = os.environ.get("RCM_DB_HOST", "").strip()
    dbname = os.environ.get("RCM_DB_NAME", "").strip()
    user = os.environ.get("RCM_DB_USER", "").strip()
    if not (host and dbname and user):
        raise RuntimeError(
            "RCM_DB_HOST/RCM_DB_NAME/RCM_DB_USER are required for the pf-sync queue "
            "store. Set them in .env."
        )
    return psycopg2.connect(
        host=host, dbname=dbname, user=user, password=_resolve_db_password(), sslmode="require"
    )


def _queue_key(path: str) -> str:
    """The queue file's basename is the namespace rows/runs/meta are keyed
    under, so pf_appointment_queue.json and *_tomorrow_test.json etc. stay
    separate logical queues inside the shared table."""
    return Path(path).name or str(path)


def _row_to_params(queue_key: str, record: Dict[str, Any]) -> List[Any]:
    params: List[Any] = [queue_key]
    for field in _RECORD_FIELDS:
        value = record.get(field, _DEFAULTS.get(field))
        if field in _TIMESTAMP_FIELDS:
            params.append(value or None)
        elif field in _JSON_FIELDS:
            params.append(psycopg2.extras.Json(value if value is not None else _DEFAULTS[field]))
        else:
            params.append(value)
    return params


def _row_from_db(values: Sequence[Any]) -> Dict[str, Any]:
    record: Dict[str, Any] = {}
    for field, value in zip(_RECORD_FIELDS, values):
        if value is None:
            record[field] = _DEFAULTS.get(field, "")
        elif field in _TIMESTAMP_FIELDS:
            record[field] = value.astimezone().isoformat(timespec="seconds")
        else:
            record[field] = value
    return record


_INSERT_COLUMNS = ["queue_key"] + _RECORD_FIELDS
_UPDATE_ASSIGNMENTS = ", ".join(f"{c} = EXCLUDED.{c}" for c in _RECORD_FIELDS if c != "row_id")
_UPSERT_ROW_SQL = f"""
    INSERT INTO {ROWS_TABLE} ({', '.join(_INSERT_COLUMNS)}, db_updated_at)
    VALUES ({', '.join(['%s'] * len(_INSERT_COLUMNS))}, now())
    ON CONFLICT (queue_key, row_id) DO UPDATE SET
        {_UPDATE_ASSIGNMENTS},
        db_updated_at = now()
"""


def atomic_write_json(path: str, payload: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)


def empty_store() -> Dict[str, Any]:
    return {
        "schema_version": 3,
        "updated_at": now_iso(),
        "counts": {},
        "patient_mappings": [],
        "runs": [],
        "rows": [],
    }


def load_store(path: str) -> Dict[str, Any]:
    queue_key = _queue_key(path)
    store = empty_store()
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {', '.join(_RECORD_FIELDS)} FROM {ROWS_TABLE} "
            f"WHERE queue_key = %s ORDER BY db_created_at",
            (queue_key,),
        )
        store["rows"] = [_row_from_db(row) for row in cur.fetchall()]

        cur.execute(
            f"SELECT schema_version, patient_mappings FROM {META_TABLE} WHERE queue_key = %s",
            (queue_key,),
        )
        meta_row = cur.fetchone()
        if meta_row:
            store["schema_version"] = meta_row[0] if meta_row[0] is not None else 3
            store["patient_mappings"] = meta_row[1] if meta_row[1] is not None else []

        cur.execute(
            f"SELECT run_id, command, status, started_at, finished_at, details FROM {RUNS_TABLE} "
            f"WHERE queue_key = %s ORDER BY db_created_at DESC LIMIT 100",
            (queue_key,),
        )
        run_rows = cur.fetchall()
    finally:
        conn.close()

    runs = []
    for run_id, command, status, started_at, finished_at, details in reversed(run_rows):
        run: Dict[str, Any] = {"run_id": run_id, "command": command}
        if started_at:
            run["started_at"] = started_at.astimezone().isoformat(timespec="seconds")
        if status is not None:
            run["status"] = status
        if finished_at:
            run["finished_at"] = finished_at.astimezone().isoformat(timespec="seconds")
        run.update(details or {})
        runs.append(run)
    store["runs"] = runs
    return store


def store_rows(store: Dict[str, Any]) -> List[QueueRecord]:
    return [
        QueueRecord.from_dict(item)
        for item in store.get("rows", [])
        if isinstance(item, dict)
    ]


def save_store(path: str, store: Dict[str, Any], rows: Optional[Sequence[QueueRecord]] = None) -> None:
    queue_key = _queue_key(path)
    if rows is not None:
        store["rows"] = [asdict(row) for row in rows]

    counts: Dict[str, int] = {}
    for item in store.get("rows", []):
        status = clean(item.get("status") if isinstance(item, dict) else "") or "unknown"
        counts[status] = counts.get(status, 0) + 1
    store["schema_version"] = 3
    store["updated_at"] = now_iso()
    store["counts"] = counts
    store["runs"] = list(store.get("runs", []))[-100:]

    row_ids = [
        item.get("row_id")
        for item in store.get("rows", [])
        if isinstance(item, dict) and item.get("row_id")
    ]

    conn = _connect()
    try:
        cur = conn.cursor()
        # Mirror the JSON file's full-overwrite semantics: store["rows"] is the
        # complete row set for this queue_key, so anything no longer in it
        # (e.g. ingest's reset_existing=True) is dropped here too.
        if row_ids:
            cur.execute(
                f"DELETE FROM {ROWS_TABLE} WHERE queue_key = %s AND row_id <> ALL(%s)",
                (queue_key, row_ids),
            )
        else:
            cur.execute(f"DELETE FROM {ROWS_TABLE} WHERE queue_key = %s", (queue_key,))

        for item in store.get("rows", []):
            if not isinstance(item, dict):
                continue
            cur.execute(_UPSERT_ROW_SQL, _row_to_params(queue_key, item))

        cur.execute(
            f"""
            INSERT INTO {META_TABLE} (queue_key, schema_version, patient_mappings, updated_at)
            VALUES (%s, %s, %s, now())
            ON CONFLICT (queue_key) DO UPDATE SET
                schema_version = EXCLUDED.schema_version,
                patient_mappings = EXCLUDED.patient_mappings,
                updated_at = now()
            """,
            (queue_key, store["schema_version"], psycopg2.extras.Json(store.get("patient_mappings", []))),
        )

        for run in store.get("runs", []):
            details = {
                k: v
                for k, v in run.items()
                if k not in {"run_id", "command", "status", "started_at", "finished_at"}
            }
            cur.execute(
                f"""
                INSERT INTO {RUNS_TABLE} (run_id, queue_key, command, status, started_at, finished_at, details)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    command = EXCLUDED.command,
                    status = EXCLUDED.status,
                    started_at = EXCLUDED.started_at,
                    finished_at = EXCLUDED.finished_at,
                    details = EXCLUDED.details
                """,
                (
                    run.get("run_id"),
                    queue_key,
                    run.get("command"),
                    run.get("status"),
                    run.get("started_at") or None,
                    run.get("finished_at") or None,
                    psycopg2.extras.Json(details),
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def append_run(store: Dict[str, Any], command: str, details: Dict[str, Any]) -> str:
    run_id = str(uuid.uuid4())
    store.setdefault("runs", []).append(
        {
            "run_id": run_id,
            "command": command,
            "started_at": now_iso(),
            **details,
        }
    )
    return run_id


def finish_run(store: Dict[str, Any], run_id: str, status: str, details: Dict[str, Any]) -> None:
    for run in reversed(store.get("runs", [])):
        if run.get("run_id") == run_id:
            run.update({"finished_at": now_iso(), "status": status, **details})
            return
