"""PostgreSQL helpers."""
from __future__ import annotations

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


def migrate(conn: psycopg.Connection, sql_path: Path) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_lock(424242)")
        try:
            cur.execute(sql_path.read_text(encoding="utf-8"))
        finally:
            cur.execute("SELECT pg_advisory_unlock(424242)")
    conn.commit()
