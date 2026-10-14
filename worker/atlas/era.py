"""Talking to ERA: dataset explorer API and the GraphDB repository.

Only the worker uses this module for bulk download. Searching and the map
never reach ERA; the API's live describe has its own small client.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import Env

log = logging.getLogger(__name__)

UA = {"User-Agent": "rinf-atlas/0.1 (+https://github.com/Matdata-eu)"}


def _client(timeout: float = 120) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(timeout, connect=30), headers=UA, follow_redirects=True)


def _retry(fn, what: str, attempts: int = 4):
    delay = 5
    for i in range(attempts):
        try:
            return fn()
        except (httpx.TransportError, httpx.HTTPStatusError) as e:
            if i == attempts - 1:
                raise
            log.warning("%s failed (%s), retrying in %ss", what, e, delay)
            time.sleep(delay)
            delay *= 3


@dataclass
class Published:
    """One entry of the dataset explorer: the latest dataset of one IM."""
    dataset_id: str
    code: str
    provider: str
    country: str | None
    file_name: str
    published: datetime | None
    type: str
    status: str


def published_datasets(env: Env) -> dict[str, Published]:
    """Latest published dataset per IM organisation code, as the dataset explorer shows it."""
    if not env.datasets_api:
        return {}

    def go():
        with _client() as c:
            r = c.get(f"{env.datasets_api}/dataset/published", params={"latest": "true", "groupBy": "im"})
            r.raise_for_status()
            return r.json()

    out: dict[str, Published] = {}
    for d in _retry(go, f"{env.key} dataset explorer"):
        acc = d.get("account") or {}
        code = acc.get("code")
        if not code:
            continue
        ts = d.get("datePublished") or d.get("dateCreated")
        out[code] = Published(
            dataset_id=d.get("id", ""),
            code=code,
            provider=(acc.get("name") or "").strip(),
            country=(acc.get("country") or {}).get("code"),
            file_name=d.get("fileName") or "",
            published=datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None,
            type=d.get("type") or "",
            status=d.get("status") or "",
        )
    return out


def list_graphs(env: Env) -> list[str]:
    """Named graphs in the repository (RDF4J /contexts)."""
    def go():
        with _client() as c:
            r = c.get(f"{env.sparql}/contexts", headers={"Accept": "application/sparql-results+json"})
            r.raise_for_status()
            return r.json()

    data = _retry(go, f"{env.key} list graphs")
    out = []
    for b in data["results"]["bindings"]:
        v = b.get("contextID") or next(iter(b.values()))
        out.append(v["value"])
    return out


def graph_size(env: Env, graph: str) -> int:
    def go():
        with _client() as c:
            r = c.get(f"{env.sparql}/size", params={"context": f"<{graph}>"})
            r.raise_for_status()
            return int(r.text.strip())
    return _retry(go, f"{env.key} size {graph}")


def download_graph(env: Env, graph: str, dest: Path) -> int:
    """Stream one named graph as N-Triples into dest. Returns bytes written.

    Uses the RDF4J statements endpoint (one fast streaming export); falls back
    to a SPARQL CONSTRUCT for non-GraphDB endpoints.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    def go():
        n = 0
        with _client(timeout=1800) as c:
            with c.stream("GET", f"{env.sparql}/statements",
                          params={"context": f"<{graph}>", "infer": "false"},
                          headers={"Accept": "application/n-triples"}) as r:
                if r.status_code in (404, 405, 406):
                    return None
                r.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
                        n += len(chunk)
        return n

    n = _retry(go, f"{env.key} download {graph}")
    if n is not None:
        return n

    def construct():
        n = 0
        q = f"CONSTRUCT {{ ?s ?p ?o }} WHERE {{ GRAPH <{graph}> {{ ?s ?p ?o }} }}"
        with _client(timeout=3600) as c:
            with c.stream("POST", env.sparql, data={"query": q},
                          headers={"Accept": "application/n-triples"}) as r:
                r.raise_for_status()
                with dest.open("wb") as f:
                    for chunk in r.iter_bytes(1 << 20):
                        f.write(chunk)
                        n += len(chunk)
        return n

    return _retry(construct, f"{env.key} construct {graph}")
