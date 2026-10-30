"""Map colouring ("Colour by" a facet).

A theme is computed once per environment, facet key and data version into the
`theme` / `theme_value` cache tables (db/init.sql), all from PostGIS:

- Features of the classes owning the facet get their own value. A terms or
  boolean facet with several values on one feature is shown as MIXED; a range
  facet shows its highest value.
- Line classes at the top of the "part of" chain (sections of line) that do not
  own the facet are coloured from their line parts (running tracks share their
  geometry): the one value all their parts agree on, MIXED when they differ, the
  highest value for a range facet. Parts without a value are ignored.

Tiles and search then only join `theme_value` on the feature id, so colouring
costs one index lookup per feature at every zoom level.
"""
from __future__ import annotations

import json
import math
import time
from collections.abc import Callable

from fastapi import HTTPException

from .query import split_key

MIXED = "__mixed__"
SLOTS = 8          # categorical palette size; less frequent values share "other values"
RANGE_BINS = 5     # sequential classes for range facets (unless the shapes give atlas:colorClasses)


def nice(x: float) -> float:
    """Round to two significant digits (87.3 -> 87, 1234 -> 1200, 0.0456 -> 0.046)."""
    if x == 0 or not math.isfinite(x):
        return x
    d = math.floor(math.log10(abs(x))) - 1
    r = round(x / 10 ** d) * 10 ** d
    return float(round(r, max(0, -d)))


def make_breaks(lo: float, hi: float, distinct: list[float], quantiles: list[float]) -> list[float]:
    """Class boundaries for a range facet: every distinct value its own class when there
    are few, else rounded quantiles. A value v falls in class i = number of breaks <= v."""
    if len(distinct) <= RANGE_BINS:
        return sorted(distinct)[1:]
    out: list[float] = []
    for q in quantiles:
        b = nice(q)
        if lo < b <= hi and (not out or b > out[-1]):
            out.append(b)
    return out


