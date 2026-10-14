"""Flatten one named graph of the mirror into `feature` rows, driven by shapes."""
from __future__ import annotations

import json
import logging
import re
import time
from collections import defaultdict

import psycopg
import pyoxigraph as ox

from .mirror import select
from .shapes import ClassShape, Facet, ShapeSet

log = logging.getLogger(__name__)

SKOS_PREF = "http://www.w3.org/2004/02/skos/core#prefLabel"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"

_CRS = re.compile(r"^\s*<([^>]*)>\s*")
# Some providers append the validity period to rdfs:label; it is noise in lists.
_VALIDITY = re.compile(r"\s*\(from \d{4}-\d{2}-\d{2} until \d{4}-\d{2}-\d{2}\)\s*$")
_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
# Explicit plus signs on coordinates ("POINT (+23.1 54.7)") are valid WKT for
# some providers but PostGIS rejects them, which silently drops the geometry.
# Web Mercator copy used by the tile query; set together with geom so a graph never
# drops off the map while a sync or reindex is still running.
_GEOM3857 = """CASE WHEN {g} IS NOT NULL AND ST_YMax({g}) < 85 AND ST_YMin({g}) > -85
    AND ST_XMax({g}) <= 180 AND ST_XMin({g}) >= -180 THEN ST_Transform({g}, 3857) END"""
_PLUS = re.compile(r"(?<![\deE])\+(?=[\d.])")


def normalize_wkt(wkt: str) -> str | None:
    """Strip a GeoSPARQL CRS prefix and return lon/lat WKT.

    GeoSPARQL says WKT without CRS is CRS84 (lon/lat) and EPSG:4326 is lat/lon;
    providers mix both up, so the axis order is also checked against Europe's
    extent (latitude is larger than longitude almost everywhere in RINF).
    """
    wkt = wkt.strip()
    crs = None
    m = _CRS.match(wkt)
    if m:
        crs = m.group(1)
        wkt = wkt[m.end():]
    wkt = _PLUS.sub("", wkt)
    if not wkt:
        return None
    nums = _NUM.findall(wkt)
    if len(nums) < 2:
        return None
    x, y = float(nums[0]), float(nums[1])
    looks_latlon = 34 <= x <= 72 and -25 <= y <= 45 and x > y
    if crs and "4326" in crs and "CRS84" not in crs:
        flip = looks_latlon or not (34 <= y <= 72)
    else:
        flip = looks_latlon
    if flip:
        wkt = _flip(wkt)
    return wkt


def _flip(wkt: str) -> str:
    def swap(m: re.Match) -> str:
        parts = m.group(0).split()
        if len(parts) >= 2:
            parts[0], parts[1] = parts[1], parts[0]
        return " ".join(parts)
    return re.sub(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?:\s+-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)+", swap, wkt)


def _val(term, kind: str):
    if kind == "link":  # IRI of another indexed feature
        return term.value if isinstance(term, ox.NamedNode) else None
    if kind == "terms":
        if isinstance(term, ox.NamedNode):
            return term.value
        if isinstance(term, ox.Literal):
            return term.value
        return None
    if not isinstance(term, ox.Literal):
        return None
    v = term.value.strip()
    if kind == "boolean":
        if v.lower() in ("true", "1"):
            return True
        if v.lower() in ("false", "0"):
            return False
        return None
    if kind == "range":
        try:
            f = float(v)
        except ValueError:
            return None
        if f != f or f in (float("inf"), float("-inf")):
            return None
        return int(f) if f.is_integer() and abs(f) < 2**53 else f
    return v  # text


