"""Vehicle compatibility: compile the rules of shapes/compatibility.ttl (as published by
the worker in compat_def) and a vehicle profile to SQL over the indexed facets.

Every rule plugs the vehicle's value(s) into a SHACL constraint component that is
applied to the infrastructure values of one or more facets. Per track (or section)
and characteristic the result is 0 = compatible, 1 = unknown (no value indexed),
2 = not compatible. See docs/DESIGN.md for the aggregation to sections of line.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from .query import _jsonpath_key

OK, UNKNOWN, FAIL = 0, 1, 2
STATUS = {OK: "green", UNKNOWN: "yellow", FAIL: "red"}
RESULT = {OK: "ok", UNKNOWN: "unknown", FAIL: "fail"}

# jsonpath filter that finds an infrastructure value violating the constraint
_VIOLATION = {"minInclusive": "<", "maxInclusive": ">", "minExclusive": "<=", "maxExclusive": ">="}


class CompatRequest(BaseModel):
    env: str
    # characteristic key -> value(s); missing, null or empty = not checked
    profile: dict[str, Any] = Field(default_factory=dict)


class ExplainRequest(CompatRequest):
    iri: str | None = None
    id: int | None = None


class ProfileError(ValueError):
    pass


@dataclass
class Check:
    key: str
    applies_to: str
    sql: str
    params: list = field(default_factory=list)


def _pooled(facets: list[str], alias: str) -> tuple[str, list]:
    parts = [f"coalesce({alias}.facets -> %s, '[]'::jsonb)" for _ in facets]
    return "(" + " || ".join(parts) + ")", list(facets)


def _number(v: Any, key: str) -> float:
    if isinstance(v, list):
        if len(v) != 1:
            raise ProfileError(f"{key}: expected one number")
        v = v[0]
    if isinstance(v, bool):
        raise ProfileError(f"{key}: expected a number")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ProfileError(f"{key}: expected a number, got {v!r}") from None
    if not math.isfinite(f):
        raise ProfileError(f"{key}: expected a finite number")
    return f


def _terms(v: Any, key: str, kind: str) -> list:
    vals = v if isinstance(v, list) else [v]
    out = []
    for x in vals:
        if kind == "boolean" and not isinstance(x, bool):
            raise ProfileError(f"{key}: expected true or false")
        if kind == "terms" and not isinstance(x, str):
            raise ProfileError(f"{key}: expected IRIs or strings")
        if x not in out:
            out.append(x)
    return out


def is_empty(v: Any) -> bool:
    return v is None or v == "" or (isinstance(v, list) and not [x for x in v if x is not None and x != ""])


def compile_profile(rules: list[dict], profile: dict[str, Any]) -> tuple[list[Check], list[str]]:
    """SQL CASE expressions (0/1/2) for the filled characteristics, and the ignored keys.
    Track checks read the track as alias f, section checks the section as alias s."""
    by_key = {r["key"]: r for r in rules}
    ignored = sorted(k for k in profile if k not in by_key)
    checks: list[Check] = []
    for r in rules:
        v = profile.get(r["key"])
        if is_empty(v):
            continue
        key, c = r["key"], r["constraint"]
        alias = "f" if r["applies_to"] == "track" else "s"
        if c in _VIOLATION:
            num = _number(v, key)
            has, fail, params_h, params_f = [], [], [], []
            for fk in r["facets"]:
                jk = _jsonpath_key(fk)
                has.append(f"{alias}.facets @? %s::jsonpath")
                params_h.append(f'$.{jk}[*] ? (@.type() == "number")')
                fail.append(f"{alias}.facets @? %s::jsonpath")
                params_f.append(f"$.{jk}[*] ? (@ {_VIOLATION[c]} {num!r})")
            sql = (f"CASE WHEN NOT ({' OR '.join(has)}) THEN {UNKNOWN} "
                   f"WHEN ({' OR '.join(fail)}) THEN {FAIL} ELSE {OK} END")
            checks.append(Check(key, r["applies_to"], sql, params_h + params_f))
            continue
        vals = _terms(v, key, r["kind"])
        pooled, pp = _pooled(r["facets"], alias)
        if c == "in":  # every infrastructure value is one of the vehicle's
            sql = (f"CASE WHEN jsonb_array_length({pooled}) = 0 THEN {UNKNOWN} "
                   f"WHEN {pooled} <@ %s::jsonb THEN {OK} ELSE {FAIL} END")
            params = pp + pp + [json.dumps(vals)]
        elif all(isinstance(x, str) for x in vals):  # hasValue, string values
            sql = (f"CASE WHEN jsonb_array_length({pooled}) = 0 THEN {UNKNOWN} "
                   f"WHEN {pooled} ?| %s::text[] THEN {OK} ELSE {FAIL} END")
            params = pp + pp + [vals]
        else:  # hasValue, booleans
            sql = (f"CASE WHEN jsonb_array_length({pooled}) = 0 THEN {UNKNOWN} "
                   f"WHEN EXISTS (SELECT 1 FROM jsonb_array_elements({pooled}) e(v) WHERE e.v = ANY(%s::jsonb[])) "
                   f"THEN {OK} ELSE {FAIL} END")
            params = pp + pp + [[json.dumps(x) for x in vals]]
        checks.append(Check(key, r["applies_to"], sql, params))
    return checks, ignored


def _worst(checks: list[Check]) -> tuple[str, list]:
    if not checks:
        return str(OK), []
    if len(checks) == 1:
        return checks[0].sql, list(checks[0].params)
    return "GREATEST(" + ", ".join(c.sql for c in checks) + ")", [p for c in checks for p in c.params]


def status_sql(cdef: dict, checks: list[Check], env: str, sol_iri: str | None = None) -> tuple[str, list]:
    """SELECT id, iri, status for every section of line of env (or one)."""
    track = [c for c in checks if c.applies_to == "track"]
    section = [c for c in checks if c.applies_to == "section"]
    t_sql, t_p = _worst(track)
    s_sql, s_p = _worst(section)
    one = " AND s.iri = %s" if sol_iri else ""
    one_t = " AND l.target = %s" if sol_iri else ""
    if track:
        sql = f"""
            WITH t AS (
                SELECT l.target AS sol, min({t_sql}) AS st
                FROM feature_link l
                JOIN feature f ON f.env = l.env AND f.iri = l.iri AND f.class = l.class
                WHERE l.env = %s AND l.rule = 'from' AND l.class = ANY(%s){one_t}
                GROUP BY l.target)
            SELECT s.id, s.iri, GREATEST(coalesce(t.st, {UNKNOWN}), {s_sql}) AS status
            FROM feature s LEFT JOIN t ON t.sol = s.iri
            WHERE s.env = %s AND s.class = %s{one}"""
        params = t_p + [env, cdef["track_classes"]] + ([sol_iri] if sol_iri else []) + s_p + [env, cdef["section_class"]]
    else:
        sql = f"SELECT s.id, s.iri, {s_sql} AS status FROM feature s WHERE s.env = %s AND s.class = %s{one}"
        params = s_p + [env, cdef["section_class"]]
    if sol_iri:
        params.append(sol_iri)
    return sql, params


def track_detail_sql(cdef: dict, checks: list[Check], env: str, sol_iri: str) -> tuple[str, list]:
    """Per track of one section: each characteristic's result."""
    track = [c for c in checks if c.applies_to == "track"]
    cols = ", ".join(f"{c.sql} AS c{i}" for i, c in enumerate(track)) or "NULL AS c0"
    params = [p for c in track for p in c.params]
    # DISTINCT: a track linked by both era:isPartOf and era:hasPart has two link rows.
    sql = f"""SELECT DISTINCT ON (f.label, f.iri, f.class) f.id, f.iri, f.label, f.class, f.facets, {cols}
              FROM feature_link l
              JOIN feature f ON f.env = l.env AND f.iri = l.iri AND f.class = l.class
              WHERE l.env = %s AND l.rule = 'from' AND l.class = ANY(%s) AND l.target = %s
              ORDER BY f.label, f.iri, f.class"""
    return sql, params + [env, cdef["track_classes"], sol_iri]


def section_detail_sql(cdef: dict, checks: list[Check], env: str, sol_iri: str) -> tuple[str, list]:
    section = [c for c in checks if c.applies_to == "section"]
    cols = ", ".join(f"{c.sql} AS c{i}" for i, c in enumerate(section)) or "NULL AS c0"
    params = [p for c in section for p in c.params]
    sql = f"""SELECT s.id, s.iri, s.label, s.facets, {cols} FROM feature s
              WHERE s.env = %s AND s.class = %s AND s.iri = %s"""
    return sql, params + [env, cdef["section_class"], sol_iri]
