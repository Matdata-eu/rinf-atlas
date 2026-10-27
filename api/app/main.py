"""RINF Atlas HTTP API (contract: docs/api.md).

Everything is answered from PostGIS, except /api/describe, which is the one
place that goes live to ERA's SPARQL endpoint (describe + graph traversal).
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from . import compat as cx
from . import export as xp
from . import profile as pf
from . import turtle as ttl
from .query import QueryState, SearchRequest, ThemeRequest, Where, split_key
from .theme import MIXED, Themes

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://atlas:atlas@db:5432/atlas")
CONFIG = Path(os.environ.get("ATLAS_CONFIG", "/app/config/environments.yaml"))
TERMS_LIMIT = int(os.environ.get("ATLAS_TERMS_LIMIT", "200"))
LOW_DETAIL_ZOOM = int(os.environ.get("ATLAS_LOW_DETAIL_ZOOM", "8"))
TILE_CACHE_SECONDS = int(os.environ.get("ATLAS_TILE_CACHE_SECONDS", "300"))
# Turtle export queries ERA live (batches of 100 IRIs), so it is capped.
TTL_EXPORT_MAX = int(os.environ.get("ATLAS_TTL_EXPORT_MAX", "20000"))
# A shacl-report instance (https://github.com/Matdata-eu/shacl-report) the UI links to for
# exploring a downloaded report; empty hides the link.
SHACL_REPORT_URL = os.environ.get("ATLAS_SHACL_REPORT_URL", "https://shacl-report.matdata.eu/").strip() or None

SH = "http://www.w3.org/ns/shacl#"
SEVERITY_NAME = {3: "violation", 2: "warning", 1: "info"}
# Worst data quality severity of a feature, from the `quality` facet the worker writes.
QA_EXPR = (f"CASE WHEN f.facets->'quality' ? '{SH}Violation' THEN 3 WHEN f.facets->'quality' ? '{SH}Warning' THEN 2 "
           f"WHEN f.facets->'quality' ? '{SH}Info' THEN 1 WHEN f.facets ? 'quality' THEN 0 END")

pool: ConnectionPool | None = None
_envs: dict[str, dict] = {}
_defs_cache: dict = {"t": 0.0}
# Facet kinds that are stored but never offered as filters: text (search only) and
# link (IRIs of other features, e.g. a section's start and end operational point).
HIDDEN_KINDS = ("text", "link")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    enabled = [s.strip().lower() for s in os.environ.get("ATLAS_ENVS", "").split(",") if s.strip()]
    for k, e in (raw.get("environments") or {}).items():
        if not enabled or k.lower() in enabled:
            _envs[k] = e
    if not _envs:
        raise RuntimeError(f"ATLAS_ENVS={os.environ.get('ATLAS_ENVS')!r} matches no environment in environments.yaml")
    pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=int(os.environ.get("ATLAS_DB_POOL", "10")),
                          kwargs={"row_factory": dict_row}, open=True)
    yield
    pool.close()


app = FastAPI(title="RINF Atlas API", version="0.1.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(GZipMiddleware, minimum_size=1024)


def _env(key: str) -> dict:
    if key not in _envs:
        raise HTTPException(404, f"unknown environment {key!r}")
    return _envs[key]


def _q(sql: str, params=None) -> list[dict]:
    with pool.connection() as c:
        return c.execute(sql, params or []).fetchall()


def _defs() -> dict:
    """class_def / facet_def, cached for a few seconds (they change on reindex only)."""
    if time.time() - _defs_cache["t"] < 10:
        return _defs_cache
    classes = _q("SELECT * FROM class_def ORDER BY position, class")
    facets = _q("SELECT * FROM facet_def ORDER BY position, key")
    owners: dict[str, set[str]] = defaultdict(set)
    for f in facets:
        owners[f["key"]].add(f["class"])
    crow = _q("SELECT def FROM compat_def WHERE id = 1")
    _defs_cache.update(t=time.time(), classes=classes, facets=facets, owners=owners,
                       compat=crow[0]["def"] if crow else None,
                       kinds={f["key"]: f["kind"] for f in facets},
                       min_zoom={c["class"]: c["min_zoom"] for c in classes})
    return _defs_cache


def _labels(env: str, iris: set[str]) -> dict[str, str]:
    iris = {i for i in iris if isinstance(i, str) and (i.startswith("http") or i.startswith("urn:"))}
    if not iris:
        return {}
    rows = _q("SELECT iri, label FROM term WHERE env=%s AND iri = ANY(%s)", [env, list(iris)])
    out = {r["iri"]: r["label"] for r in rows}
    missing = iris - out.keys()
    if missing:
        for r in _q("SELECT DISTINCT ON (iri) iri, label FROM feature WHERE env=%s AND iri = ANY(%s)",
                    [env, list(missing)]):
            if r["label"]:
                out[r["iri"]] = r["label"]
    return out


def _local(iri: str) -> str:
    return iri.rstrip("/").replace("#", "/").rsplit("/", 1)[-1]


@app.get("/api/health")
def health():
    _q("SELECT 1")
    return {"ok": True}


@app.get("/api/envs")
def envs():
    return [{"key": k, "label": e.get("label", k.upper()), "sparql": e["sparql"],
             "portal": e.get("portal"), "default": bool(e.get("default"))} for k, e in _envs.items()]


@app.get("/api/meta")
def meta(env: str):
    _env(env)
    d = _defs()
    counts = {r["class"]: r["n"] for r in _q("SELECT class, count(*) AS n FROM feature WHERE env=%s GROUP BY class", [env])}
    ds = _q("""SELECT count(*) AS total, count(*) FILTER (WHERE status='loaded') AS loaded,
                      count(*) FILTER (WHERE status='failed') AS failed,
                      count(*) FILTER (WHERE status IN ('pending','loading')) AS pending,
                      max(ingested_at) AS last_ingest,
                      (SELECT max(finished_at) FROM ingest_run r WHERE r.env=%s) AS last_check
               FROM dataset WHERE env=%s AND kind='provider' AND status <> 'removed'""", [env, env])[0]
    return {
        "classes": [{"class": c["class"], "class_iri": c["class_iri"], "label": c["label"], "color": c["color"],
                     "geometry": c["geometry"], "min_zoom": c["min_zoom"],
                     "draw_order": c.get("draw_order"), "count": counts.get(c["class"], 0)}
                    for c in d["classes"]],
        "facets": [{"key": f["key"], "class": f["class"], "label": f["label"], "kind": f["kind"],
                    "datatype": f["datatype"], "unit": f["unit"], "position": f["position"],
                    "color_by": f.get("color_by")}
                   for f in d["facets"] if f["kind"] not in HIDDEN_KINDS],
        "limits": {"ttl_export": TTL_EXPORT_MAX},
        "datasets": {"total": ds["total"], "loaded": ds["loaded"], "failed": ds["failed"], "pending": ds["pending"],
                     "last_ingest": ds["last_ingest"].isoformat() if ds["last_ingest"] else None,
                     "last_check": ds["last_check"].isoformat() if ds["last_check"] else None},
        "quality": _quality_meta(env, d),
    }


def _quality_meta(env: str, d: dict) -> dict:
    enabled = any(f["key"] == "quality" for f in d["facets"])
    r = _q("""SELECT count(*) FILTER (WHERE qa_status IN ('ok','partial')) AS validated,
                     coalesce(sum(qa_violations), 0) AS violations, coalesce(sum(qa_warnings), 0) AS warnings,
                     coalesce(sum(qa_infos), 0) AS infos, max(qa_checked_at) AS checked_at,
                     count(*) FILTER (WHERE qa_reason = 'too_large') AS too_large
              FROM dataset WHERE env=%s AND kind='provider' AND status <> 'removed'""", [env])[0]
    s = _q("""SELECT nullif(graph_iri, '') AS graph_iri, loaded_at, source, max_triples, error
              FROM quality_shapes WHERE env=%s""", [env])
    s = s[0] if s else {}
    return {"enabled": enabled, "validated": r["validated"], "violations": r["violations"],
            "warnings": r["warnings"], "infos": r["infos"], "too_large": r["too_large"],
            "checked_at": r["checked_at"].isoformat() if r["checked_at"] else None,
            "shapes_graph": s.get("graph_iri"),
            "shapes_loaded_at": s["loaded_at"].isoformat() if s.get("loaded_at") else None,
            "shapes": s.get("source"), "shapes_error": s.get("error"), "max_triples": s.get("max_triples"),
            "shacl_report_url": SHACL_REPORT_URL}


def _facet_keys(state: QueryState, d: dict) -> list[str]:
    if state.facets is not None:
        out = []
        for k in state.facets:
            scope, fk = split_key(k)
            if fk in d["kinds"] and d["kinds"][fk] not in HIDDEN_KINDS and (scope is None or scope in d["owners"][fk]):
                out.append(k)
        return out
    wanted = set(state.classes) | {"*"}
    keys = []
    for f in d["facets"]:
        if f["class"] in wanted and f["kind"] not in HIDDEN_KINDS and f["key"] not in keys:
            keys.append(f["key"])
    return keys


def _data_version(env: str) -> str:
    r = _q("""SELECT (SELECT max(updated_at) FROM index_state WHERE env=%s)::text AS a,
                     (SELECT max(finished_at) FROM ingest_run WHERE env=%s)::text AS b""", [env, env])[0]
    return f"{r['a']}|{r['b']}"


_facet_cache: dict[str, tuple[str, dict]] = {}
_executor = ThreadPoolExecutor(max_workers=int(os.environ.get("ATLAS_FACET_THREADS", "4")))


@app.post("/api/facets")
def facets(state: QueryState):
    _env(state.env)
    version = _data_version(state.env)
    ckey = state.model_dump_json()
    hit = _facet_cache.get(ckey)
    if hit and hit[0] == version:
        return hit[1]
    result = _compute_facets(state)
    if len(_facet_cache) > 500:
        _facet_cache.clear()
    _facet_cache[ckey] = (version, result)
    return result


def _compute_facets(state: QueryState) -> dict:
    d = _defs()
    w = Where(state, d["owners"])
    keys = _facet_keys(state, d)
    active = [k for k in keys if k in state.filters]
    plain = [k for k in keys if k not in active]
    unfiltered = not state.filters and not (state.q and state.q.strip()) and not state.bbox and not state.graphs
    out: dict[str, dict] = {}

    def run(keys_: list[str], where_sql: str, params: list) -> dict:
        res: dict[str, dict] = {}
        terms = [k for k in keys_ if d["kinds"][k] in ("terms", "boolean")]
        ranges = [k for k in keys_ if d["kinds"][k] == "range"]
        if terms:
            rows = _q(f"""SELECT k.key, v.value AS value, count(*) AS n
                          FROM feature f CROSS JOIN unnest(%s::text[]) k(key)
                          CROSS JOIN LATERAL jsonb_array_elements(f.facets -> k.key) v
                          WHERE {where_sql} GROUP BY 1, 2""", [terms] + params)
            vals: dict[str, list] = defaultdict(list)
            for r in rows:
                vals[r["key"]].append((r["value"], r["n"]))
            for k in terms:
                vs = sorted(vals.get(k, []), key=lambda x: (-x[1], str(x[0])))
                res[k] = {"kind": d["kinds"][k], "values": vs, "more": len(vs) > TERMS_LIMIT}
        if ranges:
            rows = _q(f"""SELECT k.key, min(v.value::numeric) AS lo, max(v.value::numeric) AS hi, count(DISTINCT f.id) AS n
                          FROM feature f CROSS JOIN unnest(%s::text[]) k(key)
                          CROSS JOIN LATERAL jsonb_array_elements(f.facets -> k.key) v
                          WHERE {where_sql} AND jsonb_typeof(v.value) = 'number' GROUP BY 1""", [ranges] + params)
            got = {r["key"]: r for r in rows}
            for k in ranges:
                r = got.get(k)
                res[k] = {"kind": "range", "min": float(r["lo"]) if r else None,
                          "max": float(r["hi"]) if r else None, "count": r["n"] if r else 0}
        return res

    def run_cached(keys_: list[str], scope: str | None = None) -> dict:
        """Unfiltered state: counts precomputed by the worker (facet_cache)."""
        res: dict[str, dict] = {}
        cls = ([scope] if not state.classes or scope in state.classes else ["-"]) if scope else (list(state.classes) or None)
        rows = _q("""SELECT key, value, sum(n)::bigint AS n FROM facet_cache
                     WHERE env=%s AND key = ANY(%s) AND (%s::text[] IS NULL OR class = ANY(%s::text[]))
                     GROUP BY 1, 2""", [state.env, keys_, cls, cls])
        vals: dict[str, list] = defaultdict(list)
        for r in rows:
            vals[r["key"]].append((r["value"], r["n"]))
        for k in keys_:
            vs = vals.get(k, [])
            if d["kinds"][k] == "range":
                nums = [float(v) for v, _ in vs if isinstance(v, (int, float)) and not isinstance(v, bool)]
                res[k] = {"kind": "range", "min": min(nums) if nums else None, "max": max(nums) if nums else None,
                          "count": sum(n for v, n in vs)}
            else:
                vs = sorted(vs, key=lambda x: (-x[1], str(x[0])))
                res[k] = {"kind": d["kinds"][k], "values": vs, "more": len(vs) > TERMS_LIMIT}
        return res

    sql_nc, p_nc = w.build(with_classes=False)
    jobs = [_executor.submit(lambda: _q(
        f"""SELECT f.class, count(*) AS n, count(*) FILTER (WHERE f.geom IS NULL) AS u
            FROM feature f WHERE {sql_nc} GROUP BY 1""", p_nc))]
    def scoped(full: list[str], scope: str | None, fn):
        """Run fn on the plain facet keys of `full` and key the result by the full keys."""
        plain_keys = {split_key(k)[1]: k for k in full}
        return lambda: {plain_keys[k]: v for k, v in fn(list(plain_keys)).items()}

    def class_cond(sql_: str, p_: list, scope: str | None) -> tuple[str, list]:
        return (f"{sql_} AND f.class = %s", p_ + [scope]) if scope else (sql_, p_)

    groups: dict[str | None, list[str]] = defaultdict(list)
    for k in plain:
        groups[split_key(k)[0]].append(k)
    for scope, full in groups.items():
        if unfiltered:
            jobs.append(_executor.submit(scoped(full, scope, lambda ks, sc=scope: run_cached(ks, sc))))
        else:
            sql_all, p_all = class_cond(*w.build(), scope)
            jobs.append(_executor.submit(scoped(full, scope, lambda ks, s_=sql_all, p_=p_all: run(ks, s_, p_))))
    for k in active:  # a facet's own selection doesn't narrow its own counts
        scope = split_key(k)[0]
        s_k, p_k = class_cond(*w.build(exclude_facet=k), scope)
        jobs.append(_executor.submit(scoped([k], scope, lambda ks, s_=s_k, p_=p_k: run(ks, s_, p_))))
    class_rows = jobs[0].result()
    by_class = {r["class"]: r["n"] for r in class_rows}
    unlocated = {r["class"]: r["u"] for r in class_rows if r["u"]}
    for j in jobs[1:]:
        out.update(j.result())
    total = sum(n for c, n in by_class.items() if not state.classes or c in state.classes)

    iris = {v for f in out.values() if f["kind"] == "terms" for v, _ in f["values"][:TERMS_LIMIT]}
    labels = _labels(state.env, iris)
    result = {}
    for k in keys:
        f = out.get(k)
        if not f:
            continue
        if f["kind"] == "range":
            result[k] = {"kind": "range", "min": f["min"], "max": f["max"], "count": f["count"]}
        else:
            vals = [{"value": v, "label": labels.get(v, _local(v)) if isinstance(v, str) else ("Yes" if v is True else "No" if v is False else str(v)),
                     "count": n} for v, n in f["values"][:TERMS_LIMIT]]
            result[k] = {"kind": f["kind"], "values": vals, "more": f["more"]}
    return {"total": total, "by_class": by_class, "unlocated_by_class": unlocated, "facets": result}


@app.post("/api/search")
def search(req: SearchRequest):
    _env(req.env)
    w = Where(req, _defs()["owners"])
    sql, p = w.build()
    vcol, vjoin, vp = "", "", []
    if req.color:
        t = themes.get(req.env, req.color)
        vcol = ", t.n AS v" if t["kind"] == "range" else ", t.v AS v"
        vjoin = "LEFT JOIN theme_value t ON t.theme = %s AND t.fid = f.id"
        vp = [t["id"]]
    counts = _q(f"SELECT count(*) AS n, count(*) FILTER (WHERE f.geom IS NULL) AS u FROM feature f WHERE {sql}", p)[0]
    order = "cd.position NULLS LAST, f.label NULLS LAST, f.id"
    op: list = []
    if req.q and req.q.strip() and not req.q.startswith("http"):
        # exact label, then label prefix, then important classes first (class order in shapes)
        order = ("(lower(f.label) = lower(%s)) DESC, (f.label ILIKE %s) DESC, cd.position NULLS LAST, "
                 "length(f.label), f.label, f.id")
        op = [req.q.strip(), req.q.strip() + "%"]
    rows = _q(f"""SELECT f.id, f.iri, f.class, f.label, {QA_EXPR} AS qa,
                         ST_X(ST_PointOnSurface(f.geom)) AS lon, ST_Y(ST_PointOnSurface(f.geom)) AS lat{vcol}
                  FROM feature f LEFT JOIN class_def cd ON cd.class = f.class {vjoin}
                  WHERE {sql} ORDER BY {order} LIMIT %s OFFSET %s""",
              vp + p + op + [req.limit, req.offset])
    return {"total": counts["n"], "unlocated": counts["u"], "items": rows}


themes = Themes(_q, lambda: pool.connection(), lambda: _defs(), lambda env: _data_version(env))
_theme_cache: dict[str, tuple[str, dict]] = {}


def _value_label(v, labels: dict[str, str]) -> str:
    if v == "true":
        return "Yes"
    if v == "false":
        return "No"
    return labels.get(v, _local(v)) if isinstance(v, str) and (v.startswith("http") or v.startswith("urn:")) else str(v)


@app.post("/api/theme")
def theme(req: ThemeRequest):
    """Legend for colouring the map by facet `key`: the palette assignment (stable for the
    environment) and how many features of the query state fall in each value or class."""
    _env(req.env)
    t = themes.get(req.env, req.key)
    ckey = req.model_dump_json()
    hit = _theme_cache.get(ckey)
    if hit and hit[0] == t["version"]:
        return hit[1]
    d = _defs()
    fk = split_key(req.key)[1]
    fdef = next((f for f in d["facets"] if f["key"] == fk and (f["class"] in t["classes"] or t["classes"] == ["*"])), None)
    coloured = themes.coloured_classes(t)
    sql, p = Where(req, d["owners"]).build()
    cond, cp = ("", []) if coloured is None else (" AND f.class = ANY(%s)", [coloured])
    out = {"key": req.key, "kind": t["kind"], "label": fdef["label"] if fdef else fk,
           "unit": fdef["unit"] if fdef else None,
           "classes": [] if coloured is None else list(t["classes"]), "rollup": list(t["rollup"])}
    if t["kind"] == "range":
        spec = t["breaks"] or {}
        breaks, discrete = list(spec.get("breaks") or []), bool(spec.get("discrete"))
        bucket = "width_bucket(t.n, %s::float8[])" if breaks else "0"
        bp = [breaks] if breaks else []
        rows = _q(f"""SELECT CASE WHEN t.fid IS NULL THEN -1 ELSE {bucket} END AS b, count(*) AS n
                      FROM feature f LEFT JOIN theme_value t ON t.theme = %s AND t.fid = f.id
                      WHERE {sql}{cond} GROUP BY 1""", bp + [t["id"]] + p + cp)
        counts = {r["b"]: r["n"] for r in rows}
        lo, hi = t["lo"], t["hi"]
        # Class i holds values >= breaks[i-1] and < breaks[i]; discrete: one class per value.
        if lo is None:
            bins = []
        elif discrete:
            bins = [{"min": v, "max": v, "count": counts.get(i, 0)} for i, v in enumerate([lo] + breaks)]
        else:
            edges = [lo] + breaks + [hi]
            bins = [{"min": edges[i], "max": edges[i + 1], "count": counts.get(i, 0)} for i in range(len(edges) - 1)]
        out.update(breaks=breaks, bins=bins, min=lo, max=hi, discrete=discrete,
                   nodata=counts.get(-1, 0), total=sum(counts.values()))
    else:
        rows = _q(f"""SELECT t.v, count(*) AS n
                      FROM feature f LEFT JOIN theme_value t ON t.theme = %s AND t.fid = f.id
                      WHERE {sql}{cond} GROUP BY 1""", [t["id"]] + p + cp)
        counts = {r["v"]: r["n"] for r in rows}
        slots = list(t["slots"] or [])
        others = sorted(((v, n) for v, n in counts.items() if v is not None and v != MIXED and v not in slots),
                        key=lambda x: (-x[1], x[0]))
        shown = [(v, counts.get(v, 0)) for v in slots] + others[:TERMS_LIMIT]
        labels = _labels(req.env, {v for v, _ in shown})
        out.update(values=[{"value": v, "label": _value_label(v, labels), "count": n,
                            "slot": slots.index(v) if v in slots else None} for v, n in shown],
                   more=len(others) > TERMS_LIMIT, other=sum(n for _, n in others),
                   mixed=counts.get(MIXED, 0), nodata=counts.get(None, 0), total=sum(counts.values()))
    if len(_theme_cache) > 500:
        _theme_cache.clear()
    _theme_cache[ckey] = (t["version"], out)
    return out


@app.post("/api/extent")
def extent(state: QueryState):
    _env(state.env)
    sql, p = Where(state, _defs()["owners"]).build(with_bbox=False)
    r = _q(f"SELECT ST_XMin(e) w, ST_YMin(e) s, ST_XMax(e) e, ST_YMax(e) n FROM (SELECT ST_Extent(f.geom) e FROM feature f WHERE {sql}) x", p)[0]
    if r["w"] is None:
        return {"bbox": None}
    return {"bbox": [r["w"], r["s"], r["e"], r["n"]]}


def _decode_state(s: str) -> QueryState:
    try:
        pad = "=" * (-len(s) % 4)
        return QueryState(**json.loads(base64.urlsafe_b64decode(s + pad)))
    except Exception as e:
        raise HTTPException(400, f"bad state: {e}")


@app.get("/api/tiles/{z}/{x}/{y}.pbf")
def tiles(z: int, x: int, y: int, state: str, color: str | None = None):
    st = _decode_state(state)
    _env(st.env)
    theme = themes.get(st.env, color) if color else None
    st.bbox = None
    d = _defs()
    allowed = [c for c, mz in d["min_zoom"].items() if (mz <= z or c in st.all_zoom) and (not st.classes or c in st.classes)]
    if not allowed:
        return Response(b"", media_type="application/vnd.mapbox-vector-tile")
    st.classes = allowed
    sql, p = Where(st, d["owners"]).build()
    # Below zoom 10 lines are simplified to roughly a pixel to keep tiles small.
    tol = 40075016.0 / (256 * 2 ** z) if z < 10 else 0
    geom = f"ST_Simplify(f.geom3857, {tol})" if tol else "f.geom3857"
    # Below LOW_DETAIL_ZOOM, tiles carry only id + class (resolve with /api/feature?id=) and
    # features that collapse onto the same tile pixels are sent once.
    low = z < LOW_DETAIL_ZOOM
    props = f"f.id, f.class, {QA_EXPR} AS qa" if low else f"f.id, f.iri, f.class, f.label, {QA_EXPR} AS qa"
    # Colouring by a facet adds its display value `v` (see /api/theme) at every zoom.
    vjoin, vp = "", []
    if theme:
        props += ", t.n AS v" if theme["kind"] == "range" else ", t.v AS v"
        vjoin = "LEFT JOIN theme_value t ON t.theme = %s AND t.fid = f.id"
        vp = [theme["id"]]
    # Classes overlap at low zoom (a running track usually has its section of line's geometry),
    # so of features on the same pixels only the one drawn on top (first in the class list) is sent.
    dedup, order, op = "", "", []
    if low:
        dedup = "DISTINCT ON (v, geom)" if theme else "DISTINCT ON (geom)"
        order = ("ORDER BY v, geom" if theme else "ORDER BY geom") + ", array_position(%s::text[], class)"
        op = [[c["class"] for c in d["classes"]]]
    # A coarser tile grid at low zoom: fewer vertices and more pixel-duplicates removed.
    extent = 512 if low else 4096
    mvt_geom = f"ST_AsMVTGeom({geom}, bounds.b, {extent}, {extent // 64}, true)"
    if tol:
        # A line shorter than a pixel snaps to nothing and is dropped, which leaves gaps
        # in a network of short sections at low zoom (and their colours with them). Keep
        # it as a one-unit line at its start, drawn as a dot by the round line caps.
        start = f"ST_AsMVTGeom(ST_StartPoint(ST_GeometryN(f.geom3857, 1)), bounds.b, {extent}, {extent // 64}, true)"
        mvt_geom = (f"coalesce({mvt_geom}, CASE WHEN GeometryType(f.geom3857) IN ('LINESTRING', 'MULTILINESTRING') "
                    f"THEN ST_MakeLine({start}, ST_Translate({start}, 1, 0)) END)")
    rows = _q(f"""
        WITH bounds AS (SELECT ST_TileEnvelope(%s, %s, %s) AS b),
        mvt AS (
            SELECT {dedup} * FROM (
                SELECT f.id AS fid, {props}, {mvt_geom} AS geom
                FROM feature f CROSS JOIN bounds {vjoin}
                WHERE f.geom3857 && ST_Expand(bounds.b, (ST_XMax(bounds.b) - ST_XMin(bounds.b)) / 64) AND {sql}
            ) q WHERE geom IS NOT NULL {order})
        SELECT ST_AsMVT(mvt.*, 'features', {extent}, 'geom', 'fid') AS tile FROM mvt
    """, [z, x, y] + vp + p + op)
    data = rows[0]["tile"] if rows and rows[0]["tile"] else b""
    return Response(bytes(data), media_type="application/vnd.mapbox-vector-tile",
                    headers={"Cache-Control": f"public, max-age={TILE_CACHE_SECONDS}"})


@app.get("/api/export")
def export(state: str, format: str = Query("csv", pattern="^(csv|xlsx|ttl)$"),
           values: str = Query("label", pattern="^(label|iri)$")):
    """Every feature matching the query state (classes, facets, datasets, search, bbox)
    as CSV (one table, streamed), XLSX (one sheet per class) or Turtle (the outgoing
    triples of each entity, live from ERA, streamed)."""
    st = _decode_state(state)
    e = _env(st.env)
    d = _defs()
    sql, p = Where(st, d["owners"]).build()
    if format == "ttl":
        return _export_ttl(st, e, sql, p)
    present = {r["class"] for r in _q(f"SELECT DISTINCT f.class FROM feature f WHERE {sql}", p)}
    order = [c["class"] for c in d["classes"]]
    classes = sorted(present, key=lambda c: (order.index(c) if c in order else len(order), c))
    class_labels = {c["class"]: c["label"] for c in d["classes"]}
    vals = xp.Values(lambda iris: _labels(st.env, iris), _local, values == "label")
    batches = xp.feature_rows(pool.connection, sql, p)
    stamp = datetime.now(UTC)
    name = f"rinf-atlas-{st.env}-{stamp:%Y%m%d-%H%M}"
    if format == "csv":
        cols = xp.columns_for(classes, d["facets"])
        return StreamingResponse(xp.stream_csv(batches, cols, vals, class_labels), media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})
    filters = {k: v.model_dump() if hasattr(v, "model_dump") else v for k, v in st.filters.items()}
    info = [("RINF Atlas export", ""), ("Environment", st.env), ("Exported (UTC)", stamp.strftime("%Y-%m-%d %H:%M")),
            ("Classes", ", ".join(class_labels.get(c, c) for c in classes) or "(none)"),
            ("Filters", json.dumps(filters, ensure_ascii=False) if filters else "(none)")]
    if st.q:
        info.append(("Search", st.q))
    if st.graphs:
        info.append(("Datasets", "; ".join(st.graphs)))
    if st.bbox:
        info.append(("Map view [w,s,e,n]", ", ".join(f"{x:.5f}" for x in st.bbox)))
    path = xp.write_xlsx(batches, lambda c: xp.columns_for([c], d["facets"]), vals, class_labels, info)
    return FileResponse(path, filename=f"{name}.xlsx", background=BackgroundTask(os.unlink, path),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def _export_ttl(st: QueryState, e: dict, sql: str, p: list) -> StreamingResponse:
    iris = [r["iri"] for r in _q(f"SELECT DISTINCT f.iri FROM feature f WHERE {sql} ORDER BY f.iri LIMIT %s",
                                 p + [TTL_EXPORT_MAX + 1])]
    if len(iris) > TTL_EXPORT_MAX:
        raise HTTPException(413, f"Turtle export is limited to {TTL_EXPORT_MAX} entities (it queries ERA live); "
                                 "narrow the selection")
    stamp = datetime.now(UTC)

    def fetch(query: str) -> list[dict]:
        r = httpx.post(e["sparql"], data={"query": query}, timeout=120,
                       headers={"Accept": "application/sparql-results+json", "User-Agent": "rinf-atlas/0.1"})
        r.raise_for_status()
        return r.json()["results"]["bindings"]

    head = ttl.header([f"RINF Atlas export: outgoing triples of {len(iris)} entities",
                       f"Source: {e['sparql']} ({st.env}), {stamp:%Y-%m-%d %H:%M} UTC"])
    name = f"rinf-atlas-{st.env}-{stamp:%Y%m%d-%H%M}.ttl"
    return StreamingResponse(ttl.stream(iris, fetch, head), media_type="text/turtle; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/feature")
def feature(env: str, iri: str | None = None, id: int | None = None):
    _env(env)
    if not iri and id is None:
        raise HTTPException(400, "iri or id required")
    if not iri:
        hit = _q("SELECT iri FROM feature WHERE env=%s AND id=%s", [env, id])
        if not hit:
            raise HTTPException(404, "feature not indexed")
        iri = hit[0]["iri"]
    rows = _q("""SELECT f.iri, f.label, f.class, f.class_iri, f.graph_iri, f.facets, f.geom_source,
                        ST_AsGeoJSON(f.geom, 6)::json AS geometry, d.provider, d.title AS dataset
                 FROM feature f LEFT JOIN dataset d ON d.env=f.env AND d.graph_iri=f.graph_iri
                 WHERE f.env=%s AND f.iri=%s""", [env, iri])
    if not rows:
        raise HTTPException(404, "feature not indexed")
    r = rows[0]
    d = _defs()
    facet_labels = {f["key"]: f["label"] for f in d["facets"]}
    iris = {v for vs in r["facets"].values() for v in vs if isinstance(v, str)}
    labels = _labels(env, iris)
    facets_out = {}
    for k, vs in r["facets"].items():
        if k == "quality":
            continue  # shown as `quality` below
        facets_out[k] = [{"value": v, "label": labels.get(v, _local(v)) if isinstance(v, str) else
                          ("Yes" if v is True else "No" if v is False else str(v))} for v in vs]
    return {"iri": r["iri"], "label": r["label"], "classes": [x["class"] for x in rows],
            "class_iri": r["class_iri"], "graph_iri": r["graph_iri"], "provider": r["provider"],
            "dataset": r["dataset"], "geometry_source": r["geom_source"],
            "position": _feature_position(env, r["iri"], r["class"], r["geom_source"], d),
            "facets": facets_out, "facet_labels": {k: facet_labels.get(k, k) for k in r["facets"] if k != "quality"},
            "facet_kinds": {k: d["kinds"].get(k, "terms") for k in r["facets"] if k != "quality"},
            "geometry": r["geometry"], "quality": _feature_quality(env, r["iri"], r["graph_iri"])}


FEATURE_RESULTS = 200
POSITION_LINKS = 20


def _feature_position(env: str, iri: str, cls: str, source: str | None, d: dict) -> dict:
    """How the feature got its position: its class's rules from the shapes, and for a borrowed
    position (`from`) or a straight line (`line`) the indexed features it came from."""
    c = next((x for x in d["classes"] if x["class"] == cls), None) or {}
    links = []
    if source in ("from", "line"):
        rules = ["from"] if source == "from" else ["lineA", "lineB"]
        links = _q("""SELECT DISTINCT ON (l.rule, l.target) l.rule, l.target AS iri, t.label, t.class
                      FROM feature_link l JOIN feature t ON t.env=l.env AND t.iri=l.target AND t.geom IS NOT NULL
                      WHERE l.env=%s AND l.iri=%s AND l.class=%s AND l.rule = ANY(%s)
                      ORDER BY l.rule, l.target, t.class LIMIT %s""", [env, iri, cls, rules, POSITION_LINKS + 1])
    return {"source": source, "rules": c.get("position_rules"), "shape_file": c.get("shape_file"),
            "links": links[:POSITION_LINKS], "more_links": len(links) > POSITION_LINKS}


def _feature_quality(env: str, iri: str, graph: str) -> dict:
    ds = _q("""SELECT d.qa_status, d.qa_checked_at, d.qa_note, d.qa_reason, d.qa_source,
                      r.turtle_gz IS NOT NULL AS report, r.html_gz IS NOT NULL AS html
               FROM dataset d LEFT JOIN quality_report r ON r.env=d.env AND r.graph_iri=d.graph_iri
               WHERE d.env=%s AND d.graph_iri=%s""", [env, graph])
    st = ds[0] if ds else {"qa_status": None, "qa_checked_at": None, "qa_note": None, "qa_reason": None,
                           "qa_source": None, "report": None, "html": None}
    rows = _q("""SELECT severity, shape, component, path, value, message FROM violation
                 WHERE env=%s AND focus=%s AND graph_iri=%s
                 ORDER BY severity DESC, shape, component, value LIMIT %s""", [env, iri, graph, FEATURE_RESULTS + 1])
    values = {_nt_iri(r["value"]) for r in rows} - {None}
    labels = _labels(env, values)
    results = [{"severity": SEVERITY_NAME.get(r["severity"], "info"), "message": r["message"], "path": r["path"],
                "value": r["value"], "value_label": labels.get(_nt_iri(r["value"]) or ""),
                "shape": r["shape"], "component": r["component"]} for r in rows[:FEATURE_RESULTS]]
    return {"status": st["qa_status"], "checked_at": st["qa_checked_at"].isoformat() if st["qa_checked_at"] else None,
            "note": st["qa_note"], "reason": st["qa_reason"], "shapes": st["qa_source"],
            "results": results, "truncated": len(rows) > FEATURE_RESULTS,
            "report": bool(st["report"]), "html": bool(st["html"])}


def _nt_iri(v: str | None) -> str | None:
    return v[1:-1] if v and v.startswith("<") and v.endswith(">") else None


# --- vehicle compatibility (rules: shapes/compatibility.ttl) -------------------

def _compat_def() -> dict:
    cdef = _defs().get("compat")
    if not cdef or not cdef.get("rules"):
        raise HTTPException(404, "no vehicle compatibility rules are configured (shapes/compatibility.ttl)")
    return cdef


def _compile(cdef: dict, profile: dict) -> tuple[list[cx.Check], list[str]]:
    try:
        checks, ignored = cx.compile_profile(cdef["rules"], profile)
    except cx.ProfileError as e:
        raise HTTPException(400, f"bad profile: {e}")
    if not checks:
        raise HTTPException(400, "the profile has no characteristic filled in that a rule checks")
    return checks, ignored


@app.get("/api/compatibility/rules")
def compatibility_rules(env: str):
    """The characteristics a vehicle profile can fill, with the values the indexed
    infrastructure has for the facets each one is compared with (for pickers)."""
    _env(env)
    cdef = _compat_def()
    out = []
    iris: set[str] = set()
    for r in cdef["rules"]:
        classes = cdef["track_classes"] if r["applies_to"] == "track" else [cdef["section_class"]]
        rows = _q("""SELECT value, sum(n)::bigint AS n FROM facet_cache
                     WHERE env=%s AND key = ANY(%s) AND class = ANY(%s) GROUP BY 1""", [env, r["facets"], classes])
        item = {k: r.get(k) for k in ("key", "label", "comment", "path", "constraint", "kind", "datatype", "unit",
                                      "facets", "applies_to", "when_missing", "none_value", "none_from",
                                      "missing_reason", "without_value", "without_label", "without_matches",
                                      "term_values", "path_factor", "implied")}
        # for explaining the rule: what it compares (facet, RINF path, classes) and its Turtle
        item["section_facets"] = r.get("section_facets") or []
        item["infra"] = r.get("infra") or [{"key": fk, "label": fk, "path": None, "classes": classes} for fk in r["facets"]]
        item["source"] = r.get("source") or None
        if r["kind"] == "number" and r.get("term_values"):
            # the numbers the track's concepts stand for (e.g. axle load limits of line categories)
            tv = r["term_values"]
            nums = [tv[x["value"]] for x in rows if x["value"] in tv]
            item["range"] = {"min": min(nums) if nums else None, "max": max(nums) if nums else None,
                             "count": sum(x["n"] for x in rows if x["value"] in tv)}
            iris |= set(tv)
        elif r["kind"] == "number":
            nums = [float(x["value"]) for x in rows if isinstance(x["value"], (int, float)) and not isinstance(x["value"], bool)]
            item["range"] = {"min": min(nums) if nums else None, "max": max(nums) if nums else None,
                             "count": sum(x["n"] for x in rows)}
        else:
            vals = sorted(rows, key=lambda x: (-x["n"], str(x["value"])))[:TERMS_LIMIT]
            if r.get("none_value"):
                # "none" (e.g. not electrified) first; its count is the tracks without a value
                n = _q("""SELECT count(*) AS n FROM feature WHERE env=%s AND class = ANY(%s)
                          AND NOT facets ?| %s""", [env, classes, r["facets"]])[0]["n"]
                vals = [{"value": r["none_value"], "n": n, "none": True}] + vals
            if r.get("without_value"):
                # "does not have the system" (e.g. no ETCS) first, its count the tracks it runs on
                wv, wm = r["without_value"], set(r.get("without_matches") or [])
                n = sum(x["n"] for x in rows if x["value"] in wm)
                vals = [{"value": wv, "n": n, "without": True}] + [x for x in vals if x["value"] != wv]
            item["values"] = vals
            iris |= {x["value"] for x in vals if isinstance(x["value"], str)}
        out.append(item)
    labels = _labels(env, iris | {r["none_value"] for r in cdef["rules"] if r.get("none_value")}
                     | {x for r in cdef["rules"] for x in r.get("without_matches") or []})
    class_labels = {c["class"]: c["label"] for c in _defs()["classes"]}
    by_key = {r["key"]: r for r in cdef["rules"]}
    for r, item in zip(cdef["rules"], out):
        for v in item.get("values", []):
            x = v["value"]
            v["label"] = (r["without_label"] if v.get("without") and r.get("without_label")
                          else labels.get(x, _local(x)) if isinstance(x, str) else ("Yes" if x is True else "No"))
            v["count"] = v.pop("n")
        item["explanation"] = cx.explain_rule(r, by_key, cdef["track_classes"], cdef["section_class"],
                                              labels, class_labels)
    return {"track_classes": cdef["track_classes"], "section_class": cdef["section_class"],
            "class_labels": {k: class_labels.get(k, k) for k in cdef["track_classes"] + [cdef["section_class"]]},
            "rules": out}


_compat_cache: dict[str, tuple[str, dict]] = {}


@app.post("/api/compatibility")
def compatibility(req: cx.CompatRequest):
    """Status of every section of line for a vehicle profile: ids per colour."""
    _env(req.env)
    cdef = _compat_def()
    checks, ignored = _compile(cdef, req.profile)
    version = _data_version(req.env)
    ckey = json.dumps([req.env, req.profile, cdef], sort_keys=True)
    hit = _compat_cache.get(ckey)
    if hit and hit[0] == version:
        return hit[1]
    sql, params = cx.status_sql(cdef, checks, req.env)
    ids: dict[str, list[int]] = {"green": [], "yellow": [], "red": []}
    for r in _q(f"SELECT id, status FROM ({sql}) x ORDER BY id", params):
        ids[cx.STATUS[r["status"]]].append(r["id"])
    # version: the indexed data the ids refer to; clients put it in the tile URL of the
    # layer they colour, so tiles and ids always come from the same index.
    out = {"env": req.env, "version": hashlib.sha1(version.encode()).hexdigest()[:12],
           "checked": [c.key for c in checks], "ignored": ignored,
           "counts": {k: len(v) for k, v in ids.items()}, **ids}
    if len(_compat_cache) > 200:
        _compat_cache.clear()
    _compat_cache[ckey] = (version, out)
    return out


@app.post("/api/compatibility/explain")
def compatibility_explain(req: cx.ExplainRequest):
    """Why one section of line has its status: each characteristic per track (and section)."""
    _env(req.env)
    cdef = _compat_def()
    checks, ignored = _compile(cdef, req.profile)
    iri = req.iri
    if not iri and req.id is not None:
        hit = _q("SELECT iri FROM feature WHERE env=%s AND id=%s", [req.env, req.id])
        iri = hit[0]["iri"] if hit else None
    if not iri:
        raise HTTPException(400, "iri or id required")
    sql, params = cx.status_sql(cdef, checks, req.env, iri)
    st = _q(sql, params)
    if not st:
        raise HTTPException(404, f"not an indexed {cdef['section_class']}")
    track = [c for c in checks if c.applies_to == "track"]
    section = [c for c in checks if c.applies_to == "section"]
    rules = {r["key"]: r for r in cdef["rules"]}

    def facet_values(row: dict, rule: dict, keys: list[str]) -> list:
        # a track without a value takes its section's (rule.section_facets), as in the SQL
        sec_f = row.get("section_facets") or {}
        fb = set(rule.get("section_facets") or [])
        vals = [v for fk in keys for v in (row["facets"].get(fk) or (sec_f.get(fk, []) if fk in fb else []))]
        if keys != rule["facets"]:
            return vals
        # e.g. a track with ETCS not applicable counts as level N
        return vals + [v for v in cx.implied_values(rule, row["facets"]) if v not in vals]

    def results(row: dict, cs: list[cx.Check]) -> list[dict]:
        out = []
        for i, c in enumerate(cs):
            rule = rules[c.key]
            vals = facet_values(row, rule, rule["facets"])
            raw = row[f"c{i}"]
            item = {"key": c.key, "result": cx.RESULT[cx.effective(c, raw)], "values": vals}
            # why a track without a value got its result (e.g. "No contact line")
            nr = cx.none_rule(rule, rules)
            if not vals and nr and rule.get("missing_reason") and not facet_values(row, nr, nr["facets"]):
                item["note"] = rule["missing_reason"]
            elif c.bypass and raw != cx.OK:
                # compatible only because the vehicle can do without the system here
                item["bypass"] = nr["none_value"]
            out.append(item)
        return out

    tracks = []
    if track:
        tsql, tp = cx.track_detail_sql(cdef, track, req.env, iri)
        for r in _q(tsql, tp):
            res = results(r, track)
            worst = max(cx.effective(c, r[f"c{i}"]) for i, c in enumerate(track))
            tracks.append({"id": r["id"], "iri": r["iri"], "label": r["label"], "class": r["class"],
                           "status": cx.STATUS[worst], "results": res})
    sec = []
    if section:
        ssql, sp = cx.section_detail_sql(cdef, section, req.env, iri)
        rows = _q(ssql, sp)
        sec = results(rows[0], section) if rows else []
    vals = {v for t in tracks for x in t["results"] for v in x["values"] if isinstance(v, str)}
    vals |= {v for x in sec for v in x["values"] if isinstance(v, str)}
    vals |= {x["bypass"] for t in tracks for x in t["results"] if x.get("bypass")}
    labels = _labels(req.env, vals)

    def lab(res: list[dict]) -> None:
        for x in res:
            if x.get("bypass"):
                x["note"] = f"Compatible in “{labels.get(x['bypass'], _local(x['bypass']))}” mode"
            x["values"] = [{"value": v, "label": labels.get(v, _local(v)) if isinstance(v, str)
                            else ("Yes" if v is True else "No" if v is False else str(v))} for v in x["values"]]
    for t in tracks:
        lab(t["results"])
    lab(sec)
    return {"iri": iri, "id": st[0]["id"], "status": cx.STATUS[st[0]["status"]], "checked": [c.key for c in checks],
            "ignored": ignored, "section": sec, "tracks": tracks}


@app.get("/api/datasets")
def datasets(env: str):
    _env(env)
    rows = _q("""SELECT d.graph_iri, d.title, d.provider, d.country, d.modified, d.status, d.triples,
                        d.ingested_at, d.error, d.dataset_id, d.file_name,
                        (SELECT count(*) FROM feature f WHERE f.env=d.env AND f.graph_iri=d.graph_iri) AS features,
                        d.qa_status, d.qa_violations, d.qa_warnings, d.qa_infos, d.qa_focus, d.qa_kept,
                        d.qa_checked_at, d.qa_seconds, d.qa_note, d.qa_reason, d.qa_limit, d.qa_source,
                        (SELECT r.turtle_gz IS NOT NULL FROM quality_report r
                         WHERE r.env=d.env AND r.graph_iri=d.graph_iri) AS qa_report,
                        (SELECT r.html_gz IS NOT NULL FROM quality_report r
                         WHERE r.env=d.env AND r.graph_iri=d.graph_iri) AS qa_html
                 FROM dataset d WHERE d.env=%s AND d.kind='provider'
                 ORDER BY d.modified DESC NULLS LAST, d.graph_iri""", [env])
    out = []
    for r in rows:
        for k in ("modified", "ingested_at"):
            if r[k]:
                r[k] = r[k].isoformat()
        q = {k[3:]: r.pop(k) for k in list(r) if k.startswith("qa_")}
        r["quality"] = {
            "status": q["status"], "violations": q["violations"], "warnings": q["warnings"], "infos": q["infos"],
            "focus_nodes": q["focus"], "kept": q["kept"],
            "checked_at": q["checked_at"].isoformat() if q["checked_at"] else None,
            "seconds": q["seconds"], "note": q["note"], "reason": q["reason"], "limit": q["limit"],
            "shapes": q["source"], "report": bool(q["report"]), "html": bool(q["html"]),
        }
        out.append(r)
    return out


# --- data quality (validation against ERA's SHACL shapes, done by the worker) --

def _code(graph: str) -> str:
    return "".join(c for c in graph.rstrip("/").rsplit("/", 1)[-1] if c.isalnum() or c in "-_") or "dataset"


@app.get("/api/quality/summary")
def quality_summary(env: str, graph: str, limit: int = Query(50, le=500)):
    """Results per shape and constraint for one dataset, most frequent first."""
    _env(env)
    rows = _q("""SELECT shape, component, severity, n, message FROM quality_count
                 WHERE env=%s AND graph_iri=%s ORDER BY severity DESC, n DESC, shape LIMIT %s""", [env, graph, limit])
    return [{"shape": r["shape"], "component": r["component"], "severity": SEVERITY_NAME.get(r["severity"], "info"),
             "count": r["n"], "message": r["message"]} for r in rows]


@app.get("/api/quality/report")
def quality_report(env: str, graph: str, format: str = Query("ttl", pattern="^(ttl|html)$"), download: bool = False):
    """The dataset's sh:ValidationReport (Turtle), or shacl-report's HTML viewer of it."""
    _env(env)
    col = "turtle_gz" if format == "ttl" else "html_gz"
    rows = _q(f"SELECT {col} AS data, created_at FROM quality_report WHERE env=%s AND graph_iri=%s", [env, graph])
    if not rows or rows[0]["data"] is None:
        raise HTTPException(404, "no validation report for this dataset" if format == "ttl"
                            else "no HTML report for this dataset (too many results, or shacl-report not installed)")
    body = gzip.decompress(bytes(rows[0]["data"]))
    name = f"rinf-atlas-{env}-{_code(graph)}-shacl-report"
    if format == "ttl":
        return Response(body, media_type="text/turtle; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.ttl"'})
    # Inline, in a sandbox: the page is generated by shacl-report from data published by others.
    headers = {"Content-Security-Policy": "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox "
                                          "allow-downloads allow-modals allow-forms"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{name}.html"'
    return Response(body, media_type="text/html; charset=utf-8", headers=headers)


@app.get("/api/quality/shapes")
def quality_shapes(env: str):
    """ERA's SHACL shapes graph the datasets were validated with (Turtle)."""
    _env(env)
    rows = _q("SELECT turtle_gz, source FROM quality_shapes WHERE env=%s", [env])
    if not rows or rows[0]["turtle_gz"] is None:
        raise HTTPException(404, "shapes not mirrored yet")
    src = rows[0]["source"] or {}
    version = "".join(c for c in (src.get("tag") or src.get("version") or "") if c.isalnum() or c in "._-")
    name = f"era-shacl-shapes-{env}{'-' + version if version else ''}.ttl"
    # The file starts with comments naming the source (graph or GitLab tag) and version.
    return Response(gzip.decompress(bytes(rows[0]["turtle_gz"])), media_type="text/turtle; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/shapes")
