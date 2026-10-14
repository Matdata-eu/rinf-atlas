"""RINF Atlas HTTP API (contract: docs/api.md).

Everything is answered from PostGIS, except /api/describe, which is the one
place that goes live to ERA's SPARQL endpoint (describe + graph traversal).
"""
from __future__ import annotations

import base64
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
from starlette.background import BackgroundTask

from . import compat as cx
from . import export as xp
from . import turtle as ttl
from .query import QueryState, SearchRequest, Where, split_key

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://atlas:atlas@db:5432/atlas")
CONFIG = Path(os.environ.get("ATLAS_CONFIG", "/app/config/environments.yaml"))
TERMS_LIMIT = int(os.environ.get("ATLAS_TERMS_LIMIT", "200"))
LOW_DETAIL_ZOOM = int(os.environ.get("ATLAS_LOW_DETAIL_ZOOM", "8"))
TILE_CACHE_SECONDS = int(os.environ.get("ATLAS_TILE_CACHE_SECONDS", "300"))
# Turtle export queries ERA live (batches of 100 IRIs), so it is capped.
TTL_EXPORT_MAX = int(os.environ.get("ATLAS_TTL_EXPORT_MAX", "20000"))

pool: ConnectionPool | None = None
_envs: dict[str, dict] = {}
_defs_cache: dict = {"t": 0.0}


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
                     "geometry": c["geometry"], "min_zoom": c["min_zoom"], "count": counts.get(c["class"], 0)}
                    for c in d["classes"]],
        "facets": [{"key": f["key"], "class": f["class"], "label": f["label"], "kind": f["kind"],
                    "datatype": f["datatype"], "unit": f["unit"], "position": f["position"]}
                   for f in d["facets"] if f["kind"] != "text"],
        "limits": {"ttl_export": TTL_EXPORT_MAX},
        "datasets": {"total": ds["total"], "loaded": ds["loaded"], "failed": ds["failed"], "pending": ds["pending"],
                     "last_ingest": ds["last_ingest"].isoformat() if ds["last_ingest"] else None,
                     "last_check": ds["last_check"].isoformat() if ds["last_check"] else None},
    }


def _facet_keys(state: QueryState, d: dict) -> list[str]:
    if state.facets is not None:
        out = []
        for k in state.facets:
            scope, fk = split_key(k)
            if fk in d["kinds"] and d["kinds"][fk] != "text" and (scope is None or scope in d["owners"][fk]):
                out.append(k)
        return out
    wanted = set(state.classes) | {"*"}
    keys = []
    for f in d["facets"]:
        if f["class"] in wanted and f["kind"] != "text" and f["key"] not in keys:
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
    jobs = [_executor.submit(lambda: {r["class"]: r["n"] for r in _q(
        f"SELECT f.class, count(*) AS n FROM feature f WHERE {sql_nc} GROUP BY 1", p_nc)})]
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
    by_class = jobs[0].result()
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
    return {"total": total, "by_class": by_class, "facets": result}


