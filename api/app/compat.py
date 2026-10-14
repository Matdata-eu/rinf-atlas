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


def _values(fk: str, alias: str, fallback: bool) -> tuple[str, list]:
    """The infrastructure values of facet fk as a jsonb array: the track's, or (fallback)
    its section's when the track has none. The section is alias s in track queries."""
    if fallback and alias == "f":
        return "coalesce(f.facets -> %s, s.facets -> %s, '[]'::jsonb)", [fk, fk]
    return f"coalesce({alias}.facets -> %s, '[]'::jsonb)", [fk]


def _pooled(facets: list[str], alias: str, fallback: set[str] = frozenset()) -> tuple[str, list]:
    parts, params = [], []
    for fk in facets:
        sql, p = _values(fk, alias, fk in fallback)
        parts.append(sql)
        params += p
    return "(" + " || ".join(parts) + ")", params


def none_accepted(rule: dict, by_key: dict[str, dict], profile: dict[str, Any]) -> bool:
    """Whether the vehicle runs where the rule's system is absent: it has the "none"
    value (e.g. not electrified) for this rule or for the rule named by none_from."""
    src = by_key.get(rule.get("none_from") or "") or rule
    nv = src.get("none_value")
    v = profile.get(src["key"])
    return bool(nv) and not is_empty(v) and nv in (v if isinstance(v, list) else [v])


def none_rule(rule: dict, by_key: dict[str, dict]) -> dict | None:
    """The rule whose facets tell whether the system is absent on a track (e.g. the
    energy supply rule for the contact line), or None when missing data is just unknown."""
    if rule.get("none_from"):
        return by_key[rule["none_from"]]
    if rule.get("none_value") or rule.get("when_missing") == "incompatible":
        return rule
    return None


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
    Track checks read the track as alias f (and its section as s), section checks the
    section as alias s.

    A track without a value is unknown, unless the rule says otherwise: with
    when_missing "incompatible" it fails, and when the vehicle accepts the absence of
    the system (none_value, e.g. not electrified, for this rule or its none_from rule)
    it is compatible. For a rule with none_from the system counts as absent only when
    the none_from rule's facets have no value either (an electrified track without a
    contact wire height is unknown, a track without a contact line is not)."""
    by_key = {r["key"]: r for r in rules}
    ignored = sorted(k for k in profile if k not in by_key)
    checks: list[Check] = []
    for r in rules:
        v = profile.get(r["key"])
        if is_empty(v):
            continue
        key, c = r["key"], r["constraint"]
        alias = "f" if r["applies_to"] == "track" else "s"
        fb = set(r.get("section_facets") or [])
        own, own_p = _pooled(r["facets"], alias, fb)
        # what a track without a value gives
        nr = none_rule(r, by_key)
        absent = OK if none_accepted(r, by_key, profile) else FAIL if r.get("when_missing") == "incompatible" else UNKNOWN
        if nr is None or absent == UNKNOWN:
            missing, missing_p = str(UNKNOWN), []
        elif nr is r:
            missing, missing_p = str(absent), []
        else:
            sys_vals, sys_p = _pooled(nr["facets"], alias, set(nr.get("section_facets") or []))
            missing, missing_p = f"CASE WHEN jsonb_array_length({sys_vals}) = 0 THEN {absent} ELSE {UNKNOWN} END", sys_p
        if c in _VIOLATION:
            num = _number(v, key)
            sql = (f"CASE WHEN NOT jsonb_path_exists({own}, '$[*] ? (@.type() == \"number\")') THEN {missing} "
                   f"WHEN jsonb_path_exists({own}, %s::jsonpath) THEN {FAIL} ELSE {OK} END")
            checks.append(Check(key, r["applies_to"], sql,
                                own_p + missing_p + own_p + [f"$[*] ? (@ {_VIOLATION[c]} {num!r})"]))
            continue
        vals = _terms(v, key, r["kind"])
        if r.get("none_value"):
            vals = [x for x in vals if x != r["none_value"]] or [r["none_value"]]
        head = f"CASE WHEN jsonb_array_length({own}) = 0 THEN {missing} "
        if c == "in":  # every infrastructure value is one of the vehicle's
            sql = head + f"WHEN {own} <@ %s::jsonb THEN {OK} ELSE {FAIL} END"
            params = own_p + missing_p + own_p + [json.dumps(vals)]
        elif all(isinstance(x, str) for x in vals):  # hasValue, string values
            sql = head + f"WHEN {own} ?| %s::text[] THEN {OK} ELSE {FAIL} END"
            params = own_p + missing_p + own_p + [vals]
        else:  # hasValue, booleans
            sql = head + (f"WHEN EXISTS (SELECT 1 FROM jsonb_array_elements({own}) e(v) WHERE e.v = ANY(%s::jsonb[])) "
                          f"THEN {OK} ELSE {FAIL} END")
            params = own_p + missing_p + own_p + [[json.dumps(x) for x in vals]]
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
                JOIN feature s ON s.env = l.env AND s.iri = l.target AND s.class = %s
                WHERE l.env = %s AND l.rule = 'from' AND l.class = ANY(%s){one_t}
                GROUP BY l.target)
            SELECT s.id, s.iri, GREATEST(coalesce(t.st, {UNKNOWN}), {s_sql}) AS status
            FROM feature s LEFT JOIN t ON t.sol = s.iri
            WHERE s.env = %s AND s.class = %s{one}"""
        params = (t_p + [cdef["section_class"], env, cdef["track_classes"]] + ([sol_iri] if sol_iri else [])
                  + s_p + [env, cdef["section_class"]])
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
    sql = f"""SELECT DISTINCT ON (f.label, f.iri, f.class) f.id, f.iri, f.label, f.class, f.facets,
                     s.facets AS section_facets, {cols}
              FROM feature_link l
              JOIN feature f ON f.env = l.env AND f.iri = l.iri AND f.class = l.class
              JOIN feature s ON s.env = l.env AND s.iri = l.target AND s.class = %s
              WHERE l.env = %s AND l.rule = 'from' AND l.class = ANY(%s) AND l.target = %s
              ORDER BY f.label, f.iri, f.class"""
    return sql, params + [cdef["section_class"], env, cdef["track_classes"], sol_iri]


def section_detail_sql(cdef: dict, checks: list[Check], env: str, sol_iri: str) -> tuple[str, list]:
    section = [c for c in checks if c.applies_to == "section"]
    cols = ", ".join(f"{c.sql} AS c{i}" for i, c in enumerate(section)) or "NULL AS c0"
    params = [p for c in section for p in c.params]
    sql = f"""SELECT s.id, s.iri, s.label, s.facets, {cols} FROM feature s
              WHERE s.env = %s AND s.class = %s AND s.iri = %s"""
    return sql, params + [env, cdef["section_class"], sol_iri]