class Extractor:
    def __init__(self, store: ox.Store, graph: str):
        self.store = store
        self.graph = graph
        self.dg = [ox.NamedNode(graph)]

    def q(self, sparql: str):
        res = self.store.query(sparql, default_graph=self.dg)
        names = [v.value for v in res.variables]
        for sol in res:
            yield {n: sol[n] for n in names}

    def subjects(self, cls: ClassShape, narrower: list[str]) -> list[str]:
        # Instances also typed with a more specific shaped class are indexed there only.
        excl = ""
        if narrower:
            excl = "FILTER NOT EXISTS { ?s a ?n . VALUES ?n { " + " ".join(f"<{n}>" for n in narrower) + " } }"
        return [r["s"].value for r in self.q(f"SELECT DISTINCT ?s WHERE {{ ?s a <{cls.class_iri}> {excl} }}")
                if isinstance(r["s"], ox.NamedNode)]

    def pairs(self, cls: ClassShape, path: str):
        for r in self.q(f"SELECT ?s ?v WHERE {{ ?s a <{cls.class_iri}> . ?s {path} ?v }}"):
            if isinstance(r["s"], ox.NamedNode):
                yield r["s"].value, r["v"]

    def simple_batch(self, cls: ClassShape, preds: list[str]):
        vals = " ".join(f"<{p}>" for p in preds)
        for r in self.q(f"SELECT ?s ?p ?v WHERE {{ ?s a <{cls.class_iri}> . VALUES ?p {{ {vals} }} ?s ?p ?v }}"):
            if isinstance(r["s"], ox.NamedNode):
                yield r["s"].value, r["p"].value, r["v"]


def extract_graph(conn: psycopg.Connection, store: ox.Store, env: str, graph: str, shapes: ShapeSet) -> dict:
    """Replace all features of (env, graph). Commits. Returns per-class counts."""
    t0 = time.time()
    ex = Extractor(store, graph)
    counts: dict[str, int] = {}
    rows = []
    links = []
    iris_for_labels: set[str] = set()

    shaped = {c.class_iri for c in shapes.classes}
    narrower: dict[str, list[str]] = defaultdict(list)
    for r in select(store, "SELECT ?d ?c WHERE { ?d <http://www.w3.org/2000/01/rdf-schema#subClassOf>+ ?c }"
                    " VALUES ?c { " + " ".join(f"<{c}>" for c in shaped) + " }"):
        if r["d"].value in shaped and r["d"].value != r["c"].value:
            narrower[r["c"].value].append(r["d"].value)

    for cls in shapes.classes:
        subs = ex.subjects(cls, narrower.get(cls.class_iri, []))
        if not subs:
            continue
        subset = set(subs)
        facets = shapes.all_facets(cls)
        values: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        texts: dict[str, list[str]] = defaultdict(list)

        def add(s: str, f: Facet, term):
            v = _val(term, f.kind)
            if v is None or s not in subset:
                return
            if f.kind == "text":
                if len(v) <= 200:
                    texts[s].append(v)
                return
            lst = values[s][f.key]
            if v not in lst:
                lst.append(v)
            if f.kind == "terms" and isinstance(term, ox.NamedNode):
                iris_for_labels.add(v)

        simple = [f for f in facets if f.simple]
        by_pred: dict[str, list[Facet]] = defaultdict(list)
        for f in simple:
            by_pred[f.simple].append(f)
        preds = list(by_pred)
        for i in range(0, len(preds), 40):
            for s, p, term in ex.simple_batch(cls, preds[i:i + 40]):
                for f in by_pred[p]:
                    add(s, f, term)
        for f in facets:
            if not f.simple:
                for s, term in ex.pairs(cls, f.path):
                    add(s, f, term)

        labels: dict[str, str] = {}
        for lp in cls.label_paths:
            best: dict[str, tuple[int, str]] = {}
            for s, term in ex.pairs(cls, lp):
                if s in labels or not isinstance(term, ox.Literal):
                    continue
                rank = 0 if term.language in (None, "en") else 1
                if s not in best or rank < best[s][0]:
                    best[s] = (rank, term.value)
            for s, (_, v) in best.items():
                labels[s] = v

        geoms: dict[str, list[str]] = defaultdict(list)
        for gp in cls.geometry_paths:
            found: dict[str, list[str]] = defaultdict(list)
            for s, term in ex.pairs(cls, gp):
                if s in geoms or not isinstance(term, ox.Literal):
                    continue
                w = normalize_wkt(term.value)
                if w and w not in found[s]:
                    found[s].append(w)
            for s, ws in found.items():
                geoms[s] = ws

        if cls.line_between:
            for rule, path in (("lineA", cls.line_between[0]), ("lineB", cls.line_between[1])):
                for s, term in ex.pairs(cls, path):
                    if s not in geoms and isinstance(term, ox.NamedNode):
                        links.append((env, graph, s, cls.key, rule, term.value))
        for path in cls.geometry_from:
            for s, term in ex.pairs(cls, path):
                if s not in geoms and isinstance(term, ox.NamedNode):
                    links.append((env, graph, s, cls.key, "from", term.value))

        for s in subs:
            ws = geoms.get(s)
            wkt = ws[0] if ws and len(ws) == 1 else (f"GEOMETRYCOLLECTION({', '.join(ws)})" if ws else None)
            label = _VALIDITY.sub("", labels.get(s) or "") or s.rstrip("/").rsplit("/", 1)[-1]
            search_text = " ".join([label] + texts.get(s, []))
            rows.append((env, s, cls.key, cls.class_iri, graph, label, wkt,
                         json.dumps(values.get(s, {}), ensure_ascii=False), search_text))
        counts[cls.key] = len(subs)

    with conn.cursor() as cur:
        cur.execute("DELETE FROM feature WHERE env=%s AND graph_iri=%s", (env, graph))
        cur.execute("DELETE FROM feature_link WHERE env=%s AND graph_iri=%s", (env, graph))
        cur.execute("""CREATE TEMP TABLE IF NOT EXISTS stage_feature (
            env text, iri text, class text, class_iri text, graph_iri text, label text,
            wkt text, facets jsonb, search_text text) ON COMMIT DELETE ROWS""")
        with cur.copy("COPY stage_feature FROM STDIN") as cp:
            for r in rows:
                cp.write_row(r)
        cur.execute(f"""
            INSERT INTO feature (env, iri, class, class_iri, graph_iri, label, geom, geom3857, geom_source, facets, search)
            SELECT env, iri, class, class_iri, graph_iri, label, g, {_GEOM3857.format(g="g")},
                   CASE WHEN g IS NOT NULL THEN 'path' END,
                   facets, to_tsvector('simple', unaccent(coalesce(search_text, '')))
            FROM (SELECT *, ST_CollectionHomogenize(atlas_geom(wkt)) AS g FROM stage_feature) s
            ON CONFLICT (env, iri, class) DO UPDATE SET
                graph_iri = EXCLUDED.graph_iri, label = EXCLUDED.label, geom = EXCLUDED.geom,
                geom_source = EXCLUDED.geom_source, facets = EXCLUDED.facets, search = EXCLUDED.search,
                geom3857 = EXCLUDED.geom3857
        """)
        if links:
            with cur.copy("COPY feature_link (env, graph_iri, iri, class, rule, target) FROM STDIN") as cp:
                for link in links:
                    cp.write_row(link)
    conn.commit()
    update_term_labels(conn, store, env, iris_for_labels, graph)
    log.info("%s %s: %d features (%s) in %.1fs", env, graph, len(rows),
             ", ".join(f"{k}={v}" for k, v in counts.items()), time.time() - t0)
    return counts


