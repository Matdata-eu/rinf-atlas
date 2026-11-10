"""ERATV vehicle types for the vehicle check (docs/eratv.md).

The API never contacts ERATV. A lookup is queued in `eratv_request` and the worker
fetches the type (two requests to ERATV) or reads an uploaded XML export, converts it
with the ERA ontology and stores it in `eratv_type`.

  GET  /api/eratv                         whether lookups are on, how many types are stored
  GET  /api/eratv/types?q=                stored types (name or Type ID)
  GET  /api/eratv/types/{type_id}         one stored type: summary, Turtle, values not converted
  POST /api/eratv/types/{type_id}/fetch   look a type up (stored: answers at once)
  POST /api/eratv/upload?filename=        an XML export downloaded from ERATV (request body)
  GET  /api/eratv/requests/{id}           how a lookup is going
"""
from __future__ import annotations

import os
import re

from fastapi import APIRouter, HTTPException, Query, Request

from . import main as core

router = APIRouter()

LOOKUP = (os.environ.get("ATLAS_ERATV_LOOKUP") or "1").strip().lower() not in ("0", "false", "no", "off")
TYPE_ID = re.compile(r"^\d{2}-\d{3}-\d{4}-\d(?:-\d{3}){1,2}$")
QUEUE_LIMIT = 20
MAX_UPLOAD = 5 * 1024 * 1024
VIEW_URL = "https://eratv.era.europa.eu/Eratv/Home/View/"


def _type_id(raw: str) -> str:
    tid = raw.strip()
    if not TYPE_ID.match(tid):
        raise HTTPException(400, "a Type ID looks like 11-256-0001-3-002-001")
    return tid


def _request(row: dict) -> dict:
    return {"id": row["id"], "typeId": row["type_id"], "status": row["status"], "reason": row["reason"],
            "message": row["message"],
            "requestedAt": row["requested_at"].isoformat() if row["requested_at"] else None,
            "finishedAt": row["finished_at"].isoformat() if row["finished_at"] else None}


COLS = "id, type_id, status, reason, message, requested_at, finished_at"


def _queue(type_id: str | None, xml: bytes | None = None, filename: str | None = None,
           refresh: bool = False) -> dict:
    with core.pool.connection() as c:
        busy = c.execute("SELECT count(*) AS n FROM eratv_request WHERE status IN ('queued','running')").fetchone()
        if busy["n"] >= QUEUE_LIMIT:
            raise HTTPException(429, "many ERATV lookups are waiting; try again in a minute")
        if xml is None:
            same = c.execute(f"""SELECT {COLS} FROM eratv_request WHERE type_id=%s AND xml IS NULL
                                 AND status IN ('queued','running') ORDER BY id LIMIT 1""", [type_id]).fetchone()
            if same:
                return _request(same)
        row = c.execute(f"""INSERT INTO eratv_request (type_id, xml, filename, refresh) VALUES (%s,%s,%s,%s)
                            RETURNING {COLS}""", [type_id, xml, filename, refresh]).fetchone()
        return _request(row)


@router.get("/api/eratv")
def eratv_info():
    n = core._q("SELECT count(*) AS n FROM eratv_type")[0]["n"]
    vocab = core._q("SELECT env, updated_at FROM eratv_vocab WHERE id=1")
    return {"lookup": LOOKUP, "stored": n, "ready": bool(vocab), "source": "https://eratv.era.europa.eu/"}


@router.get("/api/eratv/types")
def eratv_types(q: str = "", limit: int = Query(20, ge=1, le=100)):
    q = q.strip()
    if q:
        rows = core._q("""SELECT type_id, name, category, manufacturer, fetched_at FROM eratv_type
                          WHERE type_id ILIKE %s OR name ILIKE %s OR manufacturer ILIKE %s
                          ORDER BY (type_id ILIKE %s) DESC, similarity(coalesce(name,''), %s) DESC, type_id
                          LIMIT %s""",
                       [f"{q}%", f"%{q}%", f"%{q}%", f"{q}%", q, limit])
    else:
        rows = core._q("""SELECT type_id, name, category, manufacturer, fetched_at FROM eratv_type
                          ORDER BY fetched_at DESC LIMIT %s""", [limit])
    return [{"typeId": r["type_id"], "name": r["name"], "category": r["category"],
             "manufacturer": r["manufacturer"], "fetchedAt": r["fetched_at"].isoformat()} for r in rows]


@router.get("/api/eratv/types/{type_id}")
def eratv_type(type_id: str):
    tid = _type_id(type_id)
    rows = core._q("""SELECT type_id, summary, unmapped, coverage, ttl, fetched_at, converted_at
                      FROM eratv_type WHERE type_id=%s""", [tid])
    if not rows:
        raise HTTPException(404, f"{tid} is not stored; look it up first")
    r = rows[0]
    return {"typeId": r["type_id"], "summary": r["summary"], "unmapped": r["unmapped"], "coverage": r["coverage"],
            "ttl": r["ttl"], "fetchedAt": r["fetched_at"].isoformat(),
            "convertedAt": r["converted_at"].isoformat() if r["converted_at"] else None,
            "url": VIEW_URL + r["type_id"]}


@router.post("/api/eratv/types/{type_id}/fetch")
def eratv_fetch(type_id: str, refresh: bool = False):
    tid = _type_id(type_id)
    if not refresh:
        rows = core._q("SELECT fetched_at FROM eratv_type WHERE type_id=%s", [tid])
        if rows:
            return {"id": None, "typeId": tid, "status": "done", "reason": None, "message": "already stored",
                    "requestedAt": None, "finishedAt": rows[0]["fetched_at"].isoformat()}
    if not LOOKUP:
        raise HTTPException(503, "looking types up in ERATV is turned off here; upload the type's XML export")
    return _queue(tid, refresh=refresh)


@router.post("/api/eratv/upload")
async def eratv_upload(request: Request, filename: str = ""):
    body = await request.body()
    if not body:
        raise HTTPException(400, "send the XML export as the request body")
    if len(body) > MAX_UPLOAD:
        raise HTTPException(413, "an ERATV XML export is smaller than 5 MB")
    if b"<" not in body[:1024]:
        raise HTTPException(400, "this is not an XML file")
    name = os.path.basename(filename)[:200] or None
    if name and re.search(r"_(DRAFT|CANCELLED)\.xml$", name, re.I):
        raise HTTPException(400, "only published types are used: this file is a draft or a cancelled record")
    return _queue(None, xml=body, filename=name)


@router.get("/api/eratv/requests/{rid}")
def eratv_request(rid: int):
    rows = core._q(f"SELECT {COLS} FROM eratv_request WHERE id=%s", [rid])
    if not rows:
        raise HTTPException(404, "no such lookup (they are kept for a day)")
    return _request(rows[0])
