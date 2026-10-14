"""GIS access: OGC API - Features (Part 1 Core + GeoJSON, Part 2 CRS) and a
TileJSON / MapLibre style description of the vector tiles, so desktop GIS
(QGIS, ArcGIS Pro, GDAL) can load Atlas layers directly.

Two entry points serve the same tree:
  /api/ogc                      every environment and class, unfiltered
  /api/ogc/filtered/{state}     one environment, filtered by a query state
                                (base64url JSON, as for tiles; bbox ignored)
Collections are one per environment + class (`prod.operational-point`). Items
take bbox, limit/offset, q, dataset and one query parameter per facet key,
all mapped onto the same query state (query.Where) as the rest of the API.

Like everything except /api/describe, this reads PostGIS only.
"""
from __future__ import annotations

import base64
import inspect
import json
import math
import os
from datetime import UTC, datetime
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import export as xp
from . import main as core
from .query import QueryState, Range, Where

router = APIRouter()

DEFAULT_LIMIT = int(os.environ.get("ATLAS_OGC_DEFAULT_LIMIT", "100"))
MAX_LIMIT = int(os.environ.get("ATLAS_OGC_MAX_LIMIT", "10000"))
MAX_OFFSET = int(os.environ.get("ATLAS_OGC_MAX_OFFSET", "5000000"))
TIMEOUT_MS = int(os.environ.get("ATLAS_OGC_TIMEOUT_MS", "30000"))
TILE_MAXZOOM = int(os.environ.get("ATLAS_TILE_MAXZOOM", "14"))
PUBLIC_URL = os.environ.get("ATLAS_PUBLIC_URL", "").rstrip("/")

CRS84 = "http://www.opengis.net/def/crs/OGC/1.3/CRS84"
EPSG4326 = "http://www.opengis.net/def/crs/EPSG/0/4326"
EPSG3857 = "http://www.opengis.net/def/crs/EPSG/0/3857"
CRS_LIST = [CRS84, EPSG4326, EPSG3857]
CONFORMANCE = [
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
    "http://www.opengis.net/spec/ogcapi-features-2/1.0/conf/crs",
]
JSON = "application/json"
GEOJSON = "application/geo+json"
# Query parameters every items request accepts; any other must be a facet key.
ITEM_PARAMS = {"limit", "offset", "bbox", "bbox-crs", "crs", "datetime", "f", "q", "dataset", "values"}
FORMATS = {None, "json", "geojson", JSON, GEOJSON}
BASE_PROPS = ("iri", "label", "class", "dataset", "provider")


class OgcError(HTTPException):
    """Rendered as the OGC exception document {code, description}."""

    def __init__(self, status: int, code: str, description: str):
        super().__init__(status, description)
        self.code = code


def error_response(status: int, code: str, description: str) -> JSONResponse:
    return JSONResponse({"code": code, "description": description}, status_code=status,
                        headers={"Access-Control-Allow-Origin": "*"})


def bad(description: str) -> OgcError:
    return OgcError(400, "InvalidParameterValue", description)


def not_found(description: str) -> OgcError:
    return OgcError(404, "NotFound", description)


def _q(sql: str, params: list) -> list[dict]:
    """A read with a statement timeout, so one heavy GIS request cannot hog the database."""
    with core.pool.connection() as c:
        c.execute(f"SET LOCAL statement_timeout = {int(TIMEOUT_MS)}")
        return c.execute(sql, params).fetchall()


# --- URLs ---------------------------------------------------------------------