def update_term_labels(conn: psycopg.Connection, store: ox.Store, env: str, iris: set[str],
                       graph: str | None = None, only_missing: bool = True) -> int:
    """Look up skos:prefLabel / rdfs:label for IRI facet values in the mirror."""
    if not iris:
        return 0
    todo = sorted(iris)
    if only_missing:
        with conn.cursor() as cur:
            cur.execute("SELECT iri FROM term WHERE env=%s AND iri = ANY(%s)", (env, todo))
            have = {r[0] for r in cur.fetchall()}
        todo = [i for i in todo if i not in have]
    found: dict[str, tuple[int, str]] = {}
    for i in range(0, len(todo), 300):
        chunk = " ".join(f"<{x}>" for x in todo[i:i + 300] if ">" not in x and " " not in x)
        if not chunk:
            continue
        q = f"""SELECT ?i ?p ?l WHERE {{ VALUES ?i {{ {chunk} }}
                VALUES ?p {{ <{SKOS_PREF}> <{RDFS_LABEL}> }} ?i ?p ?l }}"""
        for r in select(store, q, union=True):
            lit = r["l"]
            if not isinstance(lit, ox.Literal):
                continue
            rank = (0 if r["p"].value == SKOS_PREF else 2) + (0 if lit.language in ("en", None) else 1)
            iri = r["i"].value
            if iri not in found or rank < found[iri][0]:
                found[iri] = (rank, lit.value)
    if found:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO term (env, iri, label) VALUES (%s,%s,%s) ON CONFLICT (env, iri) DO UPDATE SET label=EXCLUDED.label",
                [(env, k, v[1]) for k, v in found.items()])
        conn.commit()
    return len(found)