def shapes():
    """The facet shapes this instance was configured with (shapes/), and the classes each file defines."""
    d = _defs()
    by_file: dict[str, list[dict]] = defaultdict(list)
    for c in d["classes"]:
        if c.get("shape_file"):
            by_file[c["shape_file"]].append({"class": c["class"], "label": c["label"]})
    return [{"name": r["name"], "size": r["size"], "classes": by_file.get(r["name"], [])}
            for r in _q("SELECT name, length(turtle) AS size FROM shape_file ORDER BY name")]


@app.get("/api/shapes/file")
def shape_file(name: str, download: bool = False):
    """One facet shape file, as written in shapes/ (Turtle)."""
    rows = _q("SELECT turtle FROM shape_file WHERE name=%s", [name])
    if not rows:
        raise HTTPException(404, "no such shape file")
    headers = {}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{name.rsplit("/", 1)[-1]}"'
    return Response(rows[0]["turtle"], media_type="text/turtle; charset=utf-8", headers=headers)


# --- line profile ------------------------------------------------------------
# Built on the shape keys below (shapes/section-of-line.ttl, shapes/track.ttl):
# sections are chained through their opStart / opEnd link facets and read the
# parameters of the tracks that are part of them (feature_link rule 'from', the
# era:isPartOf / ^era:hasPart links the worker keeps for every feature; the same
# links colour sections by their tracks and drive the vehicle compatibility check).

