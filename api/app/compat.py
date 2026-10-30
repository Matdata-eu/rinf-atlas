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
# the same, as a test on (infrastructure value, vehicle value)
_VIOLATES = {"minInclusive": lambda i, v: i < v, "maxInclusive": lambda i, v: i > v,
             "minExclusive": lambda i, v: i <= v, "maxExclusive": lambda i, v: i >= v}


class TrainVehicle(BaseModel):
    name: str = ""
    count: int = Field(1, ge=1, le=500)
    # leading (its cab leads the train), pantograph (it runs with a pantograph raised)
    roles: list[str] = Field(default_factory=list)
    values: dict[str, Any] = Field(default_factory=dict)


class Train(BaseModel):
    """A train: its vehicles front to back (identical ones grouped with a count) and the
    values entered for the whole train (combine_over "train", or overriding a combination)."""
    name: str = ""
    vehicles: list[TrainVehicle] = Field(min_length=1, max_length=200)
    values: dict[str, Any] = Field(default_factory=dict)


class CompatRequest(BaseModel):
    env: str
    # characteristic key -> value(s); missing, null or empty = not checked
    profile: dict[str, Any] = Field(default_factory=dict)
    # instead of a profile: a train, combined into one (see combine)
    train: Train | None = None


class CombineRequest(BaseModel):
    train: Train


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
    # The vehicle can do without the system this rule checks (it has the rule's "none"
    # value, e.g. not electrified, here or in its none_from rule): every track is then
    # compatible for this rule, whatever the comparison (sql, kept for the detail) gives.
    bypass: bool = False
    # SQL of the compared values when they are not simply the rule's facets (values of
    # linked features, a record filter), for the detail; with its params.
    values_sql: str | None = None
    values_params: list = field(default_factory=list)

    @property
    def status(self) -> tuple[str, list]:
        """The SQL and params of the result that counts (bypass: always compatible)."""
        return (str(OK), []) if self.bypass else (self.sql, list(self.params))


def effective(check: Check, raw: int) -> int:
    return OK if check.bypass else raw


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


def _implied(rule: dict, alias: str) -> tuple[str, list]:
    """rule.implied as SQL appended to the pooled values: the value a track counts as
    having when another facet has a given value (e.g. ETCS not applicable -> level N)."""
    parts, params = [], []
    for x in rule.get("implied") or []:
        parts.append(f" || CASE WHEN coalesce({alias}.facets -> %s, '[]'::jsonb) ? %s "
                     f"THEN jsonb_build_array(%s::jsonb) ELSE '[]'::jsonb END")
        params += [x["facet"], x["when"], json.dumps(x["value"])]
    return "".join(parts), params


def implied_values(rule: dict, facets: dict) -> list:
    """The values rule.implied adds for a feature with these facets (as _implied)."""
    return [x["value"] for x in rule.get("implied") or [] if x["when"] in (facets.get(x["facet"]) or [])]


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


def option_sets(rule: dict, picked: list) -> tuple[list[str], list[str]]:
    """The track values the vehicle's option(s) of a rule are compatible with, and those that
    make the result unknown (e.g. "allowed under conditions"). Several options (a train
    whose vehicles picked different ones): a value must suit every one of them."""
    by_value = {o["value"]: o for o in rule.get("options") or []}
    ok: set[str] | None = None
    maybe: set[str] | None = None
    every = {x for o in by_value.values() for x in o["matches"] + o["maybe"]}
    for v in picked:
        o = by_value.get(v)
        if o is None:
            raise ProfileError(f"{rule['key']}: {v!r} is not one of the options")
        m = every if o.get("any") else set(o["matches"])
        ok = m if ok is None else ok & m
        maybe = m | set(o["maybe"]) if maybe is None else maybe & (m | set(o["maybe"]))
    ok, maybe = ok or set(), maybe or set()
    return sorted(ok), sorted(maybe - ok)


def _linked(rule: dict, fk_sql: str, params: list) -> tuple[str, list]:
    """The values of the features of rule.linked_class linked to track f ("part of", e.g. its
    tunnels), with their implied values, as one jsonb array."""
    imp, imp_p = _implied(rule, "t")
    sql = (f"(SELECT coalesce(jsonb_agg(e.v), '[]'::jsonb) FROM feature_link lk "
           f"JOIN feature t ON t.env = lk.env AND t.iri = lk.iri AND t.class = lk.class "
           f"CROSS JOIN LATERAL jsonb_array_elements({fk_sql}{imp}) e(v) "
           f"WHERE lk.env = f.env AND lk.rule = 'from' AND lk.class = %s AND lk.target = f.iri)")
    return sql, params + imp_p + [rule["linked_class"]]


def _has_linked(rule: dict) -> tuple[str, list]:
    return ("EXISTS (SELECT 1 FROM feature_link lk WHERE lk.env = f.env AND lk.rule = 'from' "
            "AND lk.class = %s AND lk.target = f.iri)", [rule["linked_class"]])


def _num_sql(v: float) -> str:
    """A number for a jsonpath or SQL literal (finite, from _number)."""
    return repr(float(v))


