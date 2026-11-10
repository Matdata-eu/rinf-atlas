"""Harvesting ERATV into PostgreSQL (tables eratv_*, db/init.sql).

* `fetch_types`: given Type IDs, now (CLI, or a user asking for one type).
* `sync_latest`: the newest page of ERATV's list; re-fetches the types whose
  "Last Update" is newer than the stored one. Runs every `sync_hours`.
* `full_pass`: visits every internal id (ExportVehicleType/1, 2, ...), keeps the
  published records and notes what every id answered. Resumable; runs once and then
  every `full_days`, which also catches status changes the list order may miss.
* `reconvert`: the stored XML to RDF again, e.g. after a new ERA ontology.
* `import_files`: XML files downloaded some other way (by hand, or a bulk export
  from ERA); no request to ERATV.

ERATV shows a CAPTCHA after a few pages (client.py). The harvester never tries to
get past it: a run that meets it stops, and the background service waits a day.

Only one harvest runs at a time (advisory lock), whether from the worker loop's
background thread or the CLI.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .. import db, mirror
from ..config import Settings
from . import vocab as vocab_mod
from .client import Blocked, CaptchaRequired, Client
from .convert import convert
from .parse import parse, state_from_filename

log = logging.getLogger(__name__)
LOCK = 777002


# -- vocabulary --------------------------------------------------------------------
def refresh_vocab(cfg: Settings, conn) -> vocab_mod.Vocab | None:
    """Reads the ERATV mapping from the first environment whose mirror has it and keeps a
    copy in eratv_vocab. Only the process that owns the mirror (the worker loop) calls this."""
    for env in cfg.envs.values():
        try:
            store = mirror.store(cfg.mirror_dir, env.key)
        except OSError as e:          # the mirror is open in another process
            log.debug("%s mirror not available: %s", env.key, e)
            continue
        v = vocab_mod.load(store)
        if v.is_empty():
            continue
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO eratv_vocab (id, env, vocab, updated_at) VALUES (1, %s, %s, now())
                           ON CONFLICT (id) DO UPDATE SET env=EXCLUDED.env, vocab=EXCLUDED.vocab, updated_at=now()""",
                        (env.key, json.dumps(vocab_mod.to_json(v))))
        conn.commit()
        return v
    return None


def stored_vocab(conn) -> vocab_mod.Vocab | None:
    with conn.cursor() as cur:
        cur.execute("SELECT vocab FROM eratv_vocab WHERE id=1")
        row = cur.fetchone()
    conn.commit()
    return vocab_mod.from_json(row[0]) if row else None


# -- storing -----------------------------------------------------------------------
def _note_id(conn, internal_id: int, type_id: str | None, state: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO eratv_id (internal_id, type_id, state, checked_at) VALUES (%s,%s,%s,now())
                       ON CONFLICT (internal_id) DO UPDATE SET type_id=EXCLUDED.type_id, state=EXCLUDED.state,
                       checked_at=now()""", (internal_id, type_id, state))


def store(conn, vocab: vocab_mod.Vocab, xml: bytes, internal_id: int | None = None,
          last_update: str | None = None, state: str = "PUBLISHED") -> tuple[str, bool]:
    """Converts and upserts one published record. Returns (Type ID, changed)."""
    sha = hashlib.sha256(xml).hexdigest()
    rec = parse(xml)
    with conn.cursor() as cur:
        cur.execute("SELECT xml_sha, last_update::text FROM eratv_type WHERE type_id=%s", (rec.type_id,))
        old = cur.fetchone()
    lu = last_update or (old[1] if old else None)
    res = convert(rec, vocab, state=state, last_update=lu)
    s = res.summary
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO eratv_type (type_id, type_number, internal_id, state, name, category, subcategory,
                                    manufacturer, last_update, summary, unmapped, coverage, ttl, xml, xml_sha,
                                    fetched_at, converted_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now())
            ON CONFLICT (type_id) DO UPDATE SET
                type_number=EXCLUDED.type_number, internal_id=COALESCE(EXCLUDED.internal_id, eratv_type.internal_id),
                state=EXCLUDED.state, name=EXCLUDED.name, category=EXCLUDED.category,
                subcategory=EXCLUDED.subcategory, manufacturer=EXCLUDED.manufacturer,
                last_update=COALESCE(EXCLUDED.last_update, eratv_type.last_update), summary=EXCLUDED.summary,
                unmapped=EXCLUDED.unmapped, coverage=EXCLUDED.coverage, ttl=EXCLUDED.ttl, xml=EXCLUDED.xml,
                xml_sha=EXCLUDED.xml_sha, fetched_at=now(), converted_at=now()""",
            (rec.type_id, s["typeNumber"], internal_id, state, s["name"], s["category"], s["subcategory"],
             s["manufacturer"], lu, json.dumps(s), json.dumps(res.unmapped), res.coverage, res.turtle(), xml, sha))
    conn.commit()
    return rec.type_id, (old is None or old[0] != sha)


