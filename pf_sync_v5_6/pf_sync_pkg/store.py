"""Persistent JSON queue store: atomic writes, load/save, and run history."""

import json
import os
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from pf_sync_pkg.models import QueueRecord
from pf_sync_pkg.utils import clean, now_iso


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
    if not os.path.exists(path):
        return empty_store()
    with open(path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    if isinstance(raw, list):
        store = empty_store()
        store["rows"] = raw
        return store
    if not isinstance(raw, dict):
        raise ValueError("Queue JSON must be an object or a list of rows.")
    store = empty_store()
    store.update(raw)
    store.setdefault("rows", [])
    store.setdefault("patient_mappings", [])
    store.setdefault("runs", [])
    return store


def store_rows(store: Dict[str, Any]) -> List[QueueRecord]:
    return [
        QueueRecord.from_dict(item)
        for item in store.get("rows", [])
        if isinstance(item, dict)
    ]


def save_store(path: str, store: Dict[str, Any], rows: Optional[Sequence[QueueRecord]] = None) -> None:
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
    atomic_write_json(path, store)


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