@app.post("/api/search")
def search(req: SearchRequest):
    _env(req.env)
    w = Where(req, _defs()["owners"])
    sql, p = w.build()
    total = _q(f"SELECT count(*) AS n FROM feature f WHERE {sql}", p)[0]["n"]
    order = "cd.position NULLS LAST, f.label NULLS LAST, f.id"
    op: list = []
    if req.q and req.q.strip() and not req.q.startswith("http"):
        # exact label, then label prefix, then important classes first (class order in shapes)
        order = ("(lower(f.label) = lower(%s)) DESC, (f.label ILIKE %s) DESC, cd.position NULLS LAST, "
                 "length(f.label), f.label, f.id")
        op = [req.q.strip(), req.q.strip() + "%"]
    rows = _q(f"""SELECT f.id, f.iri, f.class, f.label,
                         ST_X(ST_PointOnSurface(f.geom)) AS lon, ST_Y(ST_PointOnSurface(f.geom)) AS lat
                  FROM feature f LEFT JOIN class_def cd ON cd.class = f.class
                  WHERE {sql} ORDER BY {order} LIMIT %s OFFSET %s""",
              p + op + [req.limit, req.offset])
    return {"total": total, "items": rows}


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
def tiles(z: int, x: int, y: int, state: str):
    st = _decode_state(state)
    _env(st.env)
    st.bbox = None
    d = _defs()
    allowed = [c for c, mz in d["min_zoom"].items() if mz <= z and (not st.classes or c in st.classes)]
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
    props = "f.id, f.class" if low else "f.id, f.iri, f.class, f.label"
    dedup = "DISTINCT ON (class, geom)" if low else ""
    # A coarser tile grid at low zoom: fewer vertices and more pixel-duplicates removed.
    extent = 512 if low else 4096
    rows = _q(f"""
        WITH bounds AS (SELECT ST_TileEnvelope(%s, %s, %s) AS b),
        mvt AS (
            SELECT {dedup} * FROM (
                SELECT f.id AS fid, {props},
                       ST_AsMVTGeom({geom}, bounds.b, {extent}, {extent // 64}, true) AS geom
                FROM feature f, bounds
                WHERE f.geom3857 && ST_Expand(bounds.b, (ST_XMax(bounds.b) - ST_XMin(bounds.b)) / 64) AND {sql}
            ) t WHERE geom IS NOT NULL)
        SELECT ST_AsMVT(mvt.*, 'features', {extent}, 'geom', 'fid') AS tile FROM mvt
    """, [z, x, y] + p)
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
        facets_out[k] = [{"value": v, "label": labels.get(v, _local(v)) if isinstance(v, str) else
                          ("Yes" if v is True else "No" if v is False else str(v))} for v in vs]
    return {"iri": r["iri"], "label": r["label"], "classes": [x["class"] for x in rows],
            "class_iri": r["class_iri"], "graph_iri": r["graph_iri"], "provider": r["provider"],
            "dataset": r["dataset"], "geometry_source": r["geom_source"],
            "facets": facets_out, "facet_labels": {k: facet_labels.get(k, k) for k in r["facets"]},
            "geometry": r["geometry"]}


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
        item = {k: r[k] for k in ("key", "label", "comment", "path", "constraint", "kind", "datatype", "unit",
                                  "facets", "applies_to")}
        if r["kind"] == "number":
            nums = [float(x["value"]) for x in rows if isinstance(x["value"], (int, float)) and not isinstance(x["value"], bool)]
            item["range"] = {"min": min(nums) if nums else None, "max": max(nums) if nums else None,
                             "count": sum(x["n"] for x in rows)}
        else:
            vals = sorted(rows, key=lambda x: (-x["n"], str(x["value"])))[:TERMS_LIMIT]
            item["values"] = vals
            iris |= {x["value"] for x in vals if isinstance(x["value"], str)}
        out.append(item)
    labels = _labels(env, iris)
    for item in out:
        for v in item.get("values", []):
            x = v["value"]
            v["label"] = labels.get(x, _local(x)) if isinstance(x, str) else ("Yes" if x is True else "No")
            v["count"] = v.pop("n")
    return {"track_classes": cdef["track_classes"], "section_class": cdef["section_class"], "rules": out}


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

    def results(row: dict, cs: list[cx.Check]) -> list[dict]:
        out = []
        for i, c in enumerate(cs):
            vals = [v for fk in rules[c.key]["facets"] for v in row["facets"].get(fk, [])]
            out.append({"key": c.key, "result": cx.RESULT[row[f"c{i}"]], "values": vals})
        return out

    tracks = []
    if track:
        tsql, tp = cx.track_detail_sql(cdef, track, req.env, iri)
        for r in _q(tsql, tp):
            res = results(r, track)
            worst = max(r[f"c{i}"] for i in range(len(track)))
            tracks.append({"id": r["id"], "iri": r["iri"], "label": r["label"], "class": r["class"],
                           "status": cx.STATUS[worst], "results": res})
    sec = []
    if section:
        ssql, sp = cx.section_detail_sql(cdef, section, req.env, iri)
        rows = _q(ssql, sp)
        sec = results(rows[0], section) if rows else []
    vals = {v for t in tracks for x in t["results"] for v in x["values"] if isinstance(v, str)}
    vals |= {v for x in sec for v in x["values"] if isinstance(v, str)}
    labels = _labels(req.env, vals)

    def lab(res: list[dict]) -> None:
        for x in res:
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
                        (SELECT count(*) FROM feature f WHERE f.env=d.env AND f.graph_iri=d.graph_iri) AS features
                 FROM dataset d WHERE d.env=%s AND d.kind='provider'
                 ORDER BY d.modified DESC NULLS LAST, d.graph_iri""", [env])
    for r in rows:
        for k in ("modified", "ingested_at"):
            if r[k]:
                r[k] = r[k].isoformat()
    return rows


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
