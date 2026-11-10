"""Looking up one ERATV vehicle type when a user asks for it in the web app.

The API queues a request in `eratv_request` (a Type ID to fetch, or an XML export the
user uploaded) and this background thread of the worker does it, so every request to
ERATV goes through one place: one session per type (its page, then its export: two
requests), at most `lookups_per_hour` of them across all users.

ERATV shows a CAPTCHA after a few pages. Lookups never try to get past it: the request
fails with reason `captcha`, lookups pause for an hour, and the web app offers to
upload the XML export instead (downloaded by hand from the type's ERATV page).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque

from .. import db
from ..config import Settings
from . import vocab as vocab_mod
from .client import Blocked, CaptchaRequired, Client
from .harvest import _note_id, store, withdraw
from .parse import parse, state_from_filename

log = logging.getLogger(__name__)

TYPE_ID = re.compile(r"^\d{2}-\d{3}-\d{4}-\d(?:-\d{3}){1,2}$")
RECENT = 3600            # a refresh within this many seconds of the last fetch returns the stored type
CAPTCHA_PAUSE = 3600
BLOCKED_PAUSE = 900

MSG = {
    "captcha": "ERATV asked for a CAPTCHA, which RINF Atlas does not answer. Open the type in ERATV, "
               "download its XML export and upload it here.",
    "blocked": "ERATV is not answering at the moment. Try again later, or upload the type's XML export.",
}


class Failed(Exception):
    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason, self.message = reason, message


class Lookups:
    """Does one queued request at a time; the state lives here (rate, pauses, vocabulary)."""

    def __init__(self, cfg: Settings, client_factory=None, clock=time.time):
        self.cfg = cfg
        e = cfg.eratv
        self.client_factory = client_factory or (lambda: Client(e.base_url, e.delay_seconds, e.user_agent()))
        self.clock = clock
        self.fetches: deque[float] = deque()
        self.paused_until = 0.0
        self.paused_reason = ""
        self._vocab: tuple[str | None, vocab_mod.Vocab | None] = (None, None)

    # -- queue -------------------------------------------------------------------------
    def claim(self, conn) -> dict | None:
        with conn.cursor() as cur:
            cur.execute("""UPDATE eratv_request SET status='running'
                           WHERE id = (SELECT id FROM eratv_request WHERE status='queued' ORDER BY id
                                       FOR UPDATE SKIP LOCKED LIMIT 1)
                           RETURNING id, type_id, xml, filename, refresh""")
            row = cur.fetchone()
        conn.commit()
        if row is None:
            return None
        return dict(zip(("id", "type_id", "xml", "filename", "refresh"), row, strict=True))

    def finish(self, conn, rid: int, status: str, type_id: str | None = None, reason: str | None = None,
               message: str | None = None) -> None:
        with conn.cursor() as cur:
            cur.execute("""UPDATE eratv_request SET status=%s, type_id=COALESCE(%s, type_id), reason=%s, message=%s,
                                  xml=NULL, finished_at=now() WHERE id=%s""",
                        (status, type_id, reason, message, rid))
        conn.commit()

    def sweep(self, conn) -> None:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM eratv_request WHERE requested_at < now() - interval '1 day'")
            cur.execute("""UPDATE eratv_request SET status='failed', reason='error',
                                  message='the lookup was interrupted; try again', finished_at=now()
                           WHERE status='running' AND requested_at < now() - interval '15 minutes'""")
        conn.commit()

    def step(self, conn) -> bool:
        """Does the oldest queued request. False when there was none."""
        row = self.claim(conn)
        if row is None:
            return False
        try:
            tid, msg = self.handle(conn, row)
            self.finish(conn, row["id"], "done", tid, None, msg)
        except Failed as f:
            conn.rollback()
            log.info("ERATV lookup %s: %s", row.get("type_id") or row.get("filename") or row["id"], f.message)
            self.finish(conn, row["id"], "failed", None, f.reason, f.message)
        except Exception as e:  # noqa: BLE001 - one bad request must not stop the others
            conn.rollback()
            log.exception("ERATV lookup %s failed", row["id"])
            self.finish(conn, row["id"], "failed", None, "error", f"the lookup failed: {e}")
        return True

    # -- one request -------------------------------------------------------------------
    def vocab(self, conn) -> vocab_mod.Vocab:
        with conn.cursor() as cur:
            cur.execute("SELECT updated_at::text FROM eratv_vocab WHERE id=1")
            row = cur.fetchone()
        conn.commit()
        if row is None:
            raise Failed("error", "the ERA ontology is not loaded yet; try again once an environment is indexed")
        if self._vocab[0] != row[0]:
            with conn.cursor() as cur:
                cur.execute("SELECT vocab FROM eratv_vocab WHERE id=1")
                v = vocab_mod.from_json(cur.fetchone()[0])
            conn.commit()
            self._vocab = (row[0], v)
        return self._vocab[1]

    def handle(self, conn, row: dict) -> tuple[str, str | None]:
        if row["xml"] is not None:
            return self.upload(conn, bytes(row["xml"]), row["filename"])
        return self.fetch(conn, row["type_id"], bool(row["refresh"]))

    def upload(self, conn, xml: bytes, filename: str | None) -> tuple[str, str | None]:
        _, state = state_from_filename(f'attachment; filename="{filename}"') if filename else (None, None)
        if state and state != "PUBLISHED":
            raise Failed("not_public", f"this file is a {state.lower()} record; only published types are used")
        try:
            rec = parse(xml)
        except Exception as e:  # noqa: BLE001
            raise Failed("invalid", f"this is not an ERATV XML export ({e})") from None
        if not rec.type_id:
            raise Failed("invalid", "this is not an ERATV XML export (no Type ID)")
        tid, _ = store(conn, self.vocab(conn), xml)
        conn.commit()
        return tid, "read from the uploaded file"

    def fetch(self, conn, tid: str | None, refresh: bool) -> tuple[str, str | None]:
        if not tid or not TYPE_ID.match(tid):
            raise Failed("invalid", "a Type ID looks like 11-256-0001-3-002-001")
        if not self.cfg.eratv.lookup:
            raise Failed("disabled", "looking types up in ERATV is turned off here; upload the type's XML export")
        with conn.cursor() as cur:
            cur.execute("SELECT extract(epoch FROM now() - fetched_at) FROM eratv_type WHERE type_id=%s", (tid,))
            row = cur.fetchone()
        conn.commit()
        if row is not None and (not refresh or row[0] < RECENT):
            return tid, "already stored"
        vocab = self.vocab(conn)
        now = self.clock()
        if now < self.paused_until:
            raise Failed(self.paused_reason, MSG[self.paused_reason])
        while self.fetches and self.fetches[0] < now - 3600:
            self.fetches.popleft()
        if len(self.fetches) >= self.cfg.eratv.lookups_per_hour:
            wait = int((self.fetches[0] + 3600 - now) / 60) + 1
            raise Failed("limit", f"RINF Atlas looked up many types in ERATV in the last hour; try again in "
                                  f"{wait} minutes, or upload the type's XML export")
        self.fetches.append(now)
        try:
            with self.client_factory() as client:      # a new session for every type
                iid = client.view(tid)
                if iid is None:
                    raise Failed("not_found", f"ERATV has no type {tid}")
                if iid == 0:
                    withdraw(conn, tid, "NOT_PUBLIC")
                    raise Failed("not_public", f"{tid} is not public in ERATV (a draft or a cancelled type)")
                ex = client.export(iid, viewed=True)
        except CaptchaRequired:
            self._pause("captcha", CAPTCHA_PAUSE)
            raise Failed("captcha", MSG["captcha"]) from None
        except Blocked:
            self._pause("blocked", BLOCKED_PAUSE)
            raise Failed("blocked", MSG["blocked"]) from None
        _note_id(conn, iid, ex.type_id, ex.state)
        conn.commit()
        if ex.state != "PUBLISHED" or not ex.xml:
            raise Failed("not_public", f"ERATV did not export {tid} as a published type ({ex.state or 'nothing'})")
        got, _ = store(conn, vocab, ex.xml, iid)
        conn.commit()
        return got, None

    def _pause(self, reason: str, seconds: float) -> None:
        self.paused_until = self.clock() + seconds
        self.paused_reason = reason
        log.warning("ERATV lookups paused for %d minutes (%s)", seconds // 60, reason)


class LookupService:
    """Background thread of the worker loop: does the queued lookups as they come."""

    def __init__(self, cfg: Settings):
        self.cfg = cfg
        self.lookups = Lookups(cfg)
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="eratv-lookup", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        conn, last_sweep = None, 0.0
        while not self._stop.is_set():
            try:
                if conn is None or conn.closed:
                    conn = db.connect(self.cfg.database_url)
                if time.time() - last_sweep > 600:
                    self.lookups.sweep(conn)
                    last_sweep = time.time()
                if self.lookups.step(conn):
                    continue
            except Exception:
                log.exception("ERATV lookups failed; retrying")
                try:
                    if conn is not None:
                        conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = None
                self._stop.wait(30)
            self._stop.wait(1.5)