def public_base(request: Request) -> str:
    """Absolute origin the client used. ATLAS_PUBLIC_URL wins (set it when a TLS proxy
    sits in front of nginx); otherwise the forwarded headers, then the request itself."""
    if PUBLIC_URL:
        return PUBLIC_URL
    h = request.headers
    proto = (h.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (h.get("x-forwarded-host") or h.get("host") or request.url.netloc).split(",")[0].strip()
    return f"{proto}://{host}"


def _root(request: Request, state: str | None) -> str:
    return public_base(request) + "/api/ogc" + (f"/filtered/{state}" if state else "")


def _link(href: str, rel: str, type_: str = JSON, title: str | None = None) -> dict:
    out = {"href": href, "rel": rel, "type": type_}
    if title:
        out["title"] = title
    return out


def _json(body: dict, type_: str = JSON, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(body, media_type=type_, headers={"Access-Control-Allow-Origin": "*", **(headers or {})})


def _check_f(f: str | None) -> None:
    if f not in FORMATS:
        raise bad(f"unsupported format f={f!r}; this API serves JSON / GeoJSON only")


# --- collections ----------------------------------------------------------------

_coll_cache: dict[str, tuple[str, dict]] = {}

# GeometryType() -> (family, GeoJSON type). QGIS and GDAL give a layer one geometry
# type, so a class whose features mix families is served as one collection per family.
_GEOM = {"POINT": ("point", "Point"), "MULTIPOINT": ("point", "MultiPoint"),
         "LINESTRING": ("line", "LineString"), "MULTILINESTRING": ("line", "MultiLineString"),
         "POLYGON": ("polygon", "Polygon"), "MULTIPOLYGON": ("polygon", "MultiPolygon")}
_FAMILY_ORDER = ["point", "line", "polygon", "other"]
_FAMILY_TITLE = {"point": "points", "line": "lines", "polygon": "polygons", "other": "other geometries"}


def _union(a: list | None, b: list | None) -> list | None:
    if not a or not b:
        return a or b
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def _geom_type(types: set[str]) -> tuple[str | None, bool]:
    """A family's one GeoJSON type, and whether singles are promoted to Multi* for it."""
    if len(types) == 1:
        return next(iter(types)), False
    multi = [t for t in types if t.startswith("Multi")]
    if len(types) == 2 and len(multi) == 1:
        return multi[0], True
    return None, False


def _stats(env: str) -> dict:
    """Per class (shape order) its count and bbox, and the collections serving it: one
    per class, or, when the class mixes geometry families, one per family plus the
    combined collection (unlisted). Cached until the data changes."""
    version = core._data_version(env)
    hit = _coll_cache.get(env)
    if hit and hit[0] == version:
        return hit[1]
    rows = _q("""SELECT class, gt, n, ST_XMin(b) AS w, ST_YMin(b) AS s, ST_XMax(b) AS e, ST_YMax(b) AS n2
                 FROM (SELECT class, GeometryType(geom) AS gt, count(*) AS n, ST_Extent(geom) AS b
                       FROM feature WHERE env = %s GROUP BY class, gt) x""", [env])
    per: dict[str, dict] = {}
    for r in rows:
        c = per.setdefault(r["class"], {"count": 0, "bbox": None, "families": {}})
        c["count"] += r["n"]
        if r["gt"] is None:
            continue  # no geometry: only in the class's own (or combined) collection
        box = [r["w"], r["s"], r["e"], r["n2"]] if r["w"] is not None else None
        c["bbox"] = _union(c["bbox"], box)
        fam, gtype = _GEOM.get(r["gt"], ("other", "GeometryCollection"))
        fd = c["families"].setdefault(fam, {"count": 0, "bbox": None, "types": [], "geojson": set()})
        fd["count"] += r["n"]
        fd["bbox"] = _union(fd["bbox"], box)
        fd["types"].append(r["gt"])
        fd["geojson"].add(gtype)
    classes, entries = [], []
    for cdef in core._defs()["classes"]:
        c = per.get(cdef["class"])
        if not c:
            continue
        classes.append({**cdef, "count": c["count"], "bbox": c["bbox"]})
        fams = sorted(c["families"].items(), key=lambda kv: _FAMILY_ORDER.index(kv[0]))
        whole = {**cdef, "suffix": cdef["class"], "family": None, "types": None, "count": c["count"],
                 "bbox": c["bbox"]}
        if len(fams) <= 1:
            gtype, promote = _geom_type(fams[0][1]["geojson"]) if fams else (None, False)
            entries.append({**whole, "promote": promote, "geometryType": gtype, "listed": True})
            continue
        entries.append({**whole, "promote": False, "geometryType": None, "listed": False, "mixed": True})
        for fam, fd in fams:
            gtype, promote = _geom_type(fd["geojson"])
            entries.append({**cdef, "suffix": f"{cdef['class']}.{fam}", "family": fam, "types": fd["types"],
                            "count": fd["count"], "bbox": fd["bbox"], "promote": promote,
                            "geometryType": gtype, "listed": True})
    out = {"classes": classes, "entries": entries}
    _coll_cache[env] = (version, out)
    return out


def _class_stats(env: str) -> list[dict]:
    return _stats(env)["classes"]


def _decode(state: str | None) -> QueryState | None:
    if state is None:
        return None
    try:
        st = core._decode_state(state)
    except HTTPException as e:
        raise bad(str(e.detail))
    if st.env not in core._envs:
        raise not_found(f"unknown environment {st.env!r}")
    return st


def _envs_for(st: QueryState | None) -> list[str]:
    return [st.env] if st else list(core._envs)


def _facets_of(cls: str) -> list[dict]:
    """The filterable facets of a class (shared ones first), one per key."""
    seen, out = set(), []
    for scope in ("*", cls):
        for f in core._defs()["facets"]:
            if f["class"] == scope and f["kind"] != "text" and f["key"] not in seen:
                seen.add(f["key"])
                out.append(f)
    return out


def _collection(request: Request, state: str | None, env: str, c: dict, multi_env: bool) -> dict:
    cid = f"{env}.{c['suffix']}"
    base = f"{_root(request, state)}/collections/{cid}"
    env_label = core._envs[env].get("label", env.upper())
    params = ", ".join(f["key"] for f in _facets_of(c["class"]))
    qualifiers = ([env_label] if multi_env else []) + ([_FAMILY_TITLE[c["family"]]] if c["family"] else []) \
        + (["mixed geometry"] if c.get("mixed") else [])
    what = f"{c['label']} ({_FAMILY_TITLE[c['family']]})" if c["family"] else c["label"]
    desc = (f"{what} from RINF ({env_label}), {c['count']} features. "
            + ("Mixes geometry types: GIS clients should use the per-geometry collections. " if c.get("mixed") else "")
            + "Filter the items with query parameters: q, dataset" + (f", {params}" if params else "")
            + " (values comma separated; ranges as min..max).")
    out = {
        "id": cid,
        "title": c["label"] + (f" ({', '.join(qualifiers)})" if qualifiers else ""),
        "description": desc,
        "itemType": "feature",
        "crs": CRS_LIST,
        "storageCrs": CRS84,
        "links": [
            _link(base, "self"),
            _link(f"{base}/items", "items", GEOJSON, "Features (GeoJSON)"),
        ],
    }
    if c["geometryType"]:
        out["geometryType"] = c["geometryType"]  # not in the standard; a hint for clients
    if c["bbox"]:
        out["extent"] = {"spatial": {"bbox": [c["bbox"]], "crs": CRS84}}
    return out


def _resolve(state: str | None, cid: str) -> tuple[QueryState | None, str, dict]:
    st = _decode(state)
    env, _, suffix = cid.partition(".")
    if env not in core._envs or (st and env != st.env):
        raise not_found(f"no collection {cid!r}")
    for c in _stats(env)["entries"]:
        if c["suffix"] == suffix:
            if st and st.classes and c["class"] not in st.classes:
                raise not_found(f"no collection {cid!r} in this filtered view")
            return st, env, c
    raise not_found(f"no collection {cid!r}")


# --- handlers (registered twice: plain and filtered) -------------------------

def landing(request: Request, state: str | None = None, f: str | None = None):
    _check_f(f)
    st = _decode(state)
    root = _root(request, state)
    api = public_base(request) + "/api"
    title = "RINF Atlas" + (f" ({core._envs[st.env].get('label', st.env)}, filtered)" if st else "")
    return _json({
        "title": title,
        "description": "Infrastructure elements from the EU Register of Infrastructure (RINF), "
                       "indexed by RINF Atlas. One collection per environment and class.",
        "links": [
            _link(root, "self", JSON, "This document"),
            _link(f"{api}/openapi.json", "service-desc", "application/vnd.oai.openapi+json;version=3.1", "OpenAPI definition"),
            _link(f"{api}/docs", "service-doc", "text/html", "API documentation"),
            _link(f"{root}/conformance", "conformance", JSON, "Conformance classes"),
            _link(f"{root}/collections", "data", JSON, "Collections"),
        ],
    })


def conformance(state: str | None = None, f: str | None = None):
    _check_f(f)
    _decode(state)
    return _json({"conformsTo": CONFORMANCE})


def collections(request: Request, state: str | None = None, f: str | None = None):
    _check_f(f)
    st = _decode(state)
    envs = _envs_for(st)
    out = []
    for env in envs:
        for c in _stats(env)["entries"]:
            if not c["listed"] or (st and st.classes and c["class"] not in st.classes):
                continue
            out.append(_collection(request, state, env, c, len(core._envs) > 1))
    root = _root(request, state)
    return _json({"links": [_link(f"{root}/collections", "self")], "collections": out, "crs": CRS_LIST})


def collection(request: Request, cid: str, state: str | None = None, f: str | None = None):
    _check_f(f)
    _, env, c = _resolve(state, cid)
    return _json(_collection(request, state, env, c, len(core._envs) > 1))


def _crs(uri: str | None, what: str) -> str:
    if uri is None:
        return CRS84
    u = uri.strip().strip("[]")
    alias = {"EPSG:4326": EPSG4326, "EPSG:3857": EPSG3857, "CRS84": CRS84, "OGC:CRS84": CRS84}
    u = alias.get(u.upper() if u.upper().startswith(("EPSG", "OGC", "CRS")) else u, u)
    if u.startswith("https://"):
        u = "http://" + u[len("https://"):]
    if u not in CRS_LIST:
        raise bad(f"unsupported {what} {uri!r}; supported: {', '.join(CRS_LIST)}")
    return u


def _merc_to_lonlat(x: float, y: float) -> tuple[float, float]:
    r = 6378137.0
    return math.degrees(x / r), math.degrees(2 * math.atan(math.exp(y / r)) - math.pi / 2)


def _bbox(raw: str | None, crs: str) -> list[float] | None:
    if not raw:
        return None
    try:
        v = [float(x) for x in raw.split(",")]
    except ValueError:
        raise bad("bbox must be 4 or 6 comma separated numbers")
    if len(v) == 6:
        v = [v[0], v[1], v[3], v[4]]
    if len(v) != 4 or not all(math.isfinite(x) for x in v):
        raise bad("bbox must be 4 or 6 comma separated numbers")
    if crs == EPSG4326:  # lat/lon axis order
        v = [v[1], v[0], v[3], v[2]]
    elif crs == EPSG3857:
        w, s = _merc_to_lonlat(v[0], v[1])
        e, n = _merc_to_lonlat(v[2], v[3])
        v = [w, s, e, n]
    return v


def _num(s: str, key: str) -> float:
    try:
        x = float(s)
    except ValueError:
        raise bad(f"{key}: {s!r} is not a number")
    if not math.isfinite(x):
        raise bad(f"{key}: {s!r} is not a number")
    return x


def _facet_filter(f: dict, raw: list[str]):
    """Query parameter value(s) -> a query-state filter for facet f."""
    key = f["key"]
    if f["kind"] == "range":
        if len(raw) != 1:
            raise bad(f"{key}: give one range, min..max")
        s = raw[0].strip()
        if ".." in s:
            lo, hi = (x.strip() for x in s.split("..", 1))
            return Range(min=_num(lo, key) if lo else None, max=_num(hi, key) if hi else None)
        x = _num(s, key)
        return Range(min=x, max=x)
    vals = [v.strip() for r in raw for v in r.split(",") if v.strip()]
    if f["kind"] == "boolean":
        out = []
        for v in vals:
            lv = v.lower()
            if lv in ("true", "1", "yes"):
                out.append(True)
            elif lv in ("false", "0", "no"):
                out.append(False)
            else:
                raise bad(f"{key}: {v!r} is not true or false")
        return out
    return vals


def _item_state(request: Request, st: QueryState | None, env: str, cls: str,
                bbox: list[float] | None, q: str | None, dataset: str | None) -> QueryState:
    """The query state for an items request: the filtered view's state (if any),
    narrowed to this class, plus the request's bbox, q, dataset and facet parameters."""
    base = st.model_copy(deep=True) if st else QueryState(env=env)
    base.classes = [cls]
    base.bbox = bbox
    base.facets = None
    if q is not None:
        base.q = q
    if dataset is not None:
        base.graphs = [g.strip() for g in dataset.split(",") if g.strip()]
    facets = {f["key"]: f for f in _facets_of(cls)}
    unknown = []
    for key in dict.fromkeys(request.query_params.keys()):
        if key in ITEM_PARAMS:
            continue
        f = facets.get(key)
        if not f:
            unknown.append(key)
            continue
        base.filters[key] = _facet_filter(f, request.query_params.getlist(key))
    if unknown:
        raise bad(f"unknown query parameter(s) {', '.join(unknown)}; facets of this collection: "
                  f"{', '.join(facets) or '(none)'}")
    return base


def _geom_sql(crs: str, multi: bool) -> str:
    g = "ST_Multi(f.geom)" if multi else "f.geom"
    if crs == EPSG4326:
        return f"ST_AsGeoJSON(ST_FlipCoordinates({g}), 7, 0)::json"
    if crs == EPSG3857:
        return f"ST_AsGeoJSON(ST_Transform({g}, 3857), 2, 0)::json"
    return f"ST_AsGeoJSON({g}, 7, 0)::json"


def _where(qs: QueryState, c: dict) -> tuple[str, list]:
    """The query state's WHERE plus, for a per-geometry collection, its geometry types."""
    where_sql, params = Where(qs, core._defs()["owners"]).build()
    if c["types"]:
        where_sql += " AND GeometryType(f.geom) = ANY(%s)"
        params = params + [c["types"]]
    return where_sql, params


def _features(env: str, c: dict, where_sql: str, params: list, crs: str, values: str,
              limit: int, offset: int) -> list[dict]:
    cls = c["class"]
    rows = _q(f"""SELECT f.id, f.iri, f.label, f.class, f.facets, d.title AS dataset, d.provider,
                         {_geom_sql(crs, c["promote"])} AS geometry
                  FROM feature f LEFT JOIN dataset d ON d.env = f.env AND d.graph_iri = f.graph_iri
                  WHERE {where_sql} ORDER BY f.id LIMIT %s OFFSET %s""", params + [limit, offset])
    return _to_geojson(env, cls, rows, values)


def _to_geojson(env: str, cls: str, rows: list[dict], values: str) -> list[dict]:
    """Flat GeoJSON features with the same properties on every feature (iri, label, class,
    dataset, provider, then one per facet of the class), as GIS clients build their
    attribute table from the first features they see."""
    facets = _facets_of(cls)
    vals = xp.Values(lambda iris: core._labels(env, iris), core._local, values == "label")
    vals.prime(rows)
    class_label = next((c["label"] for c in core._defs()["classes"] if c["class"] == cls), cls)
    out = []
    for r in rows:
        props = {"iri": r["iri"], "label": r["label"], "class": class_label,
                 "dataset": r["dataset"], "provider": r["provider"]}
        for f in facets:
            vs = r["facets"].get(f["key"])
            if f["kind"] == "boolean" and vs and len(vs) == 1:
                v = vs[0]  # a real boolean, not Yes/No
            else:
                v = vals.cell(vs)
                if f["kind"] == "range" and vs and len(vs) > 1:
                    v = str(v)  # keeps the column numeric for single values only
            props[f["key"] if f["key"] not in BASE_PROPS else f"facet_{f['key']}"] = v
        out.append({"type": "Feature", "id": r["id"], "geometry": r["geometry"], "properties": props})
    return out


def _self_url(request: Request, **override) -> str:
    params = [(k, v) for k, v in request.query_params.multi_items() if k not in override]
    params += [(k, str(v)) for k, v in override.items() if v is not None]
    return str(request.url.path) + ("?" + urlencode(params) if params else "")


def items(request: Request, cid: str, state: str | None = None,
          limit: int | None = Query(None, ge=1, description=f"Page size (default {DEFAULT_LIMIT}, at most {MAX_LIMIT}; larger values are capped)",
                                    json_schema_extra={"maximum": MAX_LIMIT, "default": DEFAULT_LIMIT}),
          offset: int = Query(0, ge=0, le=MAX_OFFSET),
          bbox: str | None = Query(None, description="minx,miny,maxx,maxy in bbox-crs (default CRS84 lon/lat)"),
          bbox_crs: str | None = Query(None, alias="bbox-crs"),
          crs: str | None = Query(None, description="Output CRS: CRS84 (default), EPSG 4326 (lat/lon) or EPSG 3857"),
          datetime_: str | None = Query(None, alias="datetime", description="Accepted and ignored (no temporal data)"),
          f: str | None = None,
          q: str | None = Query(None, description="Full-text search (as in the UI), or a full IRI"),
          dataset: str | None = Query(None, description="Provider graph IRI(s), comma separated"),
          values: str = Query("label", pattern="^(label|iri)$", description="IRI-valued facets as labels or IRIs")):
    """Features of one collection as GeoJSON. Besides the parameters below, every facet key
    of the class is a query parameter: terms as comma separated values (IRIs or strings),
    booleans as true/false, ranges as min..max (either side optional)."""
    _check_f(f)
    st, env, c = _resolve(state, cid)
    out_crs = _crs(crs, "crs")
    box = _bbox(bbox, _crs(bbox_crs, "bbox-crs"))
    lim = min(limit or DEFAULT_LIMIT, MAX_LIMIT)
    qs = _item_state(request, st, env, c["class"], box, q, dataset)
    where_sql, params = _where(qs, c)
    feats = _features(env, c, where_sql, params, out_crs, values, lim, offset)
    matched = _q(f"SELECT count(*) AS n FROM feature f WHERE {where_sql}", params)[0]["n"]
    origin = public_base(request)
    links = [_link(origin + _self_url(request), "self", GEOJSON, "This page"),
             _link(f"{_root(request, state)}/collections/{cid}", "collection", JSON, "The collection")]
    if offset + len(feats) < matched:
        links.append(_link(origin + _self_url(request, offset=offset + len(feats), limit=lim), "next", GEOJSON, "Next page"))
    if offset > 0:
        links.append(_link(origin + _self_url(request, offset=max(0, offset - lim), limit=lim), "prev", GEOJSON, "Previous page"))
    body = {"type": "FeatureCollection", "features": feats, "numberMatched": matched,
            "numberReturned": len(feats), "timeStamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "links": links}
    return _json(body, GEOJSON, {"Content-Crs": f"<{out_crs}>"})


def item(request: Request, cid: str, fid: str, state: str | None = None, crs: str | None = None,
         f: str | None = None, values: str = Query("label", pattern="^(label|iri)$")):
    _check_f(f)
    st, env, c = _resolve(state, cid)
    out_crs = _crs(crs, "crs")
    if not fid.isdigit():
        raise not_found(f"no feature {fid!r}")
    qs = st.model_copy(deep=True) if st else QueryState(env=env)
    qs.classes, qs.bbox = [c["class"]], None
    where_sql, params = _where(qs, c)
    feats = _features(env, c, f"{where_sql} AND f.id = %s", params + [int(fid)], out_crs, values, 1, 0)
    if not feats:
        raise not_found(f"no feature {fid!r} in {cid}")
    coll = f"{_root(request, state)}/collections/{cid}"
    feats[0]["links"] = [_link(f"{coll}/items/{fid}", "self", GEOJSON), _link(coll, "collection", JSON)]
    return _json(feats[0], GEOJSON, {"Content-Crs": f"<{out_crs}>"})


def _unfiltered(fn):
    """fn without its `state` parameter (state=None), for the /api/ogc tree: otherwise
    FastAPI would take `state` as a query parameter there."""
    sig = inspect.signature(fn)

    def wrapper(**kw):
        return fn(state=None, **kw)
    wrapper.__signature__ = sig.replace(parameters=[p for p in sig.parameters.values() if p.name != "state"])
    wrapper.__name__, wrapper.__doc__ = fn.__name__, fn.__doc__
    return wrapper


_ROUTES = [("", landing, "Landing page"), ("/conformance", conformance, "Conformance classes"),
           ("/collections", collections, "Collections"), ("/collections/{cid}", collection, "Collection"),
           ("/collections/{cid}/items", items, "Features"), ("/collections/{cid}/items/{fid}", item, "Feature")]
for _path, _fn, _summary in _ROUTES:
    router.add_api_route("/api/ogc" + _path, _unfiltered(_fn), methods=["GET"], tags=["OGC API - Features"],
                         summary=_summary)
    router.add_api_route("/api/ogc/filtered/{state}" + _path, _fn, methods=["GET"], tags=["OGC API - Features"],
                         summary=f"{_summary} (filtered view)", operation_id=f"filtered_{_fn.__name__}")
# Clients (OWSLib) ask for the landing page with a trailing slash: answer it instead of
# redirecting, as a redirect built behind the proxy can point at the wrong host or port.
router.add_api_route("/api/ogc/", _unfiltered(landing), methods=["GET"], include_in_schema=False)
router.add_api_route("/api/ogc/filtered/{state}/", landing, methods=["GET"], include_in_schema=False)


# --- vector tiles for GIS: TileJSON + MapLibre style -------------------------

def _tile_state(state: str | None, env: str | None) -> tuple[QueryState, str]:
    if state:
        st = core._decode_state(state)
    else:
        env = env or next((k for k, e in core._envs.items() if e.get("default")), next(iter(core._envs)))
        st = QueryState(env=env)
    core._env(st.env)
    st.bbox, st.facets = None, None
    data = st.model_dump(exclude_defaults=True, exclude={"bbox", "facets"})
    data["env"] = st.env
    return st, base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).decode().rstrip("=")


def _tilejson(request: Request, state: str | None, env: str | None) -> dict:
    st, enc = _tile_state(state, env)
    stats = [c for c in _class_stats(st.env) if not st.classes or c["class"] in st.classes]
    boxes = [c["bbox"] for c in stats if c["bbox"]]
    bounds = ([min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]
              if boxes else [-180, -85.0511, 180, 85.0511])
    origin = public_base(request)
    return {
        "tilejson": "3.0.0",
        "name": f"RINF Atlas ({core._envs[st.env].get('label', st.env)})",
        "attribution": "RINF data: European Union Agency for Railways (ERA)",
        "scheme": "xyz",
        "tiles": [f"{origin}/api/tiles/{{z}}/{{x}}/{{y}}.pbf?state={enc}"],
        "minzoom": 0,
        "maxzoom": TILE_MAXZOOM,
        "bounds": bounds,
        "center": [(bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2, 6],
        "vector_layers": [{"id": "features", "minzoom": 0, "maxzoom": TILE_MAXZOOM,
                           "description": f"Below zoom {core.LOW_DETAIL_ZOOM} only id and class are set",
                           "fields": {"id": "Number", "iri": "String", "class": "String", "label": "String"}}],
        "_classes": stats,
    }


@router.get("/api/tiles.json", tags=["Vector tiles"], summary="TileJSON for the vector tiles")
def tilejson(request: Request, state: str | None = None, env: str | None = None):
    """TileJSON 3.0.0 describing /api/tiles for a query state (or a whole environment)."""
    tj = _tilejson(request, state, env)
    tj.pop("_classes")
    return _json(tj)


@router.get("/api/style.json", tags=["Vector tiles"], summary="MapLibre style for the vector tiles")
def style(request: Request, state: str | None = None, env: str | None = None):
    """A MapLibre GL style drawing the tiles with the UI's class colours (QGIS can load it
    as the style URL of a vector tile connection)."""
    tj = _tilejson(request, state, env)
    classes = tj.pop("_classes")
    layers = []
    for c in classes:
        color, kind, mz = c["color"] or "#888888", (c["geometry"] or "any").lower(), c["min_zoom"] or 0
        is_class = ["==", "class", c["class"]]
        if kind in ("line", "any", "linestring", "polygon"):
            layers.append({"id": f"{c['class']}-line", "type": "line", "source": "atlas", "source-layer": "features",
                           "minzoom": mz, "filter": ["all", is_class, ["!=", "$type", "Point"]],
                           "paint": {"line-color": color, "line-width": 2}})
        if kind in ("point", "any"):
            layers.append({"id": f"{c['class']}-point", "type": "circle", "source": "atlas", "source-layer": "features",
                           "minzoom": mz, "filter": ["all", is_class, ["==", "$type", "Point"]],
                           "paint": {"circle-color": color, "circle-radius": 4,
                                     "circle-stroke-color": "#ffffff", "circle-stroke-width": 1}})
    return _json({"version": 8, "name": tj["name"],
                  "sources": {"atlas": {"type": "vector", "tiles": tj["tiles"], "minzoom": 0,
                                        "maxzoom": TILE_MAXZOOM, "bounds": tj["bounds"], "attribution": tj["attribution"]}},
                  "layers": layers})


def install(app) -> None:
    """Adds the routes, and OGC style errors ({code, description}, 400 for bad parameters)."""
    from fastapi.exception_handlers import request_validation_exception_handler
    from fastapi.exceptions import RequestValidationError

    app.include_router(router)

    @app.exception_handler(OgcError)
    async def _ogc_error(request, exc: OgcError):
        return error_response(exc.status_code, exc.code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(request, exc: RequestValidationError):
        if not request.url.path.startswith("/api/ogc"):
            return await request_validation_exception_handler(request, exc)
        msg = "; ".join(f"{e['loc'][-1]}: {e['msg']}" for e in exc.errors())
        return error_response(400, "InvalidParameterValue", msg)