def withdraw(conn, type_id: str, state: str | None) -> None:
    """A type that is no longer published (cancelled, or the id answers nothing any more)."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM eratv_type WHERE type_id=%s", (type_id,))
        if cur.rowcount:
            log.info("ERATV %s is no longer published (%s): removed", type_id, state or "gone")
    conn.commit()


# -- runs --------------------------------------------------------------------------
class Run:
    def __init__(self, conn, kind: str):
        self.conn, self.kind = conn, kind
        self.fetched = self.changed = self.failed = 0
        with conn.cursor() as cur:
            cur.execute("INSERT INTO eratv_run (kind) VALUES (%s) RETURNING id", (kind,))
            self.id = cur.fetchone()[0]
        conn.commit()

    def save(self, client: Client | None, next_id: int | None = None, message: str | None = None,
             done: bool = False) -> None:
        with self.conn.cursor() as cur:
            cur.execute("""UPDATE eratv_run SET requests=%s, fetched=%s, changed=%s, failed=%s,
                           next_id=COALESCE(%s, next_id), message=%s,
                           finished_at=CASE WHEN %s THEN now() ELSE finished_at END WHERE id=%s""",
                        (client.requests if client else 0, self.fetched, self.changed, self.failed, next_id,
                         message, done, self.id))
        self.conn.commit()


def _locked(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK,))
        got = cur.fetchone()[0]
    conn.commit()
    return got


def _unlock(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK,))
    conn.commit()


def fetch_types(conn, client: Client, vocab, type_ids: list[str], last_update: dict[str, str] | None = None,
                run: Run | None = None) -> list[str]:
    """Fetches these Type IDs now. Returns the ones that changed."""
    own = run is None
    run = run or Run(conn, "fetch")
    changed = []
    for tid in type_ids:
        try:
            iid = client.view(tid)                      # also opens the session for the export
            if not iid:
                log.warning("ERATV %s: %s", tid, "not public" if iid == 0 else "no such type")
                withdraw(conn, tid, "NOT_PUBLIC" if iid == 0 else None)
                continue
            ex = client.export(iid, viewed=True)
            _note_id(conn, iid, ex.type_id, ex.state)
            conn.commit()
            if ex.state != "PUBLISHED" or not ex.xml:
                log.warning("ERATV %s: the export answered %s", tid, ex.state or "nothing")
                run.failed += 1
                continue
            got, ch = store(conn, vocab, ex.xml, iid, (last_update or {}).get(tid))
            run.fetched += 1
            if ch:
                run.changed += 1
                changed.append(got)
        except Blocked:
            raise
        except Exception as e:  # noqa: BLE001 - one bad record must not stop the run
            conn.rollback()
            run.failed += 1
            log.warning("ERATV %s failed: %s", tid, e)
    if own:
        run.save(client, done=True)
    return changed


def sync_latest(conn, client: Client, vocab) -> int:
    """Re-fetches the types on the newest list page that changed since we stored them."""
    run = Run(conn, "sync")
    rows = client.latest()
    with conn.cursor() as cur:
        cur.execute("SELECT type_id, last_update::text FROM eratv_type WHERE type_id = ANY(%s)",
                    ([r.type_id for r in rows],))
        known = dict(cur.fetchall())
    conn.commit()
    todo = [r for r in rows if known.get(r.type_id) is None or (r.last_update and r.last_update > known[r.type_id])]
    msg = None
    if rows and len(todo) == len(rows) and known:
        msg = "every row of the newest page changed: older changes may be missed until the next full pass"
        log.warning("ERATV sync: %s", msg)
    fetch_types(conn, client, vocab, [r.type_id for r in todo], {r.type_id: r.last_update for r in rows}, run=run)
    run.save(client, message=msg, done=True)
    log.info("ERATV sync: %d on the newest page, %d fetched, %d changed", len(rows), run.fetched, run.changed)
    return run.changed


def full_pass(conn, client: Client, vocab, start: int | None = None, stop: int | None = None,
              max_gap: int = 1000, should_stop: Callable[[], bool] = lambda: False) -> Run:
    """Visits internal ids from `start` (default: where the last unfinished pass stopped)
    until `stop`, or until `max_gap` ids in a row past the highest known one answer nothing."""
    with conn.cursor() as cur:
        cur.execute("SELECT next_id FROM eratv_run WHERE kind='full' AND finished_at IS NULL AND next_id IS NOT NULL "
                    "ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        cur.execute("SELECT COALESCE(max(internal_id), 0) FROM eratv_id WHERE state IS NOT NULL")
        highest = cur.fetchone()[0]
        # a resumed pass continues in the open run
        cur.execute("UPDATE eratv_run SET finished_at=now(), message='superseded' WHERE kind='full' "
                    "AND finished_at IS NULL")
    conn.commit()
    i = start if start is not None else (row[0] if row else 1)
    run = Run(conn, "full")
    gap = 0
    while not should_stop():
        if stop is not None and i > stop:
            break
        if stop is None and i > highest and gap >= max_gap:
            break
        try:
            ex = client.export(i)
            _note_id(conn, i, ex.type_id, ex.state)
            conn.commit()
            if ex.state is None:
                gap += 1
            else:
                gap = 0
                highest = max(highest, i)
            if ex.state == "PUBLISHED" and ex.xml:
                _, ch = store(conn, vocab, ex.xml, i)
                run.fetched += 1
                run.changed += int(ch)
            elif ex.state == "NOT_PUBLIC":
                with conn.cursor() as cur:       # a type we had that is no longer public
                    cur.execute("SELECT type_id FROM eratv_type WHERE internal_id=%s", (i,))
                    gone = [r[0] for r in cur.fetchall()]
                for tid in gone:
                    withdraw(conn, tid, ex.state)
        except Blocked:
            run.save(client, next_id=i, message="ERATV refused requests; resuming later")
            raise
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            run.failed += 1
            log.warning("ERATV id %d failed: %s", i, e)
        i += 1
        if i % 50 == 0:
            run.save(client, next_id=i)
            log.info("ERATV full pass at id %d: %d published, %d changed", i, run.fetched, run.changed)
    finished = not should_stop()
    run.save(client, next_id=None if finished else i, done=finished,
             message=None if finished else "stopped; resumes at next_id")
    return run


def import_files(conn, vocab, paths) -> tuple[int, int]:
    """Stores XML exports from disk. Returns (stored, skipped: not published or not ERATV)."""
    stored = skipped = 0
    files = []
    for p in map(Path, paths):
        files += sorted(p.glob("*.xml")) if p.is_dir() else [p]
    for f in files:
        _, state = state_from_filename(f.name)
        if state not in (None, "PUBLISHED"):
            skipped += 1
            continue
        try:
            store(conn, vocab, f.read_bytes())
            stored += 1
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            skipped += 1
            log.warning("%s: %s", f, e)
    return stored, skipped


def reconvert(conn, vocab) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT type_id, xml, internal_id, last_update::text, state FROM eratv_type")
        rows = cur.fetchall()
    conn.commit()
    for tid, xml, iid, lu, state in rows:
        try:
            store(conn, vocab, bytes(xml), iid, lu, state)
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            log.warning("ERATV %s: conversion failed: %s", tid, e)
    return len(rows)


def due(conn, kind: str, hours: float) -> bool:
    """Whether a run of this kind is due: never ran, ran longer than `hours` ago, or (full
    pass) one was interrupted and resumes."""
    with conn.cursor() as cur:
        if kind == "full":
            cur.execute("SELECT EXISTS (SELECT 1 FROM eratv_run WHERE kind='full' AND finished_at IS NULL "
                        "AND next_id IS NOT NULL)")
            if cur.fetchone()[0]:
                conn.commit()
                return True
        cur.execute("SELECT max(started_at) FROM eratv_run WHERE kind=%s AND finished_at IS NOT NULL "
                    "AND COALESCE(message, '') <> 'superseded'", (kind,))
        last = cur.fetchone()[0]
    conn.commit()
    return last is None or (time.time() - last.timestamp()) > hours * 3600


# -- background service -----------------------------------------------------------
class EratvService:
    """Background thread of the worker loop: daily list sync and the periodic full pass."""

    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="eratv", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        conn, backoff = None, 300.0
        while not self._stop.is_set():
            try:
                if conn is None or conn.closed:
                    conn = db.connect(self.cfg.database_url)
                self.tick(conn)
                backoff = 300.0
            except CaptchaRequired as e:
                log.warning("%s; trying again in 24 hours", e)
                self._stop.wait(24 * 3600)
            except Blocked as e:
                log.warning("%s; pausing ERATV harvesting for %.0f minutes", e, backoff / 60)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 6 * 3600)
            except Exception:
                log.exception("ERATV harvest failed; retrying in 10 minutes")
                try:
                    conn.rollback()
                except Exception:  # noqa: BLE001
                    conn = None
                self._stop.wait(600)
            self._stop.wait(60)

    def tick(self, conn) -> None:
        e = self.cfg.eratv
        vocab = stored_vocab(conn)
        if vocab is None or vocab.is_empty():
            log.info("ERATV: waiting for an environment's ontology in the mirror")
            return
        if not _locked(conn):
            return
        try:
            with Client(e.base_url, e.delay_seconds, e.user_agent()) as client:
                if due(conn, "full", e.full_days * 24):
                    full_pass(conn, client, vocab, should_stop=self._stop.is_set)
                elif due(conn, "sync", e.sync_hours):
                    sync_latest(conn, client, vocab)
        finally:
            _unlock(conn)


def locked(conn) -> bool:
    """Takes the harvest lock for a CLI command; False when a harvest is running."""
    return _locked(conn)


def unlock(conn) -> None:
    _unlock(conn)
