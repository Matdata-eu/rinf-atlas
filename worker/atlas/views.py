"""Evaluate the requirement checks of the views (shapes/views/*.ttl) on the index.

Each check compares facets the extraction already indexed, so it runs in PostgreSQL
only: no mirror, no reindex. The result is written into `feature.facets` under the
check's key, as one of three IRIs (atlas:Meets, atlas:NoData, atlas:DoesNotMeet), so
filters, facet counts, map colouring, the detail panel and exports treat it like any
other facet. A feature the check does not apply to (atlas:appliesWhen) gets no value.

- Track classes (atlas:checkClass) are checked on their own values.
- The view's summary key holds the worst result of the checks that apply.
- The section class (atlas:checkSection) gets, per key, the best result of its parts
  (feature_link rule 'from'): a section of line meets a requirement when one of its
  running tracks does, as in the vehicle check.

The checks are read from view_def (written by publish_defs), so this runs wherever the
facet cache is refreshed: after a sync, a reindex, a validation or `atlas load-file`.
"""
from __future__ import annotations

import json
import logging
import time

import psycopg

from .shapes import CHECK_FAILS, CHECK_LABELS, CHECK_MEETS, CHECK_NO_DATA, views_digest

log = logging.getLogger(__name__)

MEETS, NO_DATA, FAILS = 0, 1, 2
RESULT_IRI = {MEETS: CHECK_MEETS, NO_DATA: CHECK_NO_DATA, FAILS: CHECK_FAILS}
# jsonpath filter that finds a value violating a numeric constraint
_VIOLATION = {"minInclusive": "<", "maxInclusive": ">", "minExclusive": "<=", "maxExclusive": ">="}
_VIOLATES = {"minInclusive": lambda v, b: v < b, "maxInclusive": lambda v, b: v > b,
             "minExclusive": lambda v, b: v <= b, "maxExclusive": lambda v, b: v >= b}


def _pooled(chk: dict) -> tuple[str, list]:
    """The compared values of a feature (alias f) as one jsonb array, implied values included."""
    parts, params = [], []
    for fk in chk["facets"]:
        parts.append("coalesce(f.facets -> %s, '[]'::jsonb)")
        params.append(fk)
    for x in chk.get("implied") or []:
        parts.append("CASE WHEN coalesce(f.facets -> %s, '[]'::jsonb) ? %s THEN jsonb_build_array(%s::text) "
                     "ELSE '[]'::jsonb END")
        params += [x["facet"], x["when"], x["value"]]
    return "(" + " || ".join(parts) + ")", params


def status_sql(chk: dict) -> tuple[str, list]:
    """SQL giving 0 (meets), 1 (no data), 2 (does not meet) or NULL (does not apply) for feature f."""
    own, p = _pooled(chk)
    missing = str(FAILS if chk.get("when_missing") == "fails" else NO_DATA)
    c, vals = chk["constraint"], chk["values"]
    if c in _VIOLATION and chk.get("term_values"):
        # concepts standing for a number: meets when one of them satisfies the bound
        bound, bad = vals[0], _VIOLATES[c]
        ok = sorted(t for t, n in chk["term_values"].items() if not bad(n, bound))
        sql = (f"CASE WHEN jsonb_array_length({own}) = 0 THEN {missing} WHEN {own} ?| %s::text[] THEN {MEETS} "
               f"WHEN {own} ?| %s::text[] THEN {FAILS} ELSE {NO_DATA} END")
        params = p + p + [ok] + p + [sorted(chk["term_values"])]
    elif c in _VIOLATION:
        sql = (f"CASE WHEN NOT jsonb_path_exists({own}, '$[*] ? (@.type() == \"number\")') THEN {missing} "
               f"WHEN jsonb_path_exists({own}, %s::jsonpath) THEN {FAILS} ELSE {MEETS} END")
        params = p + p + [f"$[*] ? (@.type() == \"number\" && @ {_VIOLATION[c]} {float(vals[0])!r})"]
    else:
        head = f"CASE WHEN jsonb_array_length({own}) = 0 THEN {missing} "
        if c == "in":  # every value is in the list
            sql = head + f"WHEN {own} <@ %s::jsonb THEN {MEETS} ELSE {FAILS} END"
            params = p + p + [json.dumps(vals)]
        elif c == "notIn":  # some value is not in the list
            sql = head + (f"WHEN EXISTS (SELECT 1 FROM jsonb_array_elements({own}) e(v) WHERE NOT (e.v <@ %s::jsonb)) "
                          f"THEN {MEETS} ELSE {FAILS} END")
            params = p + p + [json.dumps(vals)]
        else:  # hasValue: one of the values
            sql = head + (f"WHEN EXISTS (SELECT 1 FROM jsonb_array_elements({own}) e(v) WHERE e.v <@ %s::jsonb) "
                          f"THEN {MEETS} ELSE {FAILS} END")
            params = p + p + [json.dumps(vals)]
    conds, cp = [], []
    for w in chk.get("applies_when") or []:
        conds.append("EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(f.facets -> %s, '[]'::jsonb)) e(v) "
                     "WHERE e.v <@ %s::jsonb)")
        cp += [w["facet"], json.dumps(w["values"])]
    if conds:
        return f"CASE WHEN {' AND '.join(conds)} THEN {sql} END", cp + params
    return sql, params


