#!/usr/bin/env python3
"""Apply a migrations/*.sql file to the RCM DB using RCM_DB_* from .env.

Usage:
    python3 migrations/run_migration.py migrations/001_create_pf_sync_queue_tables.sql
    python3 migrations/run_migration.py            # applies every 0xx_*.sql in order, once each
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg2
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=False))

import os

MIGRATIONS_DIR = Path(__file__).resolve().parent


def _connect():
    host = os.environ.get("RCM_DB_HOST", "").strip()
    dbname = os.environ.get("RCM_DB_NAME", "").strip()
    user = os.environ.get("RCM_DB_USER", "").strip()
    password = os.environ.get("RCM_DB_PASSWORD", "").strip()
    missing = [
        name
        for name, value in [
            ("RCM_DB_HOST", host),
            ("RCM_DB_NAME", dbname),
            ("RCM_DB_USER", user),
            ("RCM_DB_PASSWORD", password),
        ]
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
    return psycopg2.connect(
        host=host, dbname=dbname, user=user, password=password, sslmode="require"
    )


def apply_file(path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    print(f"Applying {path.name} ...", flush=True)
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
        print(f"  OK: {path.name}", flush=True)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main(argv: list) -> int:
    if argv:
        targets = [Path(a) for a in argv]
    else:
        targets = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    if not targets:
        print("No migration files found.")
        return 1
    for target in targets:
        apply_file(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