class Themes:
    def __init__(self, q: Callable, connection: Callable, defs: Callable, version: Callable):
        self._q = q                    # (sql, params) -> rows
        self._connection = connection  # pool.connection
        self._defs = defs
        self._version = version        # env -> data version string
        self._mem: dict[tuple[str, str], tuple[float, dict]] = {}

    def resolve(self, key: str) -> tuple[str, str, list[str] | None]:
        """facet key -> (plain key, kind, owner classes or None for every class)."""
        d = self._defs()
        scope, fk = split_key(key)
        kind = d["kinds"].get(fk)
        if kind is None or kind == "text" or (scope and scope not in d["owners"][fk]):
            raise HTTPException(400, f"cannot colour by {key!r}: not a terms, boolean or range facet")
        owners = {scope} if scope else d["owners"][fk]
        return fk, kind, (None if "*" in owners else sorted(owners))

    def fixed_classes(self, key: str) -> list[dict] | None:
        """atlas:colorClasses of the facet: [{up_to, label}], the first owner class that has them."""
        fk, kind, owners = self.resolve(key)
        if kind != "range":
            return None
        defs = [f for f in self._defs()["facets"] if f["key"] == fk and f.get("color_classes")
                and (owners is None or f["class"] in owners)]
        return min(defs, key=lambda f: f["class"])["color_classes"] if defs else None

    def get(self, env: str, key: str) -> dict:
        """The theme row for (env, key), computed if missing or stale."""
        hit = self._mem.get((env, key))
        if hit and time.time() - hit[0] < 10:
            return hit[1]
        # Colour classes change without a reindex: they are part of the theme's version.
        fixed = self.fixed_classes(key)
        version = self._version(env) + (f"|{json.dumps(fixed, sort_keys=True)}" if fixed else "")
        rows = self._q("SELECT * FROM theme WHERE env=%s AND key=%s", [env, key])
        if not rows or rows[0]["version"] != version:
            self._compute(env, key, version)
            rows = self._q("SELECT * FROM theme WHERE env=%s AND key=%s", [env, key])
        t = rows[0]
        self._mem[(env, key)] = (time.time(), t)
        if len(self._mem) > 200:
            self._mem.clear()
        return t

    def _rollup_classes(self, env: str, owners: list[str]) -> list[str]:
        """Line classes that parts of the owner classes belong to and that are not parts
        of another line class themselves (sections of line, not tracks)."""
        d = self._defs()
        lines = {c["class"] for c in d["classes"] if c["geometry"] == "line"}
        parts = [o for o in owners if o in lines]
        if not parts:
            return []
        cand = [r["class"] for r in self._q("""
            SELECT DISTINCT p.class FROM feature_link l
            JOIN feature p ON p.env = l.env AND p.iri = l.target
            WHERE l.env = %s AND l.rule = 'from' AND l.class = ANY(%s)""", [env, parts])]
        out = []
        for c in cand:
            if c not in lines or c in owners:
                continue
            nested = self._q("""
                SELECT 1 FROM feature_link l JOIN feature p ON p.env = l.env AND p.iri = l.target
                WHERE l.env = %s AND l.rule = 'from' AND l.class = %s AND p.class = ANY(%s) LIMIT 1""",
                [env, c, sorted(lines)])
            if not nested:
                out.append(c)
        return sorted(out)

    def _compute(self, env: str, key: str, version: str) -> None:
        fk, kind, owners = self.resolve(key)
        rollup = self._rollup_classes(env, owners) if owners else []
        lines = sorted(c["class"] for c in self._defs()["classes"] if c["geometry"] == "line")
        if kind == "range":
            agg = "NULL, max((val #>> '{}')::float8) FILTER (WHERE jsonb_typeof(val) = 'number')"
            having = "HAVING max((val #>> '{}')::float8) FILTER (WHERE jsonb_typeof(val) = 'number') IS NOT NULL"
        else:
            agg = f"CASE WHEN count(DISTINCT val) > 1 THEN '{MIXED}' ELSE min(val #>> '{{}}') END, NULL"
            having = ""
        own_cls = "TRUE" if owners is None else "f.class = ANY(%(owners)s)"
        up = ""
        if rollup:
            up = """UNION ALL
                SELECT p.id, e.value FROM feature c
                JOIN feature_link l ON l.env = c.env AND l.iri = c.iri AND l.class = c.class AND l.rule = 'from'
                JOIN feature p ON p.env = l.env AND p.iri = l.target AND p.class = ANY(%(rollup)s)
                CROSS JOIN LATERAL jsonb_array_elements(c.facets -> %(fk)s) e
                WHERE c.env = %(env)s AND c.class = ANY(%(owners)s) AND c.class = ANY(%(lines)s)"""
        params = {"env": env, "key": key, "fk": fk, "owners": owners or [], "rollup": rollup, "lines": lines}
        with self._connection() as conn:
            with conn.transaction():
                # One computation per theme at a time; concurrent tile requests wait for it.
                conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"atlas-theme|{env}|{key}"])
                cur = conn.execute("SELECT version FROM theme WHERE env=%s AND key=%s", [env, key]).fetchall()
                if cur and cur[0]["version"] == version:
                    return
                conn.execute("DELETE FROM theme WHERE env=%s AND key=%s", [env, key])
                tid = conn.execute("""INSERT INTO theme (env, key, version, kind, classes, rollup)
                                      VALUES (%s,%s,%s,%s,%s,%s) RETURNING id""",
                                   [env, key, version, kind, owners or ["*"], rollup]).fetchone()["id"]
                params["tid"] = tid
                conn.execute(f"""
                    INSERT INTO theme_value (theme, fid, v, n)
                    SELECT %(tid)s, fid, {agg} FROM (
                        SELECT f.id AS fid, e.value AS val FROM feature f
                        CROSS JOIN LATERAL jsonb_array_elements(f.facets -> %(fk)s) e
                        WHERE f.env = %(env)s AND {own_cls}
                        {up}
                    ) x GROUP BY fid {having}""", params)
                fixed = self.fixed_classes(key) if kind == "range" else None
                if fixed:
                    r = conn.execute("SELECT min(n) lo, max(n) hi FROM theme_value WHERE theme = %(tid)s",
                                     params).fetchone()
                    # upper: class i holds values > breaks[i-1] and <= breaks[i] (atlas:upTo is inclusive)
                    conn.execute("UPDATE theme SET breaks=%s::jsonb, lo=%s, hi=%s WHERE id=%s",
                                 [json.dumps({"breaks": [c["up_to"] for c in fixed[:-1]], "discrete": False,
                                              "upper": True, "labels": [c.get("label") for c in fixed]}),
                                  r["lo"], r["hi"], tid])
                elif kind == "range":
                    r = conn.execute("""
                        SELECT min(n) lo, max(n) hi,
                               percentile_disc(ARRAY[0.2, 0.4, 0.6, 0.8]) WITHIN GROUP (ORDER BY n) qs,
                               (SELECT array_agg(DISTINCT n) FROM (SELECT DISTINCT n FROM theme_value
                                 WHERE theme = %(tid)s LIMIT 6) d) distinct_n
                        FROM theme_value WHERE theme = %(tid)s""", params).fetchone()
                    distinct = r["distinct_n"] or []
                    breaks = make_breaks(r["lo"], r["hi"], distinct, r["qs"] or []) if r["lo"] is not None else []
                    # discrete: so few values that each is its own class (breaks = the values but the lowest)
                    conn.execute("UPDATE theme SET breaks=%s::jsonb, lo=%s, hi=%s WHERE id=%s",
                                 [json.dumps({"breaks": breaks, "discrete": len(distinct) <= RANGE_BINS}),
                                  r["lo"], r["hi"], tid])
                else:
                    # Palette slots follow the most frequent values of the whole environment,
                    # so filtering never repaints a value. Booleans: yes, then no.
                    if kind == "boolean":
                        slots = ["true", "false"]
                    else:
                        slots = [r["v"] for r in conn.execute(f"""
                            SELECT v FROM theme_value WHERE theme = %s AND v <> '{MIXED}'
                            GROUP BY v ORDER BY count(*) DESC, v LIMIT {SLOTS}""", [tid]).fetchall()]
                    conn.execute("UPDATE theme SET slots=%s::jsonb WHERE id=%s", [json.dumps(slots), tid])
        self._mem.pop((env, key), None)

    def coloured_classes(self, t: dict) -> list[str] | None:
        """Classes the theme colours (None = every class)."""
        if t["classes"] == ["*"]:
            return None
        return list(t["classes"]) + list(t["rollup"])
