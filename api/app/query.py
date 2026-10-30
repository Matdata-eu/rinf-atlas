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
    # Tiles only: classes drawn at every zoom, below their own min_zoom.
    all_zoom: list[str] = Field(default_factory=list)


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


# Line classes whose features are made of the features of other classes (feature_link
# rule 'from'): a plain filter on a facet of the parts also narrows these, to the
# features with a matching part. A section of line with an ETCS level 2 running track
# matches "ETCS level 2", as in map colouring and the vehicle check.
ROLLUP: dict[str, tuple[str, ...]] = {"section-of-line": ("running-track", "track")}


def rollup_parts(cls: str, owners: set[str], rollup: dict[str, tuple[str, ...]] = ROLLUP) -> list[str]:
    """The part classes through which a facet owned by `owners` reaches `cls` (none when
    `cls` owns it itself or is not a rollup class)."""
    if "*" in owners or cls in owners:
        return []
    return sorted(set(rollup.get(cls, ())) & owners)


class Where:
    """Builds the WHERE clause. facet_classes maps facet key -> class keys having
    that facet ('*' means every class): a filter only constrains those classes,
    so "operational points of type station + all tunnels" works. Plain (unscoped)
    filters also constrain the ROLLUP classes through their parts."""

    def __init__(self, state: QueryState, facet_classes: dict[str, set[str]],
                 rollup: dict[str, tuple[str, ...]] | None = None):
        self.state = state
        self.facet_classes = facet_classes
        self.rollup = ROLLUP if rollup is None else rollup

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
                via = {} if scope else {r: rollup_parts(r, owners, self.rollup) for r in self.rollup}
                via = {r: pcs for r, pcs in via.items() if pcs and (not s.classes or r in s.classes)}
                ors, op = [], []
                for r, part_classes in via.items():
                    pc, pp = self._facet_cond(fkey, val, "p.")
                    t = alias or "feature"
                    ors.append(f"({t}.class = %s AND EXISTS (SELECT 1 FROM feature_link l JOIN feature p "
                               f"ON p.env = l.env AND p.iri = l.iri AND p.class = l.class "
                               f"WHERE l.env = {t}.env AND l.target = {t}.iri AND l.rule = 'from' "
                               f"AND l.class = ANY(%s) AND {pc}))")
                    op += [r, part_classes] + pp
                cond = f"({a}class <> ALL(%s) OR {cond}{''.join(' OR ' + o for o in ors)})"
                cp = [sorted(owners | set(via))] + cp + op
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