def _record_row_ok(rule: dict, by_key: dict[str, dict], profile: dict[str, Any], alias: str) -> tuple[str, list]:
    """SQL condition on one record row (jsonb alias) that holds when the row's fields satisfy
    the vehicle's values; a field the row or the vehicle lacks is not checked."""
    conds, params = [], []
    for fx in rule["record"]["fields"]:
        v = profile.get(fx["characteristic"])
        if is_empty(v):
            continue
        num = _number(v, fx["characteristic"])
        op = {"minInclusive": ">=", "maxInclusive": "<=", "minExclusive": ">", "maxExclusive": "<"}[fx["constraint"]]
        conds.append(f"({alias} -> %s IS NULL OR ({alias} ->> %s)::numeric {op} {_num_sql(num)})")
        params += [fx["field"], fx["field"]]
    return (" AND ".join(conds) or "true"), params


def compile_profile(rules: list[dict], profile: dict[str, Any]) -> tuple[list[Check], list[str]]:
    """SQL CASE expressions (0/1/2) for the filled characteristics, and the ignored keys.
    Track checks read the track as alias f (and its section as s), section checks the
    section as alias s.

    A track without a value is unknown, unless the rule says otherwise: with
    when_missing "incompatible" it fails, and when the vehicle accepts the absence of
    the system (none_value, e.g. not electrified, for this rule or its none_from rule)
    it is compatible. For a rule with none_from the system counts as absent only when
    the none_from rule's facets have no value either (an electrified track without a
    contact wire height is unknown, a track without a contact line is not).

    A vehicle that accepts the absence of the system (e.g. a bi-mode vehicle with "not
    electrified") can run on any track without using it, so for those rules every track
    is compatible (Check.bypass); the comparison is kept to tell why in the detail.

    A vehicle that has the rule's without_value (e.g. no ETCS installed) is compared with
    the without_matches values instead (e.g. no ETCS, level 0, NTC): a track with only
    other values requires the system and is not compatible.

    A numeric rule with term_values compares the number each concept on the track stands
    for (e.g. the axle load limit of a line category): the track is compatible when one
    of its concepts satisfies every filled rule with term_values on the same facets (one
    category must allow both the axle load and the mass per metre), not compatible when
    it only has listed concepts that do not, unknown when none of its concepts is listed.

    The other constraints: requires (a track whose value is true requires what the
    vehicle may lack), options (the vehicle's choice stands for the track values it suits),
    record (the row of a record facet that applies at the vehicle's speed), brakingDistance
    (computed from a deceleration) and linked features (e.g. the track's tunnels). Input
    characteristics (e.g. the maximum speed) are only read by other rules."""
    by_key = {r["key"]: r for r in rules}
    # concepts that satisfy every filled term_values rule, per set of compared facets
    allowed: dict[tuple, set[str]] = {}
    for r in rules:
        if r.get("term_values") and not is_empty(profile.get(r["key"])) and r["constraint"] in _VIOLATES:
            num, bad = _number(profile[r["key"]], r["key"]), _VIOLATES[r["constraint"]]
            ok = {t for t, lim in r["term_values"].items() if not bad(lim, num)}
            g = tuple(sorted(r["facets"]))
            allowed[g] = allowed[g] & ok if g in allowed else ok
    ignored = sorted(k for k in profile if k not in by_key)
    checks: list[Check] = []
    for r in rules:
        v = profile.get(r["key"])
        if r["constraint"] == "input":
            if not is_empty(v):
                _number(v, r["key"])
            continue
        if is_empty(v):
            continue
        key, c = r["key"], r["constraint"]
        alias = "f" if r["applies_to"] == "track" else "s"
        fb = set(r.get("section_facets") or [])
        values_sql, values_p = None, []
        if r.get("linked_class"):
            parts, own_p = [], []
            for fk in r["facets"]:
                parts.append("coalesce(t.facets -> %s, '[]'::jsonb)")
                own_p.append(fk)
            own, own_p = _linked(r, "(" + " || ".join(parts) + ")", own_p)
            values_sql, values_p = own, own_p
        else:
            own, own_p = _pooled(r["facets"], alias, fb)
            if r.get("implied"):
                imp, imp_p = _implied(r, alias)
                own, own_p = f"({own}{imp})", own_p + imp_p
        rf = r.get("record_filter")
        if rf and not is_empty(profile.get(rf["select"]["characteristic"])):
            # e.g. only the line categories whose load capability speed is at least the
            # vehicle's maximum speed (a row without a speed applies at any speed)
            spd = _number(profile[rf["select"]["characteristic"]], rf["select"]["characteristic"])
            sel = rf["select"]["field"]
            jp = f"$[*] ? (!exists(@.{sel}) || @.{sel} >= {_num_sql(spd)}).{rf['field']}"
            rec = f"coalesce({alias}.facets -> %s, '[]'::jsonb)"
            own = (f"(CASE WHEN jsonb_array_length({rec}) = 0 THEN {own} "
                   f"ELSE jsonb_path_query_array({rec}, %s::jsonpath) END)")
            own_p = [rf["facet"]] + own_p + [rf["facet"], jp]
            values_sql, values_p = own, own_p
        # what a track without a value gives
        nr = none_rule(r, by_key)
        bypass = nr is not None and bool(nr.get("none_value")) and none_accepted(r, by_key, profile)
        absent = OK if none_accepted(r, by_key, profile) else FAIL if r.get("when_missing") == "incompatible" else UNKNOWN
        if r.get("linked_class"):
            # no linked feature (no tunnel): nothing to comply with; linked ones without a value: unknown
            has, has_p = _has_linked(r)
            missing, missing_p = f"CASE WHEN {has} THEN {UNKNOWN} ELSE {OK} END", has_p
        elif nr is None or absent == UNKNOWN:
            missing, missing_p = str(UNKNOWN), []
        elif nr is r:
            missing, missing_p = str(absent), []
        else:
            sys_vals, sys_p = _pooled(nr["facets"], alias, set(nr.get("section_facets") or []))
            missing, missing_p = f"CASE WHEN jsonb_array_length({sys_vals}) = 0 THEN {absent} ELSE {UNKNOWN} END", sys_p
        head = f"CASE WHEN jsonb_array_length({own}) = 0 THEN {missing} "
        head_p = own_p + missing_p

        def add(sql: str, params: list) -> None:
            checks.append(Check(key, r["applies_to"], sql, params, bypass, values_sql, values_p))

        if c == "requires":
            # the vehicle has the capability: every track is compatible; else a track that
            # requires it (true) is not
            has_it = any(x is True for x in (v if isinstance(v, list) else [v]))
            if not all(isinstance(x, bool) for x in (v if isinstance(v, list) else [v])):
                raise ProfileError(f"{key}: expected true or false")
            if has_it:
                add(str(OK), [])
            else:
                add(head + f"WHEN {own} @> 'true'::jsonb THEN {FAIL} ELSE {OK} END", head_p + own_p)
            continue
        if c == "record":
            # the row that applies at the vehicle's speed (the lowest speed at least the
            # vehicle's; any row without a select characteristic) must allow the vehicle
            ok_sql, ok_p = _record_row_ok(r, by_key, profile, "e.r")
            sel = r["record"].get("select")
            spd = profile.get(sel["characteristic"]) if sel else None
            if sel and not is_empty(spd):
                n = _number(spd, sel["characteristic"])
                f_ = sel["field"]
                row = (f"(SELECT CASE WHEN {ok_sql} THEN {OK} ELSE {FAIL} END FROM jsonb_array_elements({own}) e(r) "
                       f"WHERE e.r -> %s IS NULL OR (e.r ->> %s)::numeric >= {_num_sql(n)} "
                       f"ORDER BY (e.r ->> %s)::numeric NULLS LAST LIMIT 1)")
                add(head + f"ELSE coalesce({row}, {UNKNOWN}) END", head_p + ok_p + own_p + [f_, f_, f_])
            else:
                agg = (f"(SELECT CASE WHEN bool_and({ok_sql}) THEN {OK} WHEN bool_or({ok_sql}) THEN {UNKNOWN} "
                       f"ELSE {FAIL} END FROM jsonb_array_elements({own}) e(r))")
                add(head + f"ELSE {agg} END", head_p + ok_p + ok_p + own_p)
            continue
        if c == "brakingDistance":
            # distance from min(vehicle speed, highest track speed) at the mean deceleration
            dec = _number(v, key)
            if dec <= 0:
                raise ProfileError(f"{key}: the deceleration must be more than 0")
            b = r["braking"]
            spd_vals, spd_p = _pooled(b["speed_facets"], alias, fb | set(b["speed_facets"]) if alias == "f" else set())
            vmax = (f"(SELECT max((e.x)::numeric) FROM jsonb_array_elements({spd_vals}) e(x) "
                    f"WHERE jsonb_typeof(e.x) = 'number')")
            sv = profile.get(b["speed_from"]) if b.get("speed_from") else None
            speed = f"LEAST({_num_sql(_number(sv, b['speed_from']))}, coalesce({vmax}, 'Infinity'::numeric))" if not is_empty(sv) else vmax
            speed_p = spd_p
            dist = f"(power(({speed}) / 3.6, 2) / (2 * {_num_sql(dec)}))"
            caveat, caveat_p = "false", []
            if b.get("caveat_facets"):
                cv, caveat_p = _pooled(b["caveat_facets"], alias, set())
                caveat = f"{cv} @> 'true'::jsonb"
            sql = (head + f"WHEN {caveat} THEN {UNKNOWN} "
                   f"WHEN {speed} IS NULL OR {speed} = 'Infinity'::numeric THEN {UNKNOWN} "
                   f"WHEN EXISTS (SELECT 1 FROM jsonb_array_elements({own}) e(x) WHERE jsonb_typeof(e.x) = 'number' "
                   f"AND (e.x)::numeric < {dist}) THEN {FAIL} ELSE {OK} END")
            add(sql, head_p + caveat_p + speed_p + speed_p + own_p + speed_p)
            continue
        if c in _VIOLATION and r.get("term_values"):
            ok = sorted(allowed[tuple(sorted(r["facets"]))])
            sql = (head + f"WHEN {own} ?| %s::text[] THEN {OK} WHEN {own} ?| %s::text[] THEN {FAIL} ELSE {UNKNOWN} END")
            add(sql, head_p + own_p + [ok] + own_p + [sorted(r["term_values"])])
            continue
        if c in _VIOLATION:
            num = _number(v, key)
            sql = (f"CASE WHEN NOT jsonb_path_exists({own}, '$[*] ? (@.type() == \"number\")') THEN {missing} "
                   f"WHEN jsonb_path_exists({own}, %s::jsonpath) THEN {FAIL} ELSE {OK} END")
            add(sql, own_p + missing_p + own_p + [f"$[*] ? (@ {_VIOLATION[c]} {num!r})"])
            continue
        vals = _terms(v, key, r["kind"])
        if r.get("options"):
            ok_vals, maybe_vals = option_sets(r, vals)
            if all(o["any"] for o in r["options"] if o["value"] in vals):
                add(str(OK), [])  # e.g. no magnetic brake: whatever the track allows
                continue
            if c == "in":  # every infrastructure value suits the vehicle
                sql = head + (f"WHEN {own} <@ %s::jsonb THEN {OK} WHEN {own} <@ %s::jsonb THEN {UNKNOWN} "
                              f"ELSE {FAIL} END")
                add(sql, head_p + own_p + [json.dumps(ok_vals)] + own_p + [json.dumps(ok_vals + maybe_vals)])
            else:
                sql = head + (f"WHEN {own} ?| %s::text[] THEN {OK} WHEN {own} ?| %s::text[] THEN {UNKNOWN} "
                              f"ELSE {FAIL} END")
                add(sql, head_p + own_p + [ok_vals] + own_p + [maybe_vals])
            continue
        if r.get("none_value"):
            vals = [x for x in vals if x != r["none_value"]] or [r["none_value"]]
        wv = r.get("without_value")
        if wv and wv in vals:  # "does not have the system": the track values it runs on
            own_vals = [x for x in vals if x != wv]
            vals = own_vals + [x for x in r.get("without_matches") or [] if x not in own_vals]
        if c == "in":  # every infrastructure value is one of the vehicle's
            sql = head + f"WHEN {own} <@ %s::jsonb THEN {OK} ELSE {FAIL} END"
            params = head_p + own_p + [json.dumps(vals)]
        elif all(isinstance(x, str) for x in vals):  # hasValue, string values
            sql = head + f"WHEN {own} ?| %s::text[] THEN {OK} ELSE {FAIL} END"
            params = head_p + own_p + [vals]
        else:  # hasValue, booleans
            sql = head + (f"WHEN EXISTS (SELECT 1 FROM jsonb_array_elements({own}) e(v) WHERE e.v = ANY(%s::jsonb[])) "
                          f"THEN {OK} ELSE {FAIL} END")
            params = head_p + own_p + [[json.dumps(x) for x in vals]]
        add(sql, params)
    return checks, ignored