def _result_json(expr: str) -> str:
    """0/1/2 -> a one-value jsonb array with the result IRI (NULL stays NULL)."""
    return (f"CASE {expr} WHEN {MEETS} THEN %s::jsonb WHEN {NO_DATA} THEN %s::jsonb "
            f"WHEN {FAILS} THEN %s::jsonb END")


_RESULT_PARAMS = [json.dumps([RESULT_IRI[MEETS]]), json.dumps([RESULT_IRI[NO_DATA]]), json.dumps([RESULT_IRI[FAILS]])]


def _number(key_sql: str) -> str:
    """A part's stored result -> 0/1/2 (NULL when it has none)."""
    return (f"CASE p.facets -> {key_sql} ->> 0 WHEN '{CHECK_MEETS}' THEN {MEETS} "
            f"WHEN '{CHECK_NO_DATA}' THEN {NO_DATA} WHEN '{CHECK_FAILS}' THEN {FAILS} END")


def _write(cur, env: str, classes: list[str], keys: list[str], select_sql: str, params: list) -> int:
    """Replace `keys` in the facets of the features select_sql returns (id, obj)."""
    cur.execute(f"""
        WITH r AS ({select_sql})
        UPDATE feature f SET facets = (f.facets - %s::text[]) || r.obj
        FROM r WHERE f.id = r.id
          AND (f.facets - %s::text[]) || r.obj IS DISTINCT FROM f.facets""", params + [keys, keys])
    return cur.rowcount


def apply_view(cur, env: str, view: dict) -> int:
    checks = view["checks"]
    if not checks:
        return 0
    keys = [c["key"] for c in checks] + ([view["summary_key"]] if view.get("summary_key") else [])
    # one row per checked feature: its results as a jsonb object (no key where it does not apply)
    exprs, params = [], []
    for c in checks:
        sql, p = status_sql(c)
        exprs.append(f"({sql})")
        params += p
    cols = ", ".join(f"{e} AS c{i}" for i, e in enumerate(exprs))
    pairs, pp = [], []
    for i, c in enumerate(checks):
        pairs.append(f"%s::text, {_result_json(f'c{i}')}")
        pp += [c["key"]] + _RESULT_PARAMS
    if view.get("summary_key"):
        # GREATEST ignores NULLs: the worst result of the checks that apply
        pairs.append(f"%s::text, {_result_json('GREATEST(' + ', '.join(f'c{i}' for i in range(len(checks))) + ')')}")
        pp += [view["summary_key"]] + _RESULT_PARAMS
    select = f"""SELECT id, jsonb_strip_nulls(jsonb_build_object({', '.join(pairs)})) AS obj
                 FROM (SELECT f.id, {cols} FROM feature f WHERE f.env = %s AND f.class = ANY(%s)) x"""
    n = _write(cur, env, view["check_classes"], keys, select, pp + params + [env, view["check_classes"]])
    sc = view.get("section_class")
    if sc:
        # sections: the best result of their parts, per key
        aggs, ap = [], []
        for k in keys:
            aggs.append(f"%s::text, {_result_json('min(' + _number('%s') + ')')}")
            ap += [k, k] + _RESULT_PARAMS
        select = f"""SELECT s.id, jsonb_strip_nulls(jsonb_build_object({', '.join(aggs)})) AS obj
                     FROM feature s
                     LEFT JOIN feature_link l ON l.env = s.env AND l.target = s.iri AND l.rule = 'from'
                          AND l.class = ANY(%s)
                     LEFT JOIN feature p ON p.env = l.env AND p.iri = l.iri AND p.class = l.class
                     WHERE s.env = %s AND s.class = %s
                     GROUP BY s.id"""
        n += _write(cur, env, [sc], keys, select, ap + [view["check_classes"], env, sc])
    return n


def apply_checks(conn: psycopg.Connection, env: str) -> None:
    """Evaluate every view's checks for env (see the module docstring). Commits."""
    t0 = time.time()
    with conn.cursor() as cur:
        cur.execute("SELECT def FROM view_def ORDER BY position, key")
        views = [r[0] for r in cur.fetchall()]
        # results that no view checks any more (a check removed or renamed) go
        cur.execute("SELECT DISTINCT key FROM facet_cache WHERE env = %s AND value::text = ANY(%s)",
                    (env, [json.dumps(i) for i in RESULT_IRI.values()]))
        current = {c["key"] for v in views for c in v["checks"]} | {v["summary_key"] for v in views if v.get("summary_key")}
        gone = sorted({r[0] for r in cur.fetchall()} - current)
        if gone:
            cur.execute("UPDATE feature SET facets = facets - %s::text[] WHERE env = %s AND facets ?| %s",
                        (gone, env, gone))
        n = sum(apply_view(cur, env, v) for v in views)
        cur.executemany("INSERT INTO term (env, iri, label) VALUES (%s,%s,%s) "
                        "ON CONFLICT (env, iri) DO UPDATE SET label = EXCLUDED.label",
                        [(env, iri, label) for iri, label in CHECK_LABELS.items()])
        cur.execute("""INSERT INTO index_state (env, views_hash) VALUES (%s,%s) ON CONFLICT (env)
                       DO UPDATE SET views_hash = EXCLUDED.views_hash""", (env, views_digest(views)))
    conn.commit()
    if views:
        log.info("%s view checks: %d features updated in %.1fs", env, n, time.time() - t0)