SECTION_CLASS = "section-of-line"
TRACK_CLASSES = ["running-track", "track"]
OP_CLASS = "operational-point"
LINE_KEY = "nationalLine.lineId"
LENGTH_KEY = "lengthOfSectionOfLine"
DIRECTION_KEY = "trackDirection"
DEFAULT_PROFILE_PARAMS = [
    "maximumPermittedSpeed", "contactLineSystem.contactLineSystemType", "contactLineSystem.energySupplySystem",
    "wheelSetGauge", "etcs.etcsLevelType", "gaugingProfile", "lineCategory",
]
MAX_PROFILE_SECTIONS = int(os.environ.get("ATLAS_PROFILE_MAX_SECTIONS", "3000"))
_route_cache: dict[str, tuple[str, list[pf.Edge]]] = {}

_SECTION_COLS = f"""f.id, f.iri, f.label, f.graph_iri,
    f.facets -> 'opStart' ->> 0 AS a, f.facets -> 'opEnd' ->> 0 AS b,
    CASE WHEN jsonb_typeof(f.facets -> '{LENGTH_KEY}' -> 0) = 'number'
         THEN (f.facets -> '{LENGTH_KEY}' ->> 0)::float8 END AS len,
    CASE WHEN f.geom IS NOT NULL THEN ST_Length(f.geom::geography) / 1000 END AS glen"""


class ProfileRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    env: str
    line: str | None = None                       # national line id (nationalLine.lineId)
    graph: str | None = None                      # dataset of the line (line ids are national)
    sections: list[str] = Field(default_factory=list)   # section IRIs, any order
    from_: str | None = Field(None, alias="from")  # operational point IRIs: route between them
    to: str | None = None
    via: list[str] = Field(default_factory=list)
    params: list[str] | None = None               # facet keys for the strip rows
    reverse: bool = False


def _edge(r: dict) -> pf.Edge:
    length = r["len"] if r["len"] and r["len"] > 0 else r["glen"] if r["glen"] and r["glen"] > 0 else None
    return pf.Edge(r["iri"], r["a"], r["b"], length if length is not None else 0.0)


def _route_edges(env: str) -> list[pf.Edge]:
    """Every section of the environment as a graph edge, cached until the data changes."""
    version = _data_version(env)
    hit = _route_cache.get(env)
    if hit and hit[0] == version:
        return hit[1]
    rows = _q(f"""SELECT f.iri, f.facets -> 'opStart' ->> 0 AS a, f.facets -> 'opEnd' ->> 0 AS b,
                         CASE WHEN jsonb_typeof(f.facets -> '{LENGTH_KEY}' -> 0) = 'number'
                              THEN (f.facets -> '{LENGTH_KEY}' ->> 0)::float8 END AS len,
                         NULL::float8 AS glen
                  FROM feature f WHERE f.env = %s AND f.class = %s
                    AND f.facets ? 'opStart' AND f.facets ? 'opEnd'""", [env, SECTION_CLASS])
    edges = [_edge(r) for r in rows]
    for e in edges:
        if e.length <= 0:
            e.length = 1.0  # unknown length: still routable
    _route_cache[env] = (version, edges)
    return edges


