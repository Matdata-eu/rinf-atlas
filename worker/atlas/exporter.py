"""Export jobs: resolved descriptions of the features matching a query, written to a file.

The API inserts a row in `export` (the selection as SQL over `feature`, the format,
profile and options); the worker, which owns the mirror, picks it up here, resolves
every matching element from the mirror (atlas.resolve), streams the chosen format
to `{export_dir}/{id}.{ext}` and keeps the row's progress current. The API serves
the file. Finished files are removed after `export_retention_hours`.

Formats
  ttl, nt, jsonld   the resolved descriptions as RDF (no profile needed)
  csv, xlsx         one row per element, columns from an export profile ("wide"),
                    or one row per value with its property path ("long")
  geojson, gpkg     the same wide rows with the indexed geometry, for GIS

Jobs run in a small thread pool next to the poll loop: pyoxigraph's Store is
thread-safe, so exports read while a sync loads other graphs. A graph replaced
while an export reads it is reported in the result (`warning`).
"""
from __future__ import annotations

import csv
import gzip
import io
import json
import logging
import os
import sqlite3
import struct
import threading
import time
import traceback
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pyoxigraph as ox
from psycopg.types.json import Jsonb

from . import db, mirror, rdfout
from .config import Settings
from .profiles import ClassProfile, Column, Profile, cell, header_text, load_profiles, local
from .resolve import Lit, Node, Ref, Resolver, element_classes

log = logging.getLogger(__name__)

FORMATS = {
    "ttl": ("ttl", "text/turtle; charset=utf-8"),
    "nt": ("nt", "application/n-triples; charset=utf-8"),
    "jsonld": ("jsonld", "application/ld+json"),
    "csv": ("csv", "text/csv; charset=utf-8"),
    "xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "geojson": ("geojson", "application/geo+json"),
    "gpkg": ("gpkg", "application/geopackage+sqlite3"),
}
RDF_FORMATS = ("ttl", "nt", "jsonld")
TABLE_FORMATS = ("csv", "xlsx", "geojson", "gpkg")
BATCH = 500
XLSX_MAX_ROWS = 1_048_575
XLSX_MAX_CELL = 32_767
ERA = "http://data.europa.eu/949/"
BASE_COLUMNS = [("iri", "IRI"), ("label", "Label"), ("class", "Class"), ("dataset", "Dataset"),
                ("provider", "Provider"), ("lon", "Longitude"), ("lat", "Latitude"), ("wkt", "Geometry (WKT)")]


@dataclass
class Request:
    """What the API stored in export.request."""
    env: str
    format: str
    sql: str                      # WHERE over feature f (built by the API from the query state)
    params: list
    state: dict = field(default_factory=dict)
    profile: str | None = None    # export profile key (tables)
    include: list[str] | None = None   # column keys (a key selects its sub-columns), None = all
    values: str = "label"         # label | iri | both, for IRI values
    headers: str = "label"        # label | rinf | path (table headers)
    shape: str = "wide"           # wide | long (csv, xlsx)
    follow: list[str] = field(default_factory=list)   # predicates whose elements are resolved too (era:hasPart)
    gzip: bool = False

    @classmethod
    def parse(cls, d: dict) -> Request:
        known = {f for f in cls.__dataclass_fields__}
        r = cls(**{k: v for k, v in d.items() if k in known})
        if r.format not in FORMATS:
            raise ValueError(f"unknown format {r.format!r}")
        if r.values not in ("label", "iri", "both") or r.headers not in ("label", "rinf", "path") \
                or r.shape not in ("wide", "long"):
            raise ValueError("bad export options")
        return r


class Cancelled(Exception):
    pass


# --- the job --------------------------------------------------------------------------

