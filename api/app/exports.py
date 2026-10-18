"""Export jobs (docs/export.md): queue, follow and download exports the worker produces.

The API never touches the mirror. It turns the query state into the selection SQL
(the same `Where` as search and tiles), stores the request in `export`, and the
worker, which owns the mirror, resolves every matching element and writes the
file to the shared exports volume. Downloads are served from that volume.

  POST   /api/exports                   queue an export        -> the export row
  GET    /api/exports/profiles?env=     export profiles, formats, limits
  GET    /api/exports/{id}              status and progress
  GET    /api/exports/{id}/download     the file (when done)
  GET    /api/exports/{id}/recipe       how the file was made (text)
  DELETE /api/exports/{id}              cancel (or forget) an export
"""
from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import main as core
from .query import QueryState, Where

router = APIRouter()

EXPORT_DIR = Path(os.environ.get("ATLAS_EXPORTS", "/data/exports"))
# 0 = no limit on the number of elements per export.
MAX_ENTITIES = int(os.environ.get("ATLAS_EXPORT_MAX_ENTITIES") or 0)
QUEUE_LIMIT = int(os.environ.get("ATLAS_EXPORT_QUEUE") or 20)

FORMATS = [
    {"key": "ttl", "label": "Turtle", "kind": "rdf", "ext": "ttl",
     "description": "Resolved RDF: every element with its components (net references, geometries, validity, contact line systems…)."},
    {"key": "nt", "label": "N-Triples", "kind": "rdf", "ext": "nt",
     "description": "The same triples one per line; the simplest to stream into any triple store."},
    {"key": "jsonld", "label": "JSON-LD", "kind": "rdf", "ext": "jsonld",
     "description": "One nested JSON object per element, components embedded, referenced elements and concepts with their labels."},
    {"key": "csv", "label": "CSV", "kind": "table", "ext": "csv",
     "description": "One table; columns from the chosen profile, or one row per value (long)."},
    {"key": "xlsx", "label": "Excel", "kind": "table", "ext": "xlsx",
     "description": "One sheet per class with the profile's columns, plus an Export sheet describing the query."},
    {"key": "geojson", "label": "GeoJSON", "kind": "gis", "ext": "geojson",
     "description": "FeatureCollection with the indexed geometry and the profile's columns as properties."},
    {"key": "gpkg", "label": "GeoPackage", "kind": "gis", "ext": "gpkg",
     "description": "One layer per class for QGIS or ArcGIS, with the profile's columns."},
]
RDF = {"ttl", "nt", "jsonld"}
MEDIA = {"ttl": "text/turtle; charset=utf-8", "nt": "application/n-triples; charset=utf-8",
         "jsonld": "application/ld+json", "csv": "text/csv; charset=utf-8",
         "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         "geojson": "application/geo+json", "gpkg": "application/geopackage+sqlite3"}
HAS_PART = "http://data.europa.eu/949/hasPart"


class ExportRequest(BaseModel):
    state: QueryState
    format: str = Field(pattern="^(ttl|nt|jsonld|csv|xlsx|geojson|gpkg)$")
    profile: str | None = None
    include: list[str] | None = None
    values: str = Field("label", pattern="^(label|iri|both)$")
    headers: str = Field("label", pattern="^(label|rinf|path)$")
    shape: str = Field("wide", pattern="^(wide|long)$")
    parts: bool = False
    gzip: bool = False


def _profiles() -> list[dict]:
    return [r["def"] for r in core._q("SELECT def FROM export_def ORDER BY (def->>'order')::int, key")]


def _row(eid: str) -> dict:
    rows = core._q("""SELECT id, env, request, status, total, done, bytes, filename, warning, error, datasets,
                             created_at, started_at, finished_at, expires_at FROM export WHERE id=%s""", [eid])
    if not rows:
        raise HTTPException(404, "unknown export")
    return rows[0]


def _public(r: dict) -> dict:
    req = r["request"]
    status = "queued" if r["status"] == "claimed" else r["status"]
    return {
        "id": r["id"], "env": r["env"], "status": status,
        "format": req.get("format"), "profile": req.get("profile"), "shape": req.get("shape"),
        "values": req.get("values"), "headers": req.get("headers"), "parts": bool(req.get("follow")),
        "gzip": bool(req.get("gzip")), "state": req.get("state"),
        "total": r["total"], "done": r["done"], "bytes": r["bytes"], "filename": r["filename"],
        "warning": r["warning"], "error": r["error"],
        "created_at": _iso(r["created_at"]), "started_at": _iso(r["started_at"]),
        "finished_at": _iso(r["finished_at"]), "expires_at": _iso(r["expires_at"]),
        "download": f"/api/exports/{r['id']}/download" if status == "done" else None,
        "recipe": f"/api/exports/{r['id']}/recipe",
    }


def _iso(t):
    return t.isoformat() if t else None


@router.get("/api/exports/profiles", tags=["Exports"])
def profiles(env: str):
    """Export profiles (from shapes/export/*.ttl, compiled by the worker), with the column tree per
    indexed class, plus the formats and limits of this instance."""
    core._env(env)
    d = core._defs()
    by_iri = {c["class_iri"]: c["class"] for c in d["classes"]}
    out = []
    for p in _profiles():
        classes = {}
        for iri, cp in (p.get("classes") or {}).items():
            key = by_iri.get(iri)
            if key:
                classes[key] = {"label": cp["label"], "columns": cp["columns"]}
        out.append({"key": p["key"], "label": p["label"], "comment": p.get("comment"), "classes": classes})
    return {"profiles": out, "formats": FORMATS,
            "limits": {"max_entities": MAX_ENTITIES or None, "queue": QUEUE_LIMIT},
            "parts_predicate": HAS_PART}


@router.post("/api/exports", status_code=202, tags=["Exports"])
def create(req: ExportRequest):
    """Queues an export of every feature matching the query state. The worker runs it; poll
    GET /api/exports/{id} until `status` is `done`, then fetch `download`."""
    st = req.state
    core._env(st.env)
    d = core._defs()
    sql, params = Where(st, d["owners"]).build()
    n = core._q(f"SELECT count(*) AS n FROM feature f WHERE {sql}", params)[0]["n"]
    if n == 0:
        raise HTTPException(400, "nothing matches this selection")
    if MAX_ENTITIES and n > MAX_ENTITIES:
        raise HTTPException(413, f"this instance limits exports to {MAX_ENTITIES} elements ({n} selected); narrow the selection")
    needs_profile = req.format in ("geojson", "gpkg") or (req.format in ("csv", "xlsx") and req.shape == "wide")
    if needs_profile:
        profiles = {p["key"] for p in _profiles()}
        if not profiles:
            raise HTTPException(503, "no export profile is defined on this instance (shapes/export/*.ttl)")
        if req.profile and req.profile not in profiles:
            raise HTTPException(400, f"unknown export profile {req.profile!r}")
    pending = core._q("SELECT count(*) AS n FROM export WHERE status IN ('queued','claimed','running')")[0]["n"]
    if pending >= QUEUE_LIMIT:
        raise HTTPException(429, "too many exports are queued right now; try again in a few minutes")
    state = {k: v for k, v in json.loads(st.model_dump_json()).items() if v not in (None, [], {})}
    request = {
        "env": st.env, "format": req.format, "sql": sql, "params": params, "state": state,
        "profile": req.profile if req.format not in RDF else None,
        "include": req.include if req.format not in RDF and req.shape == "wide" else None,
        "values": req.values, "headers": req.headers, "shape": req.shape if req.format in ("csv", "xlsx") else "wide",
        "follow": [HAS_PART] if req.parts else [], "gzip": bool(req.gzip and req.format not in ("xlsx", "gpkg")),
    }
    eid = secrets.token_urlsafe(12)
    with core.pool.connection() as c:
        c.execute("INSERT INTO export (id, env, request, total) VALUES (%s, %s, %s, %s)",
                  [eid, st.env, json.dumps(request, default=str), n])
    return _public(_row(eid))


@router.get("/api/exports/{eid}", tags=["Exports"])
def status(eid: str):
    return _public(_row(eid))


@router.get("/api/exports/{eid}/download", tags=["Exports"])
def download(eid: str):
    r = _row(eid)
    if r["status"] != "done":
        raise HTTPException(409, f"export is {r['status']}")
    rows = core._q("SELECT path FROM export WHERE id=%s", [eid])
    path = EXPORT_DIR / (rows[0]["path"] or "")
    if not rows[0]["path"] or not path.is_file():
        raise HTTPException(410, "the file is no longer available")
    fmt = r["request"].get("format")
    media = "application/gzip" if r["request"].get("gzip") else MEDIA.get(fmt, "application/octet-stream")
    return FileResponse(path, media_type=media, filename=r["filename"] or path.name,
                        headers={"Cache-Control": "private, max-age=3600"})


@router.get("/api/exports/{eid}/recipe", response_class=PlainTextResponse, tags=["Exports"])
def recipe(eid: str):
    """How the export was made, so anyone can reproduce or verify it against ERA's endpoint."""
    r = _row(eid)
    req = r["request"]
    env = core._env(r["env"])
    lines = [
        "RINF Atlas export recipe", "=" * 24, "",
        f"Export:       {r['id']}  ({r['status']})",
        f"Created:      {_iso(r['created_at'])}",
        f"Environment:  {r['env']}  (ERA SPARQL endpoint: {env['sparql']})",
        f"Format:       {req.get('format')}" + (f", profile {req.get('profile')!r}" if req.get("profile") else "")
        + (f", {req.get('shape')} table" if req.get("format") in ("csv", "xlsx") else ""),
        f"Options:      values={req.get('values')}, headers={req.get('headers')}, parts={'yes' if req.get('follow') else 'no'}",
        f"Elements:     {r['total'] if r['total'] is not None else '?'}",
        "", "Selection (RINF Atlas query state; classes, facet filters, search, datasets, map view):",
        "  " + json.dumps(req.get("state"), ensure_ascii=False),
        "",
    ]
    if r["datasets"]:
        lines.append("Datasets read (named graphs of the ERA repository, with the dataset explorer fingerprint):")
        for ds in r["datasets"]:
            lines.append(f"  {ds['graph']}  provider={ds.get('provider') or '-'}  modified={ds.get('modified') or '-'}  "
                         f"fingerprint={ds.get('fingerprint') or '-'}")
        lines.append("")
    if r["warning"]:
        lines += [f"Warning: {r['warning']}", ""]
    lines += [
        "Method",
        "------",
        "1. The index (PostGIS) selects the IRIs of the elements matching the selection; each belongs to one provider graph G.",
        "2. For each graph, the elements are read from the local mirror of G (an exact copy of ERA's named graph) in",
        "   batches, hop by hop:",
        "     SELECT ?s ?p ?o WHERE { GRAPH <G> { VALUES ?s { <iri> ... } ?s ?p ?o } }",
        "   Every IRI object is classified with",
        "     SELECT ?o ?t WHERE { GRAPH <G> { VALUES ?o { <iri> ... } ?o a ?t } }",
        "   - typed as an infrastructure element (rdfs:subClassOf* era:InfrastructureElement): kept as a reference"
        + (" (except the objects of era:hasPart, which are resolved one level too);" if req.get("follow") else ";"),
        "   - anything else with triples in G (net references, geometries, validity periods, contact line systems,",
        "     ETCS, load capabilities, documents...): resolved the same way, up to 8 hops;",
        "   - without triples in G (concepts, countries, organisations, external documents): a reference.",
        "3. References get a label: skos:prefLabel or rdfs:label from the vocabulary graphs",
        "     SELECT ?i ?l WHERE { VALUES ?i { <iri> ... } ?i skos:prefLabel|rdfs:label ?l }",
        "   and, for elements, the label indexed by RINF Atlas.",
        "4. Blank nodes are written as blank nodes again (the mirror stores them under urn:atlas:bnode:... IRIs).",
    ]
    if req.get("format") not in RDF:
        lines += [
            f"5. Table columns come from the export profile {req.get('profile') or 'full'!r} (shapes/export/*.ttl): each column is a",
            "   SHACL property path read from the resolved description; several values are joined with '; '."
            if req.get("shape") != "long" else
            "5. The long table has one row per value, with the property path from the element (dotted local names).",
        ]
    lines += ["", "To check an element against ERA directly, run on the endpoint above:",
              "  SELECT ?s ?p ?o WHERE { GRAPH <G> { <element IRI> ?p ?o . BIND(<element IRI> AS ?s) } }",
              "and repeat for each component IRI it points to.", ""]
    return "\n".join(lines)


@router.delete("/api/exports/{eid}", status_code=204, tags=["Exports"])
def cancel(eid: str):
    """Cancels a queued or running export (the worker stops at its next batch), or expires a finished
    one; the worker deletes the file (this service mounts the exports folder read-only)."""
    r = _row(eid)
    with core.pool.connection() as c:
        if r["status"] in ("queued", "claimed", "running"):
            c.execute("UPDATE export SET status='cancelled', finished_at=now() WHERE id=%s AND status IN ('queued','claimed','running')", [eid])
        else:
            c.execute("UPDATE export SET status='expired', expires_at=now() WHERE id=%s", [eid])
    return Response(status_code=204)