def _ops(env: str, iris: set[str]) -> dict[str, dict]:
    if not iris:
        return {}
    rows = _q("""SELECT DISTINCT ON (f.iri) f.iri, f.id, f.label,
                        ST_X(ST_PointOnSurface(f.geom)) AS lon, ST_Y(ST_PointOnSurface(f.geom)) AS lat
                 FROM feature f WHERE f.env = %s AND f.iri = ANY(%s)
                 ORDER BY f.iri, (f.class = %s) DESC""", [env, list(iris), OP_CLASS])
    return {r["iri"]: r for r in rows}


def _value_label(v, labels: dict[str, str]) -> str:
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, str):
        return labels.get(v, _local(v) if v.startswith("http") else v)
    return str(v)


@app.get("/api/lines")
def lines(env: str, q: str = "", limit: int = Query(20, ge=1, le=100)):
    """National lines whose id starts with q (or contains it), with the dataset they belong to."""
    _env(env)
    q = q.strip()
    rows = _q(f"""
        SELECT v AS line, f.graph_iri, d.provider, count(*) AS sections,
               round(sum(CASE WHEN jsonb_typeof(f.facets -> '{LENGTH_KEY}' -> 0) = 'number'
                              THEN (f.facets -> '{LENGTH_KEY}' ->> 0)::numeric END), 3)::float8 AS length_km
        FROM feature f CROSS JOIN LATERAL jsonb_array_elements_text(f.facets -> %s) v
        LEFT JOIN dataset d ON d.env = f.env AND d.graph_iri = f.graph_iri
        WHERE f.env = %s AND f.class = %s AND f.facets ? %s AND v ILIKE %s
        GROUP BY 1, 2, 3
        ORDER BY (lower(v) = lower(%s)) DESC, (v ILIKE %s) DESC, length(v), v, d.provider
        LIMIT %s""", [LINE_KEY, env, SECTION_CLASS, LINE_KEY, f"%{q}%", q, f"{q}%", limit])
    return rows


