"""Polite HTTP client for the public ERATV web application (eratv.era.europa.eu).

ERATV has no API. What this uses, all anonymous and read-only:

* `GET /Eratv/Home/View/{Type ID or internal id}`: the type's page. For a published
  record it links to its export by internal database id (`ExportVehicleType/{id}`);
  for a record that is not public (draft, cancelled) the link is `ExportVehicleType/0`;
  an id without a record redirects to an error page;
* `GET /Eratv/Home/ExportVehicleType/{id}?exportTo=XML`: the type as XML. It only
  answers with the file once the same session opened the type's page (otherwise it
  sends an HTML page), so every export follows a View. The file name in
  Content-Disposition ends in `_PUBLISHED`, `_DRAFT` or `_CANCELLED`; only published
  records are kept;
* `GET /Eratv/Home/List`: the first page of the list (15 rows), newest "Last Update"
  first, used for the daily changes.

**ERATV protects these pages with a CAPTCHA.** After a few pages a session gets a
CAPTCHA page instead (seen 2026-11-10). The client never tries to get past it: it
raises `CaptchaRequired` and the harvester stops until the next run. Harvesting at
scale therefore needs ERA to give access (an exemption for this client, or a bulk
export); until then `atlas eratv import` loads XML files downloaded some other way.

The client keeps the session cookie. Requests are spaced by `delay` seconds; server errors and the web application
firewall's "Request Rejected" page make it back off.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import httpx

from .parse import state_from_filename

log = logging.getLogger(__name__)

BASE = "https://eratv.era.europa.eu"
_VIEW_EXPORT = re.compile(r"ExportVehicleType/(\d+)\?exportTo=XML", re.I)
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_TYPE_ID = re.compile(r"Home/View/([0-9A-Za-z-]+)")


class Blocked(RuntimeError):
    """ERATV keeps refusing requests (firewall page or repeated server errors)."""


class CaptchaRequired(Blocked):
    """ERATV answered with its CAPTCHA page. Never solved or bypassed: the run stops."""


def is_captcha(html: bytes | str) -> bool:
    if isinstance(html, bytes):
        html = html.decode("utf-8", "replace")
    return "/Captcha/" in html or "Enter the CAPTCHA" in html


@dataclass
class Export:
    internal_id: int
    type_id: str | None
    state: str | None          # PUBLISHED | DRAFT | CANCELLED | ... ; None: nothing at this id
    xml: bytes | None


@dataclass
class ListRow:
    type_id: str
    ein: str
    name: str
    status: str
    last_update: str


def parse_list(html: str) -> list[ListRow]:
    rows = []
    for tr in _ROW.findall(html):
        m = _TYPE_ID.search(tr)
        if not m:
            continue
        cells = [re.sub(r"\s+", " ", _TAG.sub("", c)).strip() for c in _CELL.findall(tr)]
        if len(cells) < 5:
            continue
        rows.append(ListRow(m.group(1), cells[1], cells[2], cells[3], cells[4]))
    return rows


def internal_id_from_view(html: str) -> int | None:
    m = _VIEW_EXPORT.search(html)
    return int(m.group(1)) if m else None


class Client:
    def __init__(self, base: str = BASE, delay: float = 2.0, user_agent: str | None = None,
                 timeout: float = 60, transport: httpx.BaseTransport | None = None, sleep=time.sleep):
        ua = user_agent or "rinf-atlas (+https://github.com/Matdata-eu/rinf-atlas)"
        self.base, self.delay, self._sleep = base.rstrip("/"), delay, sleep
        self.http = httpx.Client(base_url=self.base, headers={"User-Agent": ua}, timeout=timeout,
                                 follow_redirects=False, transport=transport)
        self._last = 0.0
        self.requests = 0

    def close(self) -> None:
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        backoff = max(30.0, self.delay * 10)
        for attempt in range(5):
            wait = self._last + self.delay - time.monotonic()
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            self.requests += 1
            try:
                r = self.http.get(path, params=params)
            except httpx.TransportError as e:
                log.warning("ERATV %s failed (%s), retrying in %.0fs", path, e, backoff)
                self._sleep(backoff)
                backoff *= 2
                continue
            if r.status_code == 200 and "html" in r.headers.get("content-type", "") and is_captcha(r.content):
                raise CaptchaRequired(f"ERATV asks for a CAPTCHA on {path}; harvesting needs ERA's access")
            if r.status_code >= 500 or r.status_code == 429 or b"Request Rejected" in r.content[:400]:
                if attempt == 4:
                    break
                log.warning("ERATV %s answered %s, backing off %.0fs", path, r.status_code, backoff)
                self._sleep(backoff)
                backoff *= 2
                continue
            return r
        raise Blocked(f"ERATV keeps refusing {path}")

    def latest(self) -> list[ListRow]:
        """The first page of the list: the 15 types updated last, newest first."""
        r = self._get("/Eratv/Home/List")
        r.raise_for_status()
        return parse_list(r.text)

    def view(self, key: str | int) -> int | None:
        """Opens a type's page (by Type ID or internal id). The internal id of its export
        when it is published, 0 when the record is not public, None when there is none."""
        r = self._get(f"/Eratv/Home/View/{key}")
        if r.status_code != 200:
            return None
        iid = internal_id_from_view(r.text)
        return 0 if iid is None else iid

    def internal_id(self, type_id: str) -> int | None:
        iid = self.view(type_id)
        return iid or None

    def export(self, internal_id: int, viewed: bool = False) -> Export:
        """The XML of a record. Opens its page first unless `viewed` (see the module doc)."""
        if not viewed:
            iid = self.view(internal_id)
            if iid is None:
                return Export(internal_id, None, None, None)
            if iid == 0:
                return Export(internal_id, None, "NOT_PUBLIC", None)
        r = self._get(f"/Eratv/Home/ExportVehicleType/{internal_id}", {"exportTo": "XML"})
        if r.status_code in (301, 302, 303, 404):
            return Export(internal_id, None, None, None)
        r.raise_for_status()
        tid, state = state_from_filename(r.headers.get("content-disposition"))
        if state and "xml" in r.headers.get("content-type", "").lower():
            return Export(internal_id, tid, state, r.content)
        return Export(internal_id, None, None, None)