def _worst(checks: list[Check]) -> tuple[str, list]:
    if not checks:
        return str(OK), []
    parts = [c.status for c in checks]
    if len(parts) == 1:
        return parts[0]
    return "GREATEST(" + ", ".join(s for s, _ in parts) + ")", [p for _, ps in parts for p in ps]


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


def _detail_cols(checks: list[Check]) -> tuple[str, list]:
    """Per check its result (c<i>) and, where the rule reads other values than its facets,
    those values (v<i>)."""
    cols, params = [], []
    for i, c in enumerate(checks):
        cols.append(f"{c.sql} AS c{i}")
        params += c.params
        if c.values_sql:
            cols.append(f"{c.values_sql} AS v{i}")
            params += c.values_params
    return ", ".join(cols) or "NULL AS c0", params


def track_detail_sql(cdef: dict, checks: list[Check], env: str, sol_iri: str) -> tuple[str, list]:
    """Per track of one section: each characteristic's result of the comparison (before
    Check.bypass; see effective())."""
    track = [c for c in checks if c.applies_to == "track"]
    cols, params = _detail_cols(track)
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
    cols, params = _detail_cols(section)
    sql = f"""SELECT s.id, s.iri, s.label, s.facets, {cols} FROM feature s
              WHERE s.env = %s AND s.class = %s AND s.iri = %s"""
    return sql, params + [env, cdef["section_class"], sol_iri]