@app.post("/api/profile")
def profile(req: ProfileRequest):
    """Sections of a national line, a list of sections, or a route between operational
    points, ordered along the line with the parameters of their tracks."""
    _env(req.env)
    env = req.env
    d = _defs()
    line_info = None
    missing: list[str] = []
    if req.line:
        rows = _q(f"SELECT {_SECTION_COLS} FROM feature f WHERE f.env = %s AND f.class = %s AND f.facets @> %s::jsonb",
                  [env, SECTION_CLASS, json.dumps({LINE_KEY: [req.line]})])
        per_graph: dict[str, int] = defaultdict(int)
        for r in rows:
            per_graph[r["graph_iri"]] += 1
        providers = {r["graph_iri"]: r["provider"] for r in _q(
            "SELECT graph_iri, provider FROM dataset WHERE env = %s AND graph_iri = ANY(%s)", [env, list(per_graph)])}
        graph = req.graph if req.graph in per_graph else max(sorted(per_graph), key=lambda g: per_graph[g], default=None)
        rows = [r for r in rows if r["graph_iri"] == graph]
        line_info = {"id": req.line, "graph_iri": graph, "provider": providers.get(graph),
                     "alternatives": [{"graph_iri": g, "provider": providers.get(g), "sections": n}
                                      for g, n in sorted(per_graph.items(), key=lambda x: -x[1])]}
        mode = "line"
    elif req.sections:
        rows = _q(f"SELECT {_SECTION_COLS} FROM feature f WHERE f.env = %s AND f.class = %s AND f.iri = ANY(%s)",
                  [env, SECTION_CLASS, list(dict.fromkeys(req.sections))])
        found = {r["iri"] for r in rows}
        missing = [s for s in req.sections if s not in found]
        mode = "sections"
    elif req.from_ and req.to:
        steps = pf.route(_route_edges(env), [req.from_, *req.via, req.to])
        if steps is None:
            raise HTTPException(404, "no connected sections of line between these operational points")
        rows = _q(f"SELECT {_SECTION_COLS} FROM feature f WHERE f.env = %s AND f.class = %s AND f.iri = ANY(%s)",
                  [env, SECTION_CLASS, [s.edge.key for s in steps]])
        mode = "route"
    else:
        raise HTTPException(400, "give a line, a list of sections, or from and to")

    by_iri = {r["iri"]: r for r in rows}
    truncated = False
    if mode == "route":  # lengths as shown: the data's, else the geometry's
        steps = [pf.Step(_edge(by_iri[s.edge.key]), s.reversed) for s in steps if s.edge.key in by_iri]
    else:
        edges = [_edge(r) for r in rows]
        nodes = {n for e in edges for n in (e.a, e.b) if n}
        ops = _ops(env, nodes)
        coords = {k: (v["lon"], v["lat"]) for k, v in ops.items() if v["lon"] is not None}
        steps = pf.default_orientation(pf.order_sections(edges, coords), coords)
    if req.reverse:
        steps = pf.reverse_steps(steps)
    if len(steps) > MAX_PROFILE_SECTIONS:
        steps, truncated = steps[:MAX_PROFILE_SECTIONS], True
    ops = _ops(env, {n for s in steps for n in (s.edge.a, s.edge.b) if n})
    coords = {k: (v["lon"], v["lat"]) for k, v in ops.items() if v["lon"] is not None}

    # parameters: facet keys of sections or tracks that can be summarised
    known = {c["class"] for c in d["classes"]}
    track_classes = [c for c in TRACK_CLASSES if c in known]
    wanted = DEFAULT_PROFILE_PARAMS if req.params is None else req.params
    params = []
    for k in dict.fromkeys(wanted):
        if d["kinds"].get(k) not in ("terms", "range", "boolean"):
            continue
        fd = next((f for f in d["facets"] if f["key"] == k and f["class"] in track_classes),
                  next(f for f in d["facets"] if f["key"] == k))
        params.append({"key": k, "label": fd["label"], "kind": fd["kind"], "unit": fd["unit"]})
    keys = [p["key"] for p in params]

    iris = [s.edge.key for s in steps]
    detail = {r["iri"]: r for r in _q("""
        SELECT f.iri, f.facets, ST_AsGeoJSON(ST_SimplifyPreserveTopology(f.geom, 0.00005), 6)::json AS geometry
        FROM feature f WHERE f.env = %s AND f.class = %s AND f.iri = ANY(%s)""", [env, SECTION_CLASS, iris])}
    tracks: dict[str, list[dict]] = defaultdict(list)
    wanted_set = set(iris)
    for i in range(0, len(iris), 1000):
        chunk = iris[i:i + 1000]
        if not track_classes or not chunk:
            break
        # DISTINCT: a track linked by both era:isPartOf and era:hasPart has two link rows.
        for r in _q("""SELECT DISTINCT ON (l.target, f.label, f.iri) l.target AS sol, f.iri, f.label, f.facets
                        FROM feature_link l
                        JOIN feature f ON f.env = l.env AND f.iri = l.iri AND f.class = l.class
                        WHERE l.env = %s AND l.rule = 'from' AND l.class = ANY(%s) AND l.target = ANY(%s)
                        ORDER BY l.target, f.label, f.iri""", [env, track_classes, chunk]):
            if r["sol"] in wanted_set:
                tracks[r["sol"]].append(r)

    label_iris: set[str] = set()
    for s in steps:
        for src in [detail.get(s.edge.key, {}).get("facets") or {}] + [t["facets"] for t in tracks[s.edge.key]]:
            for k in keys + [DIRECTION_KEY]:
                label_iris |= {v for v in src.get(k, []) if isinstance(v, str)}
    labels = _labels(env, label_iris)

    def vals(src: dict, k: str) -> list[dict]:
        return [{"value": v, "label": _value_label(v, labels)} for v in src.get(k, [])]

    sections_out, points = [], []
    km = 0.0
    gaps = 0

    def point(iri: str, at: float):
        o = ops.get(iri) or {}
        points.append({"iri": None if iri.startswith("?") else iri, "id": o.get("id"),
                       "label": o.get("label") or (None if iri.startswith("?") else _local(iri)),
                       "km": round(at, 3), "lon": o.get("lon"), "lat": o.get("lat")})

    for i, s in enumerate(steps):
        r = by_iri[s.edge.key]
        gap_km = None
        if i and s.gap_before:
            gaps += 1
            p, q = steps[i - 1].end, s.start
            gap_km = round(pf.haversine_km(coords[p], coords[q]), 3) if p in coords and q in coords else None
        if i == 0 or s.gap_before:
            point(s.start, km)
        length = s.edge.length
        sec_facets = detail.get(s.edge.key, {}).get("facets") or {}
        cells = {}
        for p in params:
            k = p["key"]
            raw = list(sec_facets.get(k, []))
            for t in tracks[s.edge.key]:
                raw += t["facets"].get(k, [])
            cell = pf.summarize(p["kind"], raw)
            if "values" in cell:
                for v in cell["values"]:
                    v["label"] = _value_label(v["value"], labels)
            cells[k] = cell
        sections_out.append({
            "id": r["id"], "iri": r["iri"], "label": r["label"], "graph_iri": r["graph_iri"],
            "from": None if s.start.startswith("?") else s.start, "to": None if s.end.startswith("?") else s.end,
            "reversed": s.reversed, "start_km": round(km, 3), "end_km": round(km + length, 3),
            "length_km": round(length, 3), "length_estimated": not (r["len"] and r["len"] > 0),
            "gap_before": bool(i and s.gap_before), "gap_km": gap_km,
            "geometry": detail.get(s.edge.key, {}).get("geometry"),
            "cells": cells,
            "tracks": [{"iri": t["iri"], "label": t["label"],
                        "direction": (vals(t["facets"], DIRECTION_KEY) or [{"label": None}])[0]["label"],
                        "values": {k: vals(t["facets"], k) for k in keys if t["facets"].get(k)}}
                       for t in tracks[s.edge.key]],
        })
        km += length
        point(s.end, km)
    return {"env": env, "mode": mode, "line": line_info, "params": params, "length_km": round(km, 3),
            "gaps": gaps, "sections": sections_out, "points": points, "missing": missing, "truncated": truncated}