def derive_geometry(conn: psycopg.Connection, env: str, max_rounds: int = 6) -> int:
    """Fill geometry for features without one from linked features, repeating
    so chains (platform -> track -> section of line -> operational points) resolve."""
    total = 0
    with conn.cursor() as cur:
        for _ in range(max_rounds):
            cur.execute(f"""
                WITH a AS (
                    SELECT DISTINCT ON (f.id) f.id, ST_PointOnSurface(t.geom) AS p
                    FROM feature f
                    JOIN feature_link l ON l.env=f.env AND l.iri=f.iri AND l.class=f.class AND l.rule='lineA'
                    JOIN feature t ON t.env=l.env AND t.iri=l.target AND t.geom IS NOT NULL
                    WHERE f.env=%(env)s AND f.geom IS NULL ORDER BY f.id),
                b AS (
                    SELECT DISTINCT ON (f.id) f.id, ST_PointOnSurface(t.geom) AS p
                    FROM feature f
                    JOIN feature_link l ON l.env=f.env AND l.iri=f.iri AND l.class=f.class AND l.rule='lineB'
                    JOIN feature t ON t.env=l.env AND t.iri=l.target AND t.geom IS NOT NULL
                    WHERE f.env=%(env)s AND f.geom IS NULL ORDER BY f.id)
                UPDATE feature f SET geom = ST_MakeLine(a.p, b.p), geom_source = 'line',
                       geom3857 = {_GEOM3857.format(g="ST_MakeLine(a.p, b.p)")}
                FROM a JOIN b USING (id)
                WHERE f.id = a.id AND NOT ST_Equals(a.p, b.p)
            """, {"env": env})
            n1 = cur.rowcount
            cur.execute(f"""
                WITH src AS (
                    SELECT f.id,
                           CASE WHEN bool_or(ST_Dimension(t.geom) >= 1)
                                THEN ST_CollectionExtract(ST_Collect(t.geom), 2)
                                ELSE ST_CollectionExtract(ST_Collect(t.geom), 1) END AS g
                    FROM feature f
                    JOIN feature_link l ON l.env=f.env AND l.iri=f.iri AND l.class=f.class AND l.rule='from'
                    JOIN feature t ON t.env=l.env AND t.iri=l.target AND t.geom IS NOT NULL
                    WHERE f.env=%(env)s AND f.geom IS NULL
                    GROUP BY f.id)
                UPDATE feature f SET geom = ST_CollectionHomogenize(src.g), geom_source = 'from',
                       geom3857 = {_GEOM3857.format(g="ST_CollectionHomogenize(src.g)")}
                FROM src WHERE f.id = src.id AND src.g IS NOT NULL AND NOT ST_IsEmpty(src.g)
            """, {"env": env})
            n2 = cur.rowcount
            conn.commit()
            total += n1 + n2
            if n1 + n2 == 0:
                break
        cur.execute("""
            UPDATE feature SET geom3857 = ST_Transform(geom, 3857)
            WHERE env=%s AND geom IS NOT NULL AND geom3857 IS NULL
              AND ST_YMax(geom) < 85 AND ST_YMin(geom) > -85
              AND ST_XMax(geom) <= 180 AND ST_XMin(geom) >= -180
        """, (env,))
    conn.commit()
    return total


def refresh_facet_cache(conn: psycopg.Connection, env: str) -> None:
    t0 = time.time()
    with conn.cursor() as cur:
        cur.execute("DELETE FROM facet_cache WHERE env=%s", (env,))
        cur.execute("""
            INSERT INTO facet_cache (env, class, key, value, n)
            SELECT f.env, f.class, kv.key, v.value, count(*)
            FROM feature f, jsonb_each(f.facets) kv, jsonb_array_elements(kv.value) v
            WHERE f.env = %s
              -- link facets (feature IRIs) are never counted as filter values
              AND NOT EXISTS (SELECT 1 FROM facet_def d WHERE d.key = kv.key AND d.kind = 'link')
            GROUP BY 1, 2, 3, 4
        """, (env,))
        cur.execute("""INSERT INTO index_state (env) VALUES (%s) ON CONFLICT (env)
                       DO UPDATE SET updated_at = now()""", (env,))
    conn.commit()
    log.info("%s facet cache refreshed in %.1fs", env, time.time() - t0)