class Job:
    def __init__(self, cfg: Settings, conn: psycopg.Connection, row: dict, profiles: list[Profile]):
        self.cfg = cfg
        self.conn = conn
        self.id = row["id"]
        self.req = Request.parse(row["request"])
        self.store = mirror.store(cfg.mirror_dir, self.req.env)
        self.profiles = {p.key: p for p in profiles}
        self.started = time.time()
        self.done = 0
        self.total = 0
        self.graphs: set[str] = set()
        self._last_progress = 0.0
        self._class_defs: dict[str, dict] = {}
        self._resolvers: dict[str, Resolver] = {}
        self._supers: dict[str, list[str]] | None = None

    # -- selection ------------------------------------------------------------------

    def count(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM feature f WHERE {self.req.sql}", self.req.params)
            return cur.fetchone()[0]

    def rows(self) -> Iterator[list[dict]]:
        """Matching features in batches, grouped by graph then class (so a batch is usually one graph)."""
        with self.conn.cursor(name=f"export_{self.id}") as cur:
            cur.itersize = BATCH
            cur.execute(f"""SELECT f.iri, f.class, f.class_iri, f.graph_iri, f.label, d.provider, d.title,
                                   ST_X(ST_PointOnSurface(f.geom)) AS lon, ST_Y(ST_PointOnSurface(f.geom)) AS lat,
                                   ST_AsText(f.geom, 7) AS wkt, ST_AsGeoJSON(f.geom, 7)::text AS geojson,
                                   ST_AsBinary(f.geom) AS wkb
                            FROM feature f LEFT JOIN dataset d ON d.env = f.env AND d.graph_iri = f.graph_iri
                            WHERE {self.req.sql} ORDER BY f.graph_iri, f.class, f.iri""", self.req.params)
            names = [c.name for c in cur.description]
            batch: list[dict] = []
            for r in cur:
                row = dict(zip(names, r, strict=True))
                if batch and (len(batch) >= BATCH or batch[-1]["graph_iri"] != row["graph_iri"]):
                    yield batch
                    batch = []
                batch.append(row)
            if batch:
                yield batch

    def class_defs(self) -> dict[str, dict]:
        if not self._class_defs:
            with self.conn.cursor() as cur:
                cur.execute("SELECT class, class_iri, label FROM class_def")
                self._class_defs = {r[0]: {"class_iri": r[1], "label": r[2]} for r in cur.fetchall()}
        return self._class_defs

    def element_labels(self, iris: set[str]) -> dict[str, str]:
        if not iris:
            return {}
        with self.conn.cursor() as cur:
            cur.execute("SELECT DISTINCT ON (iri) iri, label FROM feature WHERE env=%s AND iri = ANY(%s) AND label IS NOT NULL",
                        (self.req.env, sorted(iris)))
            return dict(cur.fetchall())

    def resolver(self, graph: str) -> Resolver:
        if graph not in self._resolvers:
            if not hasattr(self, "_elements"):
                shaped = [d["class_iri"] for d in self.class_defs().values()]
                self._elements = element_classes(self.store, shaped)
            self._resolvers[graph] = Resolver(self.store, graph, self._elements, self.element_labels,
                                              follow=self.req.follow)
        return self._resolvers[graph]

    def resolve(self, batch: list[dict]) -> dict[str, Node]:
        out: dict[str, Node] = {}
        by_graph: dict[str, list[str]] = {}
        for r in batch:
            by_graph.setdefault(r["graph_iri"], []).append(r["iri"])
        for g, iris in by_graph.items():
            self.graphs.add(g)
            out.update(self.resolver(g).resolve(iris))
        return out

    # -- profiles -------------------------------------------------------------------

    def profile(self) -> Profile | None:
        if self.req.format in RDF_FORMATS or self.req.shape == "long":
            return None
        key = self.req.profile or ("full" if "full" in self.profiles else next(iter(self.profiles), None))
        if key is None:
            raise ValueError("no export profile is defined (shapes/export/*.ttl)")
        if key not in self.profiles:
            raise ValueError(f"unknown export profile {key!r}")
        return self.profiles[key]

    def supers(self, cls: str) -> list[str]:
        """Superclasses of a class from the mirrored ontology, nearest first."""
        if self._supers is None:
            self._supers = {}
            try:
                q = "SELECT ?c ?s WHERE { ?c <http://www.w3.org/2000/01/rdf-schema#subClassOf> ?s }"
                for r in mirror.select(self.store, q, union=True):
                    if isinstance(r["c"], ox.NamedNode) and isinstance(r["s"], ox.NamedNode):
                        self._supers.setdefault(r["c"].value, []).append(r["s"].value)
            except Exception:  # noqa: BLE001
                pass
        chain, todo = [], list(self._supers.get(cls, []))
        while todo:
            x = todo.pop(0)
            if x not in chain and x != cls:
                chain.append(x)
                todo.extend(self._supers.get(x, []))
        return chain

    def class_profile(self, prof: Profile, class_iri: str) -> ClassProfile | None:
        if class_iri in prof.classes:
            return prof.classes[class_iri]
        for s in self.supers(class_iri):
            if s in prof.classes:
                return prof.classes[s]
        return None

    def columns(self, prof: Profile, class_iri: str) -> list[Column]:
        cp = self.class_profile(prof, class_iri)
        if cp is None:
            return []
        inc = set(self.req.include) if self.req.include is not None else None
        return cp.select(inc)

    # -- progress -------------------------------------------------------------------

    def progress(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_progress < 1.5:
            return
        self._last_progress = now
        with self.conn.cursor() as cur:
            cur.execute("UPDATE export SET done=%s, total=%s WHERE id=%s RETURNING status", (self.done, self.total, self.id))
            status = cur.fetchone()
        self.conn.commit()
        if status and status[0] == "cancelled":
            raise Cancelled()

    def datasets(self) -> list[dict]:
        if not self.graphs:
            return []
        with self.conn.cursor() as cur:
            cur.execute("""SELECT graph_iri, provider, fingerprint, modified, status FROM dataset
                           WHERE env=%s AND graph_iri = ANY(%s) ORDER BY graph_iri""", (self.req.env, sorted(self.graphs)))
            return [{"graph": r[0], "provider": r[1], "fingerprint": r[2],
                     "modified": r[3].isoformat() if r[3] else None, "status": r[4]} for r in cur.fetchall()]


# --- writers --------------------------------------------------------------------------

def _meta(job: Job, total: int) -> dict:
    return {"environment": job.req.env, "profile": job.req.profile, "format": job.req.format,
            "entities": total, "exported": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generator": "RINF Atlas", "state": job.req.state}


def write_rdf(job: Job, out, total: int) -> None:
    fmt = job.req.format
    if fmt == "ttl":
        out.write(rdfout.header([
            f"RINF Atlas export: resolved descriptions of {total} infrastructure elements ({job.req.env})",
            f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC. Components (net references, geometries, validity, contact line",
            "systems...) are included; other elements are referenced by IRI. See the export's recipe for the method.",
        ]).encode())

    def batches():
        for batch in job.rows():
            nodes = job.resolve(batch)
            roots = [nodes[r["iri"]] for r in batch]
            job.done += len(batch)
            job.progress()
            yield batch, roots

    if fmt == "jsonld":
        extra: dict[str, dict] = {}

        def roots_iter():
            for batch, roots in batches():
                extra.clear()
                for r, n in zip(batch, roots, strict=True):
                    extra[n.id] = {"atlas:dataset": r["graph_iri"], "atlas:class": r["class"]}
                yield from roots

        for chunk in rdfout.jsonld(roots_iter(), meta=_meta(job, total), extra=extra):
            out.write(chunk)
        return
    for batch, roots in batches():
        comments = {n.id: f"{r['class']} from {r['graph_iri']}" for r, n in zip(batch, roots, strict=True)} \
            if fmt == "ttl" else None
        gen = rdfout.turtle(roots, comments) if fmt == "ttl" else rdfout.ntriples(roots)
        for chunk in gen:
            out.write(chunk)


def _base_cells(r: dict, values: str) -> list:
    return [r["iri"], r["label"] or "", r["class"], r["title"] or r["graph_iri"], r["provider"] or "",
            r["lon"], r["lat"], r["wkt"] or ""]


def _row(job: Job, r: dict, node: Node, cols: list[Column]) -> list:
    return _base_cells(r, job.req.values) + [cell(node, c, job.req.values) for c in cols]


def _present_classes(job: Job) -> list[tuple[str, str]]:
    """(class key, class IRI) of the classes in the selection, in shape order."""
    with job.conn.cursor() as cur:
        cur.execute(f"""SELECT DISTINCT f.class, f.class_iri FROM feature f WHERE {job.req.sql}""", job.req.params)
        present = cur.fetchall()
        cur.execute("SELECT class, position FROM class_def")
        pos = dict(cur.fetchall())
    return sorted(present, key=lambda c: (pos.get(c[0], 999), c[0]))


def union_columns(job: Job, prof: Profile) -> list[Column]:
    cols: list[Column] = []
    seen: set[str] = set()
    for _, class_iri in _present_classes(job):
        for c in job.columns(prof, class_iri):
            if c.key not in seen:
                seen.add(c.key)
                cols.append(c)
    return cols


def _long_rows(root: Node, r: dict, values: str):
    """One row per value: (iri, class, dataset, path, value, value_iri, datatype, lang)."""
    seen: set[str] = set()

    def walk(n: Node, path: list[str]):
        if n.id in seen:
            return
        seen.add(n.id)
        for t in n.types:
            if path:
                yield path + ["type"], t, "", "", ""
        for p, vals in n.props.items():
            for v in vals:
                here = path + [local(p)]
                if isinstance(v, Lit):
                    yield here, v.value, "", v.datatype or "", v.lang or ""
                elif isinstance(v, Ref):
                    yield here, v.label or "", v.iri, "", ""
                else:
                    yield from walk(v, here)

    for path, val, iri, dt, lang in walk(root, []):
        yield [r["iri"], r["class"], r["title"] or r["graph_iri"], ".".join(path), val, iri, dt, lang]


LONG_HEADER = ["IRI", "Class", "Dataset", "Property path", "Value", "Value IRI", "Datatype", "Language"]


def write_csv(job: Job, out, total: int) -> None:
    text = io.TextIOWrapper(out, encoding="utf-8-sig", newline="", write_through=True)
    w = csv.writer(text)
    if job.req.shape == "long":
        w.writerow(LONG_HEADER)
        for batch in job.rows():
            nodes = job.resolve(batch)
            for r in batch:
                w.writerows(_long_rows(nodes[r["iri"]], r, job.req.values))
            job.done += len(batch)
            job.progress()
    else:
        prof = job.profile()
        cols = union_columns(job, prof)
        w.writerow([h for _, h in BASE_COLUMNS] + [header_text(c, job.req.headers) for c in cols])
        for batch in job.rows():
            nodes = job.resolve(batch)
            for r in batch:
                w.writerow(_row(job, r, nodes[r["iri"]], cols))
            job.done += len(batch)
            job.progress()
    text.flush()
    text.detach()


def write_xlsx(job: Job, path: Path, total: int) -> None:
    import xlsxwriter
    wb = xlsxwriter.Workbook(str(path), {"constant_memory": True, "strings_to_urls": False})
    try:
        wrap = wb.add_format({"bold": True})
        about = wb.add_worksheet("Export")
        info = [("RINF Atlas export", ""), ("Environment", job.req.env), ("Exported (UTC)", f"{datetime.now(UTC):%Y-%m-%d %H:%M}"),
                ("Profile", job.req.profile or ("-" if job.req.shape == "long" else "full")),
                ("Entities", str(total)), ("Query state", json.dumps(job.req.state, ensure_ascii=False))]
        for i, (k, v) in enumerate(info):
            about.write(i, 0, k, wrap)
            about.write(i, 1, v)
        sheets: dict[str, tuple] = {}
        used = {"export"}
        prof = None if job.req.shape == "long" else job.profile()

        def sheet(r: dict):
            key = r["class"]
            if key not in sheets:
                name = (job.class_defs().get(key, {}).get("label") or key)
                name = "".join(c for c in name if c not in '[]:*?/\\')[:31] or key[:31]
                n, base = name, name
                i = 2
                while n.lower() in used:
                    n = f"{base[:28]}_{i}"
                    i += 1
                used.add(n.lower())
                ws = wb.add_worksheet(n)
                if prof is None:
                    cols: list[Column] = []
                    ws.write_row(0, 0, LONG_HEADER, wrap)
                else:
                    cols = job.columns(prof, r["class_iri"])
                    ws.write_row(0, 0, [h for _, h in BASE_COLUMNS] + [header_text(c, job.req.headers) for c in cols], wrap)
                sheets[key] = (ws, cols, [1])
            return sheets[key]

        for batch in job.rows():
            nodes = job.resolve(batch)
            for r in batch:
                ws, cols, rowno = sheet(r)
                rows = _long_rows(nodes[r["iri"]], r, job.req.values) if prof is None else [_row(job, r, nodes[r["iri"]], cols)]
                for cells in rows:
                    if rowno[0] > XLSX_MAX_ROWS:
                        break
                    ws.write_row(rowno[0], 0, [("" if isinstance(c, str) and len(c) > XLSX_MAX_CELL else c) for c in cells])
                    rowno[0] += 1
            job.done += len(batch)
            job.progress()
    finally:
        wb.close()


def write_geojson(job: Job, out, total: int) -> None:
    prof = job.profile()
    out.write(b'{"type": "FeatureCollection", "features": [\n')
    first = True
    for batch in job.rows():
        nodes = job.resolve(batch)
        for r in batch:
            cols = job.columns(prof, r["class_iri"])
            props = dict(zip([k for k, _ in BASE_COLUMNS if k not in ("lon", "lat", "wkt")],
                             _base_cells(r, job.req.values)[:5], strict=True))
            for c in cols:
                v = cell(nodes[r["iri"]], c, job.req.values)
                if v:
                    props[c.key] = v
            feat = {"type": "Feature", "id": r["iri"], "geometry": json.loads(r["geojson"]) if r["geojson"] else None,
                    "properties": props}
            out.write(("" if first else ",\n").encode() + json.dumps(feat, ensure_ascii=False).encode("utf-8"))
            first = False
        job.done += len(batch)
        job.progress()
    out.write(b"\n]}\n")


def _extent(job: Job, class_key: str) -> tuple | None:
    with job.conn.cursor() as cur:
        cur.execute(f"""SELECT ST_XMin(e), ST_YMin(e), ST_XMax(e), ST_YMax(e) FROM
                        (SELECT ST_Extent(f.geom) AS e FROM feature f WHERE {job.req.sql} AND f.class = %s) x""",
                    [*job.req.params, class_key])
        row = cur.fetchone()
    return tuple(row) if row and row[0] is not None else None


def _gpkg_blob(wkb: bytes | None) -> bytes | None:
    if not wkb:
        return None
    # GeoPackage binary header: magic, version 0, flags (little endian, no envelope), srs_id 4326.
    return b"GP\x00\x01" + struct.pack("<i", 4326) + bytes(wkb)


def write_gpkg(job: Job, path: Path, total: int) -> None:
    prof = job.profile()
    con = sqlite3.connect(str(path))
    try:
        cur = con.cursor()
        cur.executescript("""
            PRAGMA application_id = 0x47504B47; PRAGMA user_version = 10300;
            CREATE TABLE gpkg_spatial_ref_sys (srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY, organization TEXT NOT NULL,
                organization_coordsys_id INTEGER NOT NULL, definition TEXT NOT NULL, description TEXT);
            INSERT INTO gpkg_spatial_ref_sys VALUES ('Undefined cartesian', -1, 'NONE', -1, 'undefined', NULL),
                ('Undefined geographic', 0, 'NONE', 0, 'undefined', NULL),
                ('WGS 84', 4326, 'EPSG', 4326, 'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","7030"]],AUTHORITY["EPSG","6326"]],PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4326"]]', 'WGS 84');
            CREATE TABLE gpkg_contents (table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL, identifier TEXT UNIQUE,
                description TEXT DEFAULT '', last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER,
                CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id));
            CREATE TABLE gpkg_geometry_columns (table_name TEXT NOT NULL, column_name TEXT NOT NULL, geometry_type_name TEXT NOT NULL,
                srs_id INTEGER NOT NULL, z TINYINT NOT NULL, m TINYINT NOT NULL, CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name));
        """)
        tables: dict[str, tuple[str, list[Column], list[str]]] = {}

        def table(r: dict):
            key = r["class"]
            if key not in tables:
                name = "".join(c if c.isalnum() else "_" for c in key)
                cols = job.columns(prof, r["class_iri"])
                names = []
                used: set[str] = set()
                for c in cols:
                    n = "".join(ch if ch.isalnum() else "_" for ch in c.key)[:60]
                    while n.lower() in used or n.lower() in ("fid", "geom", "iri", "label", "class", "dataset", "provider"):
                        n += "_"
                    used.add(n.lower())
                    names.append(n)
                defs = ", ".join(f'"{n}" TEXT' for n in names)
                cur.execute(f'CREATE TABLE "{name}" (fid INTEGER PRIMARY KEY AUTOINCREMENT, geom GEOMETRY, iri TEXT, label TEXT, '
                            f'class TEXT, dataset TEXT, provider TEXT{", " + defs if defs else ""})')
                cur.execute("INSERT INTO gpkg_contents (table_name, data_type, identifier, description, srs_id) VALUES (?, 'features', ?, ?, 4326)",
                            (name, job.class_defs().get(key, {}).get("label") or key, f"RINF Atlas export ({job.req.env})"))
                cur.execute("INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', 'GEOMETRY', 4326, 0, 0)", (name,))
                tables[key] = (name, cols, names)
            return tables[key]

        for batch in job.rows():
            nodes = job.resolve(batch)
            for r in batch:
                name, cols, names = table(r)
                vals = [_gpkg_blob(r["wkb"]), r["iri"], r["label"], r["class"], r["title"] or r["graph_iri"], r["provider"]]
                vals += [cell(nodes[r["iri"]], c, job.req.values) or None for c in cols]
                extra_cols = "".join(f', "{n}"' for n in names)
                cur.execute(f'INSERT INTO "{name}" (geom, iri, label, class, dataset, provider{extra_cols}) '
                            f"VALUES ({', '.join('?' * len(vals))})", vals)
            con.commit()
            job.done += len(batch)
            job.progress()
        for key, (name, _, _) in tables.items():
            ext = _extent(job, key)
            if ext:
                cur.execute("UPDATE gpkg_contents SET min_x=?, min_y=?, max_x=?, max_y=? WHERE table_name=?", (*ext, name))
        con.commit()
    finally:
        con.close()


# --- running ----------------------------------------------------------------------------

def run_export(cfg: Settings, conn: psycopg.Connection, row: dict, profiles: list[Profile]) -> None:
    """Runs one export row to completion (status done / failed / cancelled)."""
    job = Job(cfg, conn, row, profiles)
    req = job.req
    ext, _ = FORMATS[req.format]
    cfg.export_dir.mkdir(parents=True, exist_ok=True)
    gz = req.gzip and req.format not in ("xlsx", "gpkg")
    final = cfg.export_dir / f"{job.id}.{ext}{'.gz' if gz else ''}"
    tmp = cfg.export_dir / f"{job.id}.part"
    try:
        job.total = job.count()
        datasets_before = {d["graph"]: d["fingerprint"] for d in _all_datasets(conn, req.env)}
        with conn.cursor() as cur:
            cur.execute("UPDATE export SET status='running', started_at=now(), total=%s WHERE id=%s", (job.total, job.id))
        conn.commit()
        if req.format == "xlsx":
            write_xlsx(job, tmp, job.total)
        elif req.format == "gpkg":
            write_gpkg(job, tmp, job.total)
        else:
            raw = open(tmp, "wb")  # noqa: SIM115 - closed below, gzip wraps it
            out = gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) if gz else raw
            try:
                if req.format in RDF_FORMATS:
                    write_rdf(job, out, job.total)
                elif req.format == "csv":
                    write_csv(job, out, job.total)
                else:
                    write_geojson(job, out, job.total)
            finally:
                out.close()
                if out is not raw:
                    raw.close()
        os.replace(tmp, final)
        datasets = job.datasets()
        changed = [d["graph"] for d in datasets if datasets_before.get(d["graph"]) != d["fingerprint"] or d["status"] != "loaded"]
        warning = (f"{len(changed)} dataset(s) were updated while this export ran; their elements may mix old and new "
                   f"data: {', '.join(changed)}" if changed else None)
        stamp = datetime.now(UTC)
        name = f"rinf-atlas-{req.env}-{req.profile or req.format}-{stamp:%Y%m%d-%H%M}.{ext}{'.gz' if gz else ''}"
        with conn.cursor() as cur:
            cur.execute("""UPDATE export SET status='done', done=%s, total=%s, bytes=%s, filename=%s, path=%s, warning=%s,
                           datasets=%s, finished_at=now(), expires_at=now() + %s * interval '1 hour' WHERE id=%s""",
                        (job.total, job.total, final.stat().st_size, name, final.name, warning, Jsonb(datasets),
                         cfg.export_retention_hours, job.id))
        conn.commit()
        log.info("export %s: %s %s, %d entities, %d bytes in %.0fs", job.id, req.env, req.format, job.total,
                 final.stat().st_size, time.time() - job.started)
    except Cancelled:
        conn.rollback()
        tmp.unlink(missing_ok=True)
        with conn.cursor() as cur:
            cur.execute("UPDATE export SET finished_at=now() WHERE id=%s", (job.id,))
        conn.commit()
        log.info("export %s cancelled", job.id)
    except Exception as e:  # noqa: BLE001 - reported on the row
        conn.rollback()
        tmp.unlink(missing_ok=True)
        log.error("export %s failed: %s\n%s", job.id, e, traceback.format_exc())
        with conn.cursor() as cur:
            cur.execute("UPDATE export SET status='failed', error=%s, finished_at=now() WHERE id=%s", (str(e)[:2000], job.id))
        conn.commit()


def _all_datasets(conn, env: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT graph_iri, fingerprint FROM dataset WHERE env=%s AND kind='provider'", (env,))
        return [{"graph": r[0], "fingerprint": r[1]} for r in cur.fetchall()]


def publish_profiles(conn: psycopg.Connection, profiles: list[Profile]) -> None:
    """Write the compiled export profiles to export_def for the API and UI."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM export_def")
        for p in profiles:
            cur.execute("INSERT INTO export_def (key, def) VALUES (%s, %s)", (p.key, Jsonb(p.as_dict())))
    conn.commit()


def claim(conn: psycopg.Connection, n: int) -> list[dict]:
    """Takes up to n queued exports (SKIP LOCKED, so several workers could share the table)."""
    if n <= 0:
        return []
    with conn.cursor() as cur:
        cur.execute("""UPDATE export SET status='claimed', started_at=now() WHERE id IN (
                           SELECT id FROM export WHERE status='queued' ORDER BY created_at
                           FOR UPDATE SKIP LOCKED LIMIT %s)
                       RETURNING id, request""", (n,))
        rows = [{"id": r[0], "request": r[1]} for r in cur.fetchall()]
    conn.commit()
    return rows


def sweep(conn: psycopg.Connection, cfg: Settings) -> int:
    """Expires done exports past their retention, deletes the files of expired, failed and
    cancelled rows (the API cannot: it mounts the folder read-only) and forgets old rows."""
    n = 0
    with conn.cursor() as cur:
        cur.execute("UPDATE export SET status='expired' WHERE status='done' AND expires_at < now()")
        cur.execute("""SELECT id, path FROM export WHERE status IN ('expired','failed','cancelled') AND path IS NOT NULL
                       FOR UPDATE SKIP LOCKED""")
        rows = cur.fetchall()
        for eid, path in rows:
            (cfg.export_dir / path).unlink(missing_ok=True)
            n += 1
        if rows:
            cur.execute("UPDATE export SET path=NULL WHERE id = ANY(%s)", ([r[0] for r in rows],))
        cur.execute("""DELETE FROM export WHERE status IN ('failed','cancelled','expired') AND path IS NULL
                       AND coalesce(finished_at, created_at) < now() - interval '7 days'""")
    conn.commit()
    for f in cfg.export_dir.glob("*.part"):
        if time.time() - f.stat().st_mtime > 86400:
            f.unlink(missing_ok=True)
    return n


class ExportService:
    """Background thread: claims queued exports and runs them in a small pool."""

    def __init__(self, cfg: Settings, workers: int | None = None):
        self.cfg = cfg
        self.workers = workers or cfg.export_workers
        self.pool = ThreadPoolExecutor(max_workers=max(1, self.workers), thread_name_prefix="export")
        self.running = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="export-dispatch", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.pool.shutdown(wait=True, cancel_futures=True)

    def _loop(self) -> None:
        conn = None
        last_sweep = 0.0
        while not self._stop.is_set():
            try:
                if conn is None or conn.closed:
                    conn = db.connect(self.cfg.database_url)
                with self._lock:
                    free = self.workers - self.running
                for row in claim(conn, free):
                    with self._lock:
                        self.running += 1
                    self.pool.submit(self._run, row)
                if time.time() - last_sweep > 60:
                    sweep(conn, self.cfg)
                    last_sweep = time.time()
            except Exception:
                log.exception("export dispatcher failed; retrying")
                try:
                    if conn is not None:
                        conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = None
            self._stop.wait(2)

    def _run(self, row: dict) -> None:
        try:
            conn = db.connect(self.cfg.database_url)
            try:
                run_export(self.cfg, conn, row, load_profiles(self.cfg.shapes_dir))
            finally:
                conn.close()
        except Exception:
            log.exception("export %s crashed", row.get("id"))
        finally:
            with self._lock:
                self.running -= 1


def run_queued(cfg: Settings, conn: psycopg.Connection, limit: int = 100) -> int:
    """Runs queued exports in this process (no poll loop needed; used by `atlas export-jobs`)."""
    n = 0
    profiles = load_profiles(cfg.shapes_dir)
    while n < limit:
        rows = claim(conn, 1)
        if not rows:
            break
        run_export(cfg, conn, rows[0], profiles)
        n += 1
    sweep(conn, cfg)
    return n


