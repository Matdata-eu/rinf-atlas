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
    # The vehicle can do without the system this rule checks (it has the rule's "none"
    # value, e.g. not electrified, here or in its none_from rule): every track is then
    # compatible for this rule, whatever the comparison (sql, kept for the detail) gives.
    bypass: bool = False

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
                     f"THEN jsonb_build_array(%s::text) ELSE '[]'::jsonb END")
        params += [x["facet"], x["when"], x["value"]]
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
    it only has listed concepts that do not, unknown when none of its concepts is listed."""
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
        if is_empty(v):
            continue
        key, c = r["key"], r["constraint"]
        alias = "f" if r["applies_to"] == "track" else "s"
        fb = set(r.get("section_facets") or [])
        own, own_p = _pooled(r["facets"], alias, fb)
        if r.get("implied"):
            imp, imp_p = _implied(r, alias)
            own, own_p = f"({own}{imp})", own_p + imp_p
        # what a track without a value gives
        nr = none_rule(r, by_key)
        bypass = nr is not None and bool(nr.get("none_value")) and none_accepted(r, by_key, profile)
        absent = OK if none_accepted(r, by_key, profile) else FAIL if r.get("when_missing") == "incompatible" else UNKNOWN
        if nr is None or absent == UNKNOWN:
            missing, missing_p = str(UNKNOWN), []
        elif nr is r:
            missing, missing_p = str(absent), []
        else:
            sys_vals, sys_p = _pooled(nr["facets"], alias, set(nr.get("section_facets") or []))
            missing, missing_p = f"CASE WHEN jsonb_array_length({sys_vals}) = 0 THEN {absent} ELSE {UNKNOWN} END", sys_p
        if c in _VIOLATION and r.get("term_values"):
            ok = sorted(allowed[tuple(sorted(r["facets"]))])
            sql = (f"CASE WHEN jsonb_array_length({own}) = 0 THEN {missing} "
                   f"WHEN {own} ?| %s::text[] THEN {OK} WHEN {own} ?| %s::text[] THEN {FAIL} ELSE {UNKNOWN} END")
            checks.append(Check(key, r["applies_to"], sql,
                                own_p + missing_p + own_p + [ok] + own_p + [sorted(r["term_values"])], bypass))
            continue
        if c in _VIOLATION:
            num = _number(v, key)
            sql = (f"CASE WHEN NOT jsonb_path_exists({own}, '$[*] ? (@.type() == \"number\")') THEN {missing} "
                   f"WHEN jsonb_path_exists({own}, %s::jsonpath) THEN {FAIL} ELSE {OK} END")
            checks.append(Check(key, r["applies_to"], sql,
                                own_p + missing_p + own_p + [f"$[*] ? (@ {_VIOLATION[c]} {num!r})"], bypass))
            continue
        vals = _terms(v, key, r["kind"])
        if r.get("none_value"):
            vals = [x for x in vals if x != r["none_value"]] or [r["none_value"]]
        wv = r.get("without_value")
        if wv and wv in vals:  # "does not have the system": the track values it runs on
            own_vals = [x for x in vals if x != wv]
            vals = own_vals + [x for x in r.get("without_matches") or [] if x not in own_vals]
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
        checks.append(Check(key, r["applies_to"], sql, params, bypass))
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


def track_detail_sql(cdef: dict, checks: list[Check], env: str, sol_iri: str) -> tuple[str, list]:
    """Per track of one section: each characteristic's result of the comparison (before
    Check.bypass; see effective())."""
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
    names = _join([_quote(f.get("label") or f["key"]) for f in infra])
    any_names = _join([_quote(f.get("label") or f["key"]) for f in infra], "or")
    track = rule["applies_to"] == "track"
    where = "track" if track else "section of line"
    steps: list[dict] = []

    def say(topic: str, text: str) -> None:
        steps.append({"topic": topic, "text": text})

    c = rule["constraint"]
    pooled = f" The values of {names} are taken together." if len(infra) > 1 else ""
    if c == "hasValue":
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
    if rule.get("when_missing") == "incompatible" and src is not None:
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
        say("implied", f"A {where} whose {_quote(x['facet'])} includes {_quote(labels.get(x['when']) or compact(x['when']))} "
                       f"counts as having {_quote(labels.get(x['value']) or _local_name(x['value']))}.")

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
    say("empty", "Leave it empty and it is not checked.")
    return {"vehicle_path": " / ".join(compact(p) for p in rule.get("path") or []), "steps": steps}
