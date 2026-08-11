"""
rpa_queue.py — generalized scrape queue + job-control gate.

Two backends behind one interface so the scraper runs both locally and as a service:
  - PostgresQueue: production (Azure Postgres Flex). Set --queue-dsn / RPA_QUEUE_DSN.
  - FileQueue:     local dev fallback (a JSON file). Set --queue-file.

Natural key everywhere:
  (ehr_name, group_name, practice, entity, sub_entity, source_id)

Job control drives the login/continue gate:
  job sets state='awaiting_login'; a UI flips signal='continue'; job proceeds.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional


@dataclass
class Scope:
    ehr_name: str
    practice: str
    group_name: str = ""
    entity: str = "patient"
    sub_entity: str = ""


# ---------------------------------------------------------------------------
# Postgres backend
# ---------------------------------------------------------------------------
class PostgresQueue:
    def __init__(self, dsn: str):
        import psycopg2  # optional dependency; only needed for the Postgres backend
        import psycopg2.extras
        self._pg = psycopg2
        self._extras = psycopg2.extras
        self.conn = psycopg2.connect(dsn)
        self.conn.autocommit = True

    def upsert_target(self, scope: Scope, source_id: str, source_url: str,
                      change_signature: str, run_id: str) -> None:
        """Insert a discovered target, or if it exists and its signature changed,
        reset it to 'pending' so it gets re-scraped. Unchanged rows are left alone."""
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO wpo.rpa_scrape_queue
                    (ehr_name, group_name, practice, entity, sub_entity,
                     source_id, source_url, change_signature, status, run_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s)
                ON CONFLICT (ehr_name, group_name, practice, entity, sub_entity, source_id)
                DO UPDATE SET
                    source_url = EXCLUDED.source_url,
                    status = CASE
                        WHEN wpo.rpa_scrape_queue.change_signature
                             IS DISTINCT FROM EXCLUDED.change_signature
                        THEN 'pending'
                        ELSE wpo.rpa_scrape_queue.status
                    END,
                    change_signature = EXCLUDED.change_signature
                """,
                (scope.ehr_name, scope.group_name, scope.practice, scope.entity,
                 scope.sub_entity, source_id, source_url, change_signature, run_id),
            )

    def claim_batch(self, scope: Scope, limit: int, run_id: str) -> List[dict]:
        """Atomically claim up to `limit` pending rows (safe for parallel workers)."""
        with self.conn.cursor(cursor_factory=self._extras.RealDictCursor) as cur:
            cur.execute(
                """
                UPDATE wpo.rpa_scrape_queue q
                SET status='in_progress', claimed_at=now(),
                    attempts=attempts+1, run_id=%s
                WHERE queue_id IN (
                    SELECT queue_id FROM wpo.rpa_scrape_queue
                    WHERE status='pending' AND ehr_name=%s AND practice=%s
                      AND entity=%s
                    ORDER BY priority, discovered_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                RETURNING *
                """,
                (run_id, scope.ehr_name, scope.practice, scope.entity, limit),
            )
            return list(cur.fetchall())

    def mark_done(self, queue_id: int, result: Optional[dict] = None) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE wpo.rpa_scrape_queue SET status='done', scraped_at=now(), "
                "result=%s WHERE queue_id=%s",
                (self._extras.Json(result) if result is not None else None, queue_id),
            )

    def mark_error(self, queue_id: int, err: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE wpo.rpa_scrape_queue SET status='error', last_error=%s "
                "WHERE queue_id=%s",
                (err[:2000], queue_id),
            )

    def reclaim_stale(self, older_than_seconds: int = 900) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE wpo.rpa_scrape_queue SET status='pending' "
                "WHERE status='in_progress' "
                "AND claimed_at < now() - (%s || ' seconds')::interval",
                (older_than_seconds,),
            )
            return cur.rowcount

    def stats(self, scope: Scope) -> Dict[str, int]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT status, count(*) FROM wpo.rpa_scrape_queue "
                "WHERE ehr_name=%s AND practice=%s GROUP BY status",
                (scope.ehr_name, scope.practice),
            )
            return {row[0]: row[1] for row in cur.fetchall()}

    # --- job control ---
    def set_state(self, job_id: str, state: str, message: str = "",
                  stats: Optional[dict] = None, scope: Optional[Scope] = None,
                  mode: str = "") -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO wpo.rpa_job_control (job_id, ehr_name, practice, mode,
                    state, message, stats, updated_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s, now())
                ON CONFLICT (job_id) DO UPDATE SET
                    state=EXCLUDED.state, message=EXCLUDED.message,
                    stats=COALESCE(EXCLUDED.stats, wpo.rpa_job_control.stats),
                    updated_at=now()
                """,
                (job_id, scope.ehr_name if scope else None,
                 scope.practice if scope else None, mode or None, state, message,
                 self._extras.Json(stats) if stats is not None else None),
            )

    def get_signal(self, job_id: str) -> str:
        with self.conn.cursor() as cur:
            cur.execute("SELECT signal FROM wpo.rpa_job_control WHERE job_id=%s", (job_id,))
            row = cur.fetchone()
            return (row[0] if row else "none") or "none"

    def clear_signal(self, job_id: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute("UPDATE wpo.rpa_job_control SET signal='none' WHERE job_id=%s", (job_id,))


# ---------------------------------------------------------------------------
# Local file backend (dev / single-worker). Not concurrency-safe across procs.
# ---------------------------------------------------------------------------
class FileQueue:
    def __init__(self, path: str):
        self.path = path
        self.control_path = path + ".control.json"
        if not os.path.exists(path):
            self._save({"rows": {}})

    def _load(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"rows": {}}

    def _save(self, data: dict) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, self.path)

    @staticmethod
    def _key(scope: Scope, source_id: str) -> str:
        return "|".join([scope.ehr_name, scope.group_name, scope.practice,
                         scope.entity, scope.sub_entity, source_id])

    def upsert_target(self, scope, source_id, source_url, change_signature, run_id):
        d = self._load()
        k = self._key(scope, source_id)
        row = d["rows"].get(k)
        if row is None:
            d["rows"][k] = {
                "source_id": source_id, "source_url": source_url,
                "change_signature": change_signature, "status": "pending",
                "attempts": 0, "scope": asdict(scope), "run_id": run_id,
            }
        elif row.get("change_signature") != change_signature:
            row["change_signature"] = change_signature
            row["source_url"] = source_url
            row["status"] = "pending"
        self._save(d)

    def claim_batch(self, scope, limit, run_id):
        d = self._load()
        claimed = []
        for k, row in d["rows"].items():
            if len(claimed) >= limit:
                break
            if row.get("status") == "pending" and row["scope"].get("ehr_name") == scope.ehr_name \
               and row["scope"].get("practice") == scope.practice \
               and row["scope"].get("entity") == scope.entity:
                row["status"] = "in_progress"
                row["attempts"] = row.get("attempts", 0) + 1
                claimed.append({"queue_id": k, **row})
        self._save(d)
        return claimed

    def mark_done(self, queue_id, result=None):
        d = self._load()
        if queue_id in d["rows"]:
            d["rows"][queue_id]["status"] = "done"
            if result is not None:
                d["rows"][queue_id]["result"] = result
        self._save(d)

    def mark_error(self, queue_id, err):
        d = self._load()
        if queue_id in d["rows"]:
            d["rows"][queue_id]["status"] = "error"
            d["rows"][queue_id]["last_error"] = err[:2000]
        self._save(d)

    def reclaim_stale(self, older_than_seconds=900):
        d = self._load()
        n = 0
        for row in d["rows"].values():
            if row.get("status") == "in_progress":
                row["status"] = "pending"
                n += 1
        self._save(d)
        return n

    def stats(self, scope):
        d = self._load()
        out: Dict[str, int] = {}
        for row in d["rows"].values():
            out[row.get("status", "?")] = out.get(row.get("status", "?"), 0) + 1
        return out

    # --- job control (file-based signal the UI/endpoint can also write) ---
    def _load_control(self) -> dict:
        try:
            with open(self.control_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def set_state(self, job_id, state, message="", stats=None, scope=None, mode=""):
        c = self._load_control()
        c.update({"job_id": job_id, "state": state, "message": message,
                  "stats": stats, "updated_at": time.time()})
        c.setdefault("signal", "none")
        with open(self.control_path, "w", encoding="utf-8") as f:
            json.dump(c, f)

    def get_signal(self, job_id):
        return self._load_control().get("signal", "none") or "none"

    def clear_signal(self, job_id):
        c = self._load_control()
        c["signal"] = "none"
        with open(self.control_path, "w", encoding="utf-8") as f:
            json.dump(c, f)


def make_queue(dsn: str = "", file_path: str = ""):
    """Pick a backend: Postgres if a DSN is given, else a local file."""
    if dsn:
        return PostgresQueue(dsn)
    return FileQueue(file_path or "rpa_queue.json")


# ---------------------------------------------------------------------------
# Login / continue gate — replaces terminal input() so a UI can drive it.
# ---------------------------------------------------------------------------
def wait_for_start_signal(store, job_id: str, scope: Scope, mode: str,
                          message: str, poll: float = 2.0,
                          timeout: float = 1800.0, interactive: bool = True) -> bool:
    """
    Blocks until it's safe to proceed past login.

    Order of resolution:
      1. If running interactively with a TTY, allow a local Enter keypress too.
      2. Otherwise (service), poll job control until signal == 'continue'.

    Returns True to proceed, False if 'stop' was signalled or it timed out.
    """
    store.set_state(job_id, "awaiting_login", message=message, scope=scope, mode=mode)
    print(f"[gate] {message}", flush=True)

    tty = interactive and sys.stdin is not None and sys.stdin.isatty()
    deadline = time.time() + timeout

    # Local convenience: let a human just press Enter when at a terminal.
    if tty:
        print(">>> Press ENTER when logged in (or wait for the UI Continue signal)...", flush=True)

    while time.time() < deadline:
        sig = store.get_signal(job_id)
        if sig == "continue":
            store.clear_signal(job_id)
            store.set_state(job_id, "running", message="Login confirmed; scraping.",
                            scope=scope, mode=mode)
            return True
        if sig == "stop":
            store.clear_signal(job_id)
            store.set_state(job_id, "error", message="Stopped by operator.",
                            scope=scope, mode=mode)
            return False

        if tty:
            # Non-blocking-ish: on Windows/simple setups, fall back to input() once.
            try:
                import msvcrt  # Windows
                if msvcrt.kbhit():
                    msvcrt.getch()
                    store.set_state(job_id, "running", message="Login confirmed (local).",
                                    scope=scope, mode=mode)
                    return True
            except Exception:
                pass
        time.sleep(poll)

    store.set_state(job_id, "error", message="Timed out waiting for login.",
                    scope=scope, mode=mode)
    return False