# --- explaining a rule in words, generated from its annotations ----------------

PREFIXES = {"era": "http://data.europa.eu/949/", "sh": "http://www.w3.org/ns/shacl#",
            "atlas": "https://rinf-atlas.matdata.eu/ns#", "xsd": "http://www.w3.org/2001/XMLSchema#",
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#"}
# QUDT unit keys (atlas:unit) as written after a number; mirrors web/src/state.ts
UNITS = {"M": "m", "KiloM": "km", "MilliM": "mm", "CentiM": "cm", "M2": "m²", "KiloM-PER-HR": "km/h",
         "M-PER-SEC": "m/s", "M-PER-SEC2": "m/s²", "MilliM-PER-M": "mm/m", "V": "V", "KiloV": "kV", "A": "A",
         "TONNE": "t", "TONNE-PER-M": "t/m", "KiloGM": "kg", "DEG": "°", "SEC": "s", "HZ": "Hz", "W": "W", "KiloW": "kW", "PERCENT": "%"}
# numeric constraint -> (words, symbol, the infrastructure values that fail), "infra <symbol> vehicle"
_COMPARE = {"minInclusive": ("at least", "≥", "lower"), "maxInclusive": ("at most", "≤", "higher"),
            "minExclusive": ("more than", ">", "equal or lower"), "maxExclusive": ("less than", "<", "equal or higher")}


def compact(iri: str) -> str:
    for p, ns in PREFIXES.items():
        rest = iri[len(ns):]
        if iri.startswith(ns) and rest and all(ch.isalnum() or ch in "-_." for ch in rest):
            return f"{p}:{rest}"
    return f"<{iri}>"


def _join(items: list[str], last: str = "and") -> str:
    items = [x for x in items if x]
    if len(items) < 2:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {last} " + items[-1]


def _num(v: float) -> str:
    return f"{v:g}"


def _local_name(iri: str) -> str:
    return iri.rstrip("/").rsplit("/", 1)[-1]


def _quote(text: str) -> str:
    return f"“{text}”"


def explain_rule(rule: dict, by_key: dict[str, dict], track_classes: list[str], section_class: str,
                 labels: dict[str, str] | None = None, class_labels: dict[str, str] | None = None) -> dict:
    """How a rule is checked, in plain language, built from its annotations only (so it
    works for any rule): the vehicle property it reads and one sentence per step, each
    {topic, text} with topic compare, missing, none, fallback, rollup or empty.

    labels: IRI -> label (for the none value); class_labels: class key -> label."""
    labels, class_labels = labels or {}, class_labels or {}
    own = f"the vehicle's {_quote(rule.get('label') or rule['key'])}"
    unit = UNITS.get(rule.get("unit") or "", rule.get("unit") or "")
    infra = rule.get("infra") or [{"key": fk, "label": fk} for fk in rule["facets"]]
    compared = [f for f in infra if f["key"] in rule["facets"]] or infra
    names = _join([_quote(f.get("label") or f["key"]) for f in compared])
    any_names = _join([_quote(f.get("label") or f["key"]) for f in compared], "or")
    track = rule["applies_to"] == "track"
    where = "track" if track else "section of line"
    steps: list[dict] = []

    def say(topic: str, text: str) -> None:
        steps.append({"topic": topic, "text": text})

    c = rule["constraint"]
    pooled = f" The values of {names} are taken together." if len(compared) > 1 else ""
    linked = class_labels.get(rule.get("linked_class") or "", rule.get("linked_class") or "").lower()
    if linked:
        where_has = f"{linked}s that are part of the track"
        pooled += (f" The values are those of the {where_has} (e.g. the track's {linked}s); a track with no "
                   f"{linked} is compatible.")
    if c == "input":
        users = [x for x in by_key.values() if rule["key"] in _reads(x)]
        say("compare", f"Not compared on its own: {_join([_quote(x.get('label') or x['key']) for x in users]) or 'other characteristics'} "
                       f"{'use' if len(users) > 1 else 'uses'} it.")
        say("empty", "Leave it empty and the checks that use it work without it.")
        return {"vehicle_path": " / ".join(compact(p) for p in rule.get("path") or []), "steps": steps}
    flabel = {f["key"]: f.get("label") or f["key"] for f in infra}
    if rule.get("options"):
        opts = []
        for o in rule["options"]:
            if o.get("any"):
                opts.append(f"{_quote(o['label'])} is compatible with every {where}")
                continue
            ok = _join([_quote(labels.get(x) or _local_name(x)) for x in o["matches"]], "or")
            maybe = _join([_quote(labels.get(x) or _local_name(x)) for x in o["maybe"]], "or")
            opts.append(f"{_quote(o['label'])} is compatible with {ok}" + (f", unknown with {maybe}" if maybe else ""))
        every = "every value" if c == "in" else "one value"
        say("compare", f"The vehicle picks what it does: {'; '.join(opts)}. On the {where}, {every} of {names} must "
                       f"suit that choice; any other value is not compatible. Values that allow it only under "
                       f"conditions make the result unknown.{pooled}")
    elif c == "requires":
        say("compare", f"A {where} whose {names} is true requires it: a vehicle without it is not compatible "
                       f"there, and compatible where the value is false. A vehicle that has it is compatible with "
                       f"every {where}.{pooled}")
    elif c == "record":
        rec = rule["record"]
        fields = []
        for fx in rec["fields"]:
            words = {"minInclusive": "at least", "maxInclusive": "at most", "minExclusive": "more than",
                     "maxExclusive": "less than"}[fx["constraint"]]
            ch = by_key.get(fx["characteristic"]) or {"label": fx["characteristic"]}
            fields.append(f"its {_quote(fx['field'])} must be {words} the vehicle's {_quote(ch.get('label') or fx['characteristic'])}")
        sel = rec.get("select")
        if sel:
            sc = by_key.get(sel["characteristic"]) or {"label": sel["characteristic"]}
            which = (f"The row that applies is the one for the lowest {_quote(sel['field'])} at least the vehicle's "
                     f"{_quote(sc.get('label') or sel['characteristic'])} (a row without one applies at any "
                     f"{sel['field']}); without that value, every row must allow the vehicle and the result is "
                     f"unknown when only some do. ")
        else:
            which = "Every row must allow the vehicle. "
        say("compare", f"{names} holds rows. {which}In that row {_join(fields)}; a value the row or the vehicle "
                       f"lacks is not compared. The {where} is unknown when no row applies.")
    elif c == "brakingDistance":
        b = rule["braking"]
        sf = by_key.get(b.get("speed_from") or "")
        spd = _join([_quote(flabel.get(x, x)) for x in b["speed_facets"]], "or")
        speed = (f"the lower of the vehicle's {_quote(sf.get('label') or sf['key'])} and the {where}'s highest {spd}"
                 if sf else f"the {where}'s highest {spd}")
        cav = _join([_quote(flabel.get(x, x)) for x in b.get("caveat_facets") or []], "or")
        say("compare", f"The braking distance is worked out from {speed} at {own} (speed² / 2 × deceleration, "
                       f"speed in m/s). Every {names} value on the {where} must be at least that distance. Without a "
                       f"speed the result is unknown." + (f" A {where} whose {cav} is true is unknown: its "
                                                         f"infrastructure manager has more requirements." if cav else ""))
    elif c == "hasValue":
        say("compare", f"The {where} must offer at least one of the vehicle's values (sh:hasValue): it is "
                       f"compatible when one of its {names} values is one of {own}, and "
                       f"not compatible when none is.{pooled}")
    elif c == "in":
        say("compare", f"Every value on the {where} must be one of the vehicle's (sh:in): it is compatible when "
                       f"each of its {names} values is one of {own}, and not compatible "
                       f"as soon as one is not.{pooled}")
    elif c in _COMPARE and rule.get("term_values"):
        words, sym, _ = _COMPARE[c]
        in_unit = f" in {unit}" if unit else ""
        tv = rule["term_values"]
        named = sorted(((labels.get(t) or _local_name(t), v) for t, v in tv.items()), key=lambda x: (x[1], x[0]))
        table = _join([f"{n} {_num(v)}" for n, v in named])
        say("compare", f"Each {names} value on the {where} stands for a number{in_unit}: {table}. The {where} is "
                       f"compatible when at least one of its values stands for a number {words} {own} "
                       f"(sh:{c}: {sym}), and that same value also satisfies the other characteristics compared "
                       f"with {names} this way. It is not compatible when none does, and unknown when none of its "
                       f"values is in this list.{pooled}")
    elif c in _COMPARE:
        words, sym, bad = _COMPARE[c]
        in_unit = f", in {unit}" if unit else ""
        say("compare", f"Every {names} value on the {where} must be {words} {own} "
                       f"(sh:{c}: {where} value {sym} vehicle value{in_unit}). The {where} is not compatible when "
                       f"one of its values is {bad}.{pooled}")
    else:
        say("compare", f"The {where}'s {names} values are compared with the vehicle's using sh:{c}.")

    # a track (or section) without a value
    reason = f", with the reason {_quote(rule['missing_reason'])}" if rule.get("missing_reason") else ""
    src = by_key.get(rule["none_from"]) if rule.get("none_from") else None
    if linked:
        say("missing", f"A {linked} with no {any_names} value makes the track unknown.")
    elif rule.get("when_missing") == "incompatible" and src is not None:
        say("missing", f"A {where} with no {any_names} value is not compatible{reason}, but only when it has no "
                       f"{_quote(src.get('label') or src['key'])} value either. Where it has one, a missing "
                       f"value is unknown.")
    elif rule.get("when_missing") == "incompatible":
        say("missing", f"A {where} with no {any_names} value is not compatible{reason}.")
    else:
        say("missing", f"A {where} with no {any_names} value is unknown: the data cannot tell.")

    # the vehicle's "none" option (e.g. not electrified)
    owner = src if src is not None else rule
    if owner.get("none_value"):
        nv = owner["none_value"]
        nl = _quote(labels.get(nv) or nv.rstrip("/").rsplit("/", 1)[-1])
        if owner is rule:
            say("none", f"A vehicle that can also do without it (e.g. bi-mode) adds {nl} to its values. It is "
                        f"then compatible with every {where} for this characteristic: where the {where} has no "
                        f"value, and also where its values do not match (the detail then says: Compatible in "
                        f"{nl} mode). {nl} itself is not compared with the {where}'s values.")
        else:
            say("none", f"When the vehicle's {_quote(owner.get('label') or owner['key'])} includes {nl}, it can "
                        f"do without it: every {where} is then compatible for this characteristic, whatever its "
                        f"values (the detail says: Compatible in {nl} mode).")

    # values implied by another facet (e.g. ETCS not applicable -> level N)
    for x in rule.get("implied") or []:
        val = x["value"]
        vl = (labels.get(val) or _local_name(val)) if isinstance(val, str) else ("true" if val is True else
                                                                                 "false" if val is False else str(val))
        say("implied", f"A {linked or where} whose {_quote(x['facet'])} includes "
                       f"{_quote(labels.get(x['when']) or compact(x['when']))} counts as having {_quote(vl)}.")

    # a record filter (e.g. the load capability speed)
    rf = rule.get("record_filter")
    if rf:
        sc = by_key.get(rf["select"]["characteristic"]) or {"label": rf["select"]["characteristic"]}
        say("filter", f"When the vehicle's {_quote(sc.get('label') or rf['select']['characteristic'])} is filled in, "
                      f"only the {_quote(rf['field'])} values of the rows of {_quote(flabel.get(rf['facet'], rf['facet']))} whose "
                      f"{_quote(rf['select']['field'])} is at least that value count (a row without one counts at "
                      f"any {rf['select']['field']}).")

    # the vehicle's "without" option (e.g. no ETCS installed)
    if rule.get("without_value"):
        wl = _quote(rule.get("without_label") or labels.get(rule["without_value"]) or _local_name(rule["without_value"]))
        ml = _join([_quote(labels.get(x) or _local_name(x)) for x in rule.get("without_matches") or []], "or")
        say("without", f"A vehicle without the system picks {wl}. It is then compatible with a {where} that has "
                       f"{ml}, and not compatible with a {where} whose values are all others: that {where} "
                       f"requires the system.")

    # where the value is looked up, and how results make the section's colour
    if track:
        if rule.get("section_facets"):
            fb = [_quote(f.get("label") or f["key"]) for f in infra if f["key"] in rule["section_facets"]]
            say("fallback", f"A track with no {_join(fb, 'or')} value of its own takes the value of its section of "
                            f"line, where RINF records it for some lines.")
        cls = _join([class_labels.get(k, k).lower() for k in track_classes], "or")
        say("rollup", f"Every {cls} of a section of line is checked. Per track the worst result of all "
                      f"characteristics counts (not compatible, then unknown, then compatible). The section of "
                      f"line takes the best track: green when at least one track is compatible on every "
                      f"characteristic, yellow when the best track is unknown or no track is linked to it, red "
                      f"when every track is not compatible.")
    else:
        say("rollup", f"This characteristic is checked on the {class_labels.get(section_class, section_class).lower()} "
                      f"itself, not per track. Its result can only make the colour its tracks give worse.")
    say("train", f"In a train: {combine_phrase(rule)}.")
    say("empty", "Leave it empty and it is not checked.")
    return {"vehicle_path": " / ".join(compact(p) for p in rule.get("path") or []), "steps": steps}


def _reads(rule: dict) -> set[str]:
    """The other characteristics a rule reads (e.g. the maximum speed)."""
    out = {fx["characteristic"] for fx in (rule.get("record") or {}).get("fields", [])}
    out |= {x["select"]["characteristic"] for x in ((rule.get("record") or {}), rule.get("record_filter") or {})
            if x.get("select")}
    if rule.get("braking") and rule["braking"].get("speed_from"):
        out.add(rule["braking"]["speed_from"])
    if rule.get("combine_by") == "spacing":
        out.add(rule.get("length_from") or "trainLength")
    return out - {rule["key"]}


_OVER = {"all": "all vehicles", "pantograph": "the units with a raised pantograph", "leading": "the leading cab",
         "tail": "the last vehicle"}


def combine_phrase(rule: dict) -> str:
    """How a train's value of the characteristic comes from its vehicles, in a few words."""
    over, by = rule.get("combine_over") or "all", combine_by(rule)
    if over == "train":
        return "entered for the whole train"
    who = _OVER.get(over, over)
    if over in ("leading", "tail"):
        return f"the value of {who}"
    return {"sum": f"the sum over {who}" + (" (one each unless filled in)" if rule.get("vehicle_default") else ""),
            "max": f"the highest value among {who}", "min": f"the lowest value among {who}",
            "intersection": f"the values {who} have in common", "union": f"the most demanding choice among {who}",
            "all": f"yes only when {who} have it",
            "spacing": f"the shortest distance between raised pantographs, within a unit or between {who} "
                       f"(their centres, from the vehicles' lengths)"}.get(by, by)


# --- a train: combining its vehicles into one profile ------------------------------

# role names in TrainVehicle.roles
LEADING, PANTOGRAPH = "leading", "pantograph"
NO_COMMON = "urn:rinf-atlas:no-common-value"  # an empty intersection: matches no track value


def combine_by(rule: dict) -> str:
    """How a characteristic of several vehicles makes the train's: the rule's
    combine_by, else what its constraint implies (the most demanding vehicle counts)."""
    if rule.get("combine_by"):
        return rule["combine_by"]
    c = rule["constraint"]
    if rule["kind"] == "boolean":
        return "all"
    if rule.get("options"):
        return "union"
    if rule["kind"] == "terms":
        return "intersection"
    if c in ("minInclusive", "minExclusive"):
        return "max"
    return "min"


def _vehicle_list(train: Train) -> list[tuple[int, TrainVehicle]]:
    """(group index, vehicle) for every vehicle of the train, front to back."""
    return [(i, v) for i, v in enumerate(train.vehicles) for _ in range(v.count)]


def _scope(rule: dict, train: Train) -> list[int]:
    """Indexes of the vehicle groups whose value counts for the rule."""
    over, n = rule.get("combine_over") or "all", len(train.vehicles)
    if over == "leading":
        lead = [i for i, v in enumerate(train.vehicles) if LEADING in v.roles]
        return lead[:1] or [0]
    if over == "tail":
        return [n - 1]
    if over == "pantograph":
        return [i for i, v in enumerate(train.vehicles) if PANTOGRAPH in v.roles]
    if over == "train":
        return []
    return list(range(n))


def _norm(rule: dict, v: Any) -> Any:
    if rule["kind"] == "number":
        return _number(v, rule["key"])
    return _terms(v, rule["key"], rule["kind"])


def _apply(by: str, vals: list) -> Any:
    if by == "sum":
        return sum(vals)
    if by == "max":
        return max(vals)
    if by == "min":
        return min(vals)
    if by == "all":
        return [all(x is True for xs in vals for x in xs)]
    if by == "union":
        return sorted({x for xs in vals for x in xs}, key=str)
    common = set(vals[0])
    for xs in vals[1:]:
        common &= set(xs)
    keep = [x for x in vals[0] if x in common]
    return keep or [NO_COMMON]


def _pantograph_spacing(train: Train, own: list[tuple[int, float]], length_key: str) -> float | None:
    """The shortest distance between raised pantographs: the units' own spacing (a unit with
    two raised) and the distance between the centres of consecutive units with a pantograph
    raised, from the vehicles' lengths."""
    vals = [x for _, x in own]
    pos, at = [], 0.0
    lengths_known = True
    for i, v in _vehicle_list(train):
        ln = v.values.get(length_key)
        if is_empty(ln):
            lengths_known = False
            break
        ln = _number(ln, length_key)
        if PANTOGRAPH in v.roles:
            pos.append(at + ln / 2)
        at += ln
    if lengths_known and len(pos) > 1:
        vals.append(min(b - a for a, b in zip(pos, pos[1:])))
    return min(vals) if vals else None


def combine(rules: list[dict], train: Train) -> tuple[dict[str, Any], dict[str, dict]]:
    """A train as one profile: per characteristic the vehicles that count (combine_over:
    all, those with a pantograph raised, the leading cab, the tail, or a value entered for
    the whole train) and how their values make the train's (combine_by: sum, max, min,
    intersection, union, all). Identical vehicles count once per vehicle for a sum.

    A characteristic is not checked when a vehicle that counts has no value for it (the
    train's value is unknown). Returns the profile and, per characteristic, where its
    value comes from: {"by", "over", "from": groups that set it, "missing": groups without
    a value, "entered": true when the train's own value is used}."""
    profile: dict[str, Any] = {}
    sources: dict[str, dict] = {}
    by_key = {r["key"]: r for r in rules}
    for r in rules:
        key = r["key"]
        by, over = combine_by(r), r.get("combine_over") or "all"
        src = {"by": by, "over": over, "from": [], "missing": []}
        entered = train.values.get(key)
        if not is_empty(entered):
            profile[key] = _norm(r, entered)
            sources[key] = {**src, "entered": True}
            continue
        scope = _scope(r, train)
        default = r.get("vehicle_default")
        # the default (e.g. one raised pantograph per unit) only stands in once the train has
        # several units in scope or a value filled in; a single unit left empty is not checked
        if default is not None and sum(train.vehicles[i].count for i in scope) < 2 \
                and all(is_empty(train.vehicles[i].values.get(key)) for i in scope):
            default = None
        have, missing = [], []
        for i in scope:
            v = train.vehicles[i].values.get(key)
            if is_empty(v) and default is not None:
                v = default
            (missing if is_empty(v) else have).append((i, v))
        if by == "spacing":
            own = [(i, _number(v, key)) for i, v in have]
            val = _pantograph_spacing(train, own, r.get("length_from") or "trainLength")
            if val is not None:
                profile[key] = val
                sources[key] = {**src, "from": scope}
            continue
        if not have:
            continue
        if missing:
            sources[key] = {**src, "missing": [i for i, _ in missing]}
            continue
        vals = [_norm(r, v) for _, v in have]
        counts = [train.vehicles[i].count for i, _ in have]
        if by == "sum":
            result = sum(x * n for x, n in zip(vals, counts))
            setters = [i for i, _ in have]
        else:
            result = _apply(by, vals)
            # the groups that set the value: without them it would be different
            setters = [i for k, (i, _) in enumerate(have)
                       if len(have) == 1 or _apply(by, vals[:k] + vals[k + 1:]) != result]
            # tied vehicles (e.g. two with the same highest axle load): each of them sets it
            setters = setters or [i for k, (i, _) in enumerate(have)
                                  if by in ("intersection", "union", "all") or vals[k] == result]
        profile[key] = result
        sources[key] = {**src, "from": setters}
    for key in list(profile):
        if key not in by_key:
            profile.pop(key)
    return profile, sources


def _numbers(vals: list) -> list[float]:
    return [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]


def check_note(rule: dict, row: dict, profile: dict[str, Any], vals: list, raw: int, rules: dict[str, dict]) -> str | None:
    """A short line for the detail that says what was compared beyond the values: the
    braking distance worked out, the row of a record that applied, a choice allowed only
    under conditions."""
    c = rule["constraint"]
    facets, sec = row.get("facets") or {}, row.get("section_facets") or {}
    if c == "brakingDistance":
        b = rule["braking"]
        if any(True in (facets.get(fk) or []) for fk in b.get("caveat_facets") or []):
            return "The infrastructure manager has more braking requirements for this track"
        speeds = _numbers([v for fk in b["speed_facets"] for v in (facets.get(fk) or sec.get(fk) or [])])
        sv = profile.get(b.get("speed_from") or "")
        cand = ([max(speeds)] if speeds else []) + ([] if is_empty(sv) else [_number(sv, b["speed_from"])])
        if not cand:
            return "No speed to work out the braking distance from"
        v = min(cand)
        d = (v / 3.6) ** 2 / (2 * _number(profile[rule["key"]], rule["key"]))
        return f"Braking distance from {v:g} km/h: {d:.0f} m"
    if c == "record" and vals:
        sel = rule["record"].get("select")
        spd = profile.get(sel["characteristic"]) if sel else None
        if sel and not is_empty(spd):
            n = _number(spd, sel["characteristic"])
            rows = [x for x in vals if isinstance(x, dict) and (x.get(sel["field"]) is None or x[sel["field"]] >= n)]
            rows.sort(key=lambda x: (x.get(sel["field"]) is None, x.get(sel["field"]) or 0))
            f = sel["field"]
            if not rows:
                return f"No row of the track covers a {f} of {n:g}"
            at = rows[0].get(f)
            return (f"The row without a {f} applies" if at is None
                    else f"The row for a {f} up to {at:g} applies (the vehicle's is {n:g})")
        return None
    if rule.get("options") and raw == UNKNOWN and vals:
        return "Allowed only under conditions"
    return None
