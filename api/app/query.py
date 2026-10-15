"""Query state -> SQL. Shared by facets, search, extent and tiles."""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field


class Range(BaseModel):
    min: float | None = None
    max: float | None = None


def split_key(key: str) -> tuple[str | None, str]:
    """'operational-point/opType' -> ('operational-point', 'opType'): a filter or facet
    scoped to one class. A plain key applies to every class that has the facet."""
    if "/" in key:
        cls, fkey = key.split("/", 1)
        return cls, fkey
    return None, key


class QueryState(BaseModel):
    env: str
    classes: list[str] = Field(default_factory=list)
    filters: dict[str, list[Any] | Range] = Field(default_factory=dict)
    q: str | None = None
    bbox: list[float] | None = None
    # Restrict to features from these provider graphs (the UI's "show dataset on map").
    graphs: list[str] = Field(default_factory=list)
    # Optional: only compute these facets (default: shared + selected classes' facets).
    facets: list[str] | None = None


class SearchRequest(QueryState):
    limit: int = Field(50, ge=1, le=500)
    offset: int = Field(0, ge=0)
    # Optional facet key the map is coloured by: items then carry its display value `v`.
    color: str | None = None


class ThemeRequest(QueryState):
    key: str


_TOKEN = re.compile(r"[\w]+", re.UNICODE)


def tsquery(q: str) -> str | None:
    toks = _TOKEN.findall(q.lower())
    if not toks:
        return None
    return " & ".join(f"{t}:*" for t in toks[:8])


def _jsonpath_key(key: str) -> str:
    return '"' + key.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Where:
    """Builds the WHERE clause. facet_classes maps facet key -> class keys having
    that facet ('*' means every class): a filter only constrains those classes,
    so "operational points of type station + all tunnels" works."""

    def __init__(self, state: QueryState, facet_classes: dict[str, set[str]]):
        self.state = state
        self.facet_classes = facet_classes

    def build(self, *, exclude_facet: str | None = None, with_classes: bool = True,
              with_bbox: bool = True, alias: str = "f") -> tuple[str, list]:
        s = self.state
        a = alias + "." if alias else ""
        parts = [f"{a}env = %s"]
        params: list = [s.env]
        if with_classes and s.classes:
            parts.append(f"{a}class = ANY(%s)")
            params.append(list(s.classes))
        if s.graphs:
            parts.append(f"{a}graph_iri = ANY(%s)")
            params.append(list(s.graphs))
        if with_bbox and s.bbox and len(s.bbox) == 4:
            parts.append(f"{a}geom && ST_MakeEnvelope(%s, %s, %s, %s, 4326)")
            params += [float(x) for x in s.bbox]
        if s.q and s.q.strip():
            q = s.q.strip()
            if q.startswith("http://") or q.startswith("https://"):
                parts.append(f"{a}iri = %s")
                params.append(q)
            else:
                tq = tsquery(q)
                if tq:
                    parts.append(f"({a}search @@ to_tsquery('simple', unaccent(%s)) OR {a}label ILIKE %s)")
                    params += [tq, f"%{q}%"]
                else:
                    parts.append(f"{a}label ILIKE %s")
                    params.append(f"%{q}%")
        for key, val in s.filters.items():
            if key == exclude_facet:
                continue
            scope, fkey = split_key(key)
            cond, cp = self._facet_cond(fkey, val, a)
            if not cond:
                continue
            owners = {scope} if scope else self.facet_classes.get(fkey, set())
            if "*" not in owners and owners:
                cond = f"({a}class <> ALL(%s) OR {cond})"
                cp = [sorted(owners)] + cp
            parts.append(cond)
            params += cp
        return " AND ".join(parts), params

    @staticmethod
    def _facet_cond(key: str, val, a: str) -> tuple[str | None, list]:
        if isinstance(val, Range) or isinstance(val, dict):
            r = val if isinstance(val, Range) else Range(**val)
            conds = []
            if r.min is not None:
                conds.append(f"@ >= {float(r.min)!r}")
            if r.max is not None:
                conds.append(f"@ <= {float(r.max)!r}")
            if not conds:
                return None, []
            return f"{a}facets @? %s::jsonpath", [f"$.{_jsonpath_key(key)}[*] ? ({' && '.join(conds)})"]
        if not val:
            return None, []
        ors = []
        params = []
        for v in val:
            ors.append(f"{a}facets @> %s::jsonb")
            params.append(json.dumps({key: [v]}))
        return "(" + " OR ".join(ors) + ")", params
