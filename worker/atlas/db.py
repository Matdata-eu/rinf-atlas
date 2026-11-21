"""PostgreSQL helpers."""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import psycopg

log = logging.getLogger(__name__)


def connect(url: str, attempts: int = 30) -> psycopg.Connection:
    for i in range(attempts):
        try:
            return psycopg.connect(url, autocommit=False)
        except psycopg.OperationalError as e:
            if i == attempts - 1:
                raise
            log.info("database not ready (%s), waiting", str(e).strip().splitlines()[0])
            time.sleep(2)
    raise RuntimeError("unreachable")


def migrate(conn: psycopg.Connection, sql_path: Path) -> bool:
    """Apply the schema (`init.sql`) unless this version of it was applied before; True when it ran.

    The file is idempotent, but its ALTER TABLE statements take an exclusive lock on tables even
    when there is nothing to change. A running export holds a read transaction on those tables
    for as long as it runs, so the lock would wait for it, and every query of the API would
    queue behind that wait. Every `atlas` command comes through here, so the schema is only
    applied when the file changed: after an upgrade, when the worker restarts."""
    sql = sql_path.read_text(encoding="utf-8")
    digest = hashlib.sha256(sql.encode()).hexdigest()
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(424242)")
        try:
            cur.execute("SELECT to_regclass('schema_state') IS NOT NULL")
            if cur.fetchone()[0]:
                cur.execute("SELECT digest FROM schema_state")
                row = cur.fetchone()
                if row and row[0] == digest:
                    return False
            cur.execute(sql)
            cur.execute("CREATE TABLE IF NOT EXISTS schema_state (one boolean PRIMARY KEY DEFAULT true CHECK (one), "
                        "digest text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())")
            cur.execute("INSERT INTO schema_state (digest) VALUES (%s) "
                        "ON CONFLICT (one) DO UPDATE SET digest = EXCLUDED.digest, applied_at = now()", (digest,))
            conn.commit()
            return True
        except BaseException:
            conn.rollback()  # so the lock can be released below
            raise
        finally:
            cur.execute("SELECT pg_advisory_unlock(424242)")
            conn.commit()