# --- live describe (the only call that reaches ERA) -------------------------

_describe_cache: dict[tuple, tuple[float, dict]] = {}
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
SKOS_PREF = "http://www.w3.org/2004/02/skos/core#prefLabel"


def _sparql(endpoint: str, query: str) -> dict:
    try:
        r = httpx.post(endpoint, data={"query": query}, timeout=30,
                       headers={"Accept": "application/sparql-results+json",
                                "User-Agent": "rinf-atlas/0.1"})
        r.raise_for_status()
        return r.json()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"ERA SPARQL endpoint error: {e}")


def _term(b: dict) -> dict:
    t = {"type": "uri" if b["type"] == "uri" else "bnode" if b["type"] == "bnode" else "literal", "value": b["value"]}
    if b.get("datatype"):
        t["datatype"] = b["datatype"]
    if b.get("xml:lang"):
        t["lang"] = b["xml:lang"]
    return t


@app.get("/api/describe")
def describe(env: str, iri: str, direction: str = Query("out", pattern="^(out|in)$"), limit: int = Query(1000, le=5000)):
    e = _env(env)
    key = (env, iri, direction, limit)
    hit = _describe_cache.get(key)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    if "<" in iri or ">" in iri or " " in iri:
        raise HTTPException(400, "invalid IRI")
    if direction == "out":
        q = f"SELECT ?p ?o WHERE {{ <{iri}> ?p ?o }} LIMIT {limit}"
    else:
        q = f"SELECT ?s ?p WHERE {{ ?s ?p <{iri}> }} LIMIT {limit}"
    res = _sparql(e["sparql"], q)
    subj = {"type": "uri", "value": iri}
    triples = []
    for b in res["results"]["bindings"]:
        if direction == "out":
            triples.append({"s": iri, "p": b["p"]["value"], "o": _term(b["o"])})
        else:
            triples.append({"s": b["s"]["value"], "p": b["p"]["value"], "o": subj, "s_type": b["s"]["type"]})
    iris = {iri} | {t["p"] for t in triples}
    iris |= {t["o"]["value"] for t in triples if t["o"]["type"] == "uri"} | {t["s"] for t in triples}
    labels = _labels(env, iris)
    missing = [i for i in iris if i not in labels and i.startswith("http")][:300]
    if missing:
        vals = " ".join(f"<{i}>" for i in missing if ">" not in i)
        lr = _sparql(e["sparql"], f"""SELECT ?x ?l WHERE {{ VALUES ?x {{ {vals} }}
            ?x <{RDFS_LABEL}>|<{SKOS_PREF}> ?l FILTER(lang(?l) = 'en' || lang(?l) = '') }}""")
        for b in lr["results"]["bindings"]:
            labels.setdefault(b["x"]["value"], b["l"]["value"])
    out = {"iri": iri, "direction": direction, "triples": triples, "labels": labels,
           "truncated": len(triples) >= limit}
    if len(_describe_cache) > 2000:
        _describe_cache.clear()
    _describe_cache[key] = (time.time(), out)
    return out


# OGC API - Features and TileJSON for desktop GIS (imports this module, so last).
from . import gis  # noqa: E402

gis.install(app)

# Extra map layers configured by the deployer (config/layers.yaml), with a tile proxy for keys.
from . import layers as extra_layers  # noqa: E402

app.include_router(extra_layers.router)
