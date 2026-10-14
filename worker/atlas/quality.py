"""Data quality layer: validate provider graphs against ERA's SHACL shapes.

ERA publishes the shapes it validates RINF uploads with in the named graph
`quality.shapes_graph` (config/environments.yaml). The worker mirrors that
graph next to the vocabularies, validates every new or changed provider graph
with `atlas.shacl` (on the mirror, no ERA query) and stores in PostGIS:

* `violation`: one row per result (capped), shown in a feature's details;
* `quality_count` and the `dataset.qa_*` columns: complete counts per dataset;
* `quality_report`: the standard sh:ValidationReport as Turtle, plus the
  self-contained HTML viewer of shacl-report (https://github.com/Matdata-eu/
  shacl-report) when its CLI is installed in the worker image;
* `quality_shapes`: the shapes graph, for download next to the reports;
* `feature.facets.quality`: the severities found on each feature, which the
  API exposes as an ordinary shared facet and as the `qa` tile property.
"""
from __future__ import annotations

import gzip
import hashlib
import logging
import re
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg
import pyoxigraph as ox

from . import shacl
from .config import Env, Settings

log = logging.getLogger(__name__)

FACET = "quality"
NO_ISSUES = "https://rinf-atlas.matdata.eu/ns#NoIssues"
SEVERITY_IRI = shacl.SEVERITY_IRI
TERM_LABELS = {SEVERITY_IRI[3]: "Violation", SEVERITY_IRI[2]: "Warning", SEVERITY_IRI[1]: "Info",
               NO_ISSUES: "No issues found"}
REPORT_BASE = "https://rinf-atlas.matdata.eu/report/"


def shapes_fingerprint(store: ox.Store, graph: str) -> str | None:
    """Content hash of the mirrored shapes graph (None when it is not mirrored)."""
    g = ox.NamedNode(graph)
    if not store.contains_named_graph(g):
        return None
    lines = sorted(f"{q.subject} {q.predicate} {q.object}" for q in store.quads_for_pattern(None, None, None, g))
    if not lines:
        return None
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


_SKOLEM_NT = re.compile(r"<urn:atlas:bnode:([0-9a-f]+):([^>]+)>")


def shapes_turtle(store: ox.Store, graph: str) -> bytes:
    """The shapes graph as Turtle, with the mirror's skolem IRIs turned back into blank nodes."""
    nt_text = store.dump(format=ox.RdfFormat.N_TRIPLES, from_graph=ox.NamedNode(graph)).decode()
    nt_text = _SKOLEM_NT.sub(lambda m: f"_:b{m.group(1)}{re.sub(r'[^A-Za-z0-9]', '', m.group(2))}", nt_text)
    triples = list(ox.parse(nt_text.encode(), format=ox.RdfFormat.N_TRIPLES))
    return ox.serialize(triples, format=ox.RdfFormat.TURTLE, prefixes={
        "sh": shacl.SH, "rdf": shacl.RDF, "rdfs": shacl.RDFS, "xsd": shacl.XSD, "era": "http://data.europa.eu/949/",
        "shapes": "http://data.europa.eu/949/shapes/", "skos": "http://www.w3.org/2004/02/skos/core#",
        "dct": "http://purl.org/dc/terms/", "geo": "http://www.opengis.net/ont/geosparql#"})


def publish_shapes(conn: psycopg.Connection, store: ox.Store, env: str, graph: str, fingerprint: str) -> None:
    """Keep a downloadable copy of the shapes graph (only rewritten when it changed)."""
    with conn.cursor() as cur:
        cur.execute("SELECT fingerprint FROM quality_shapes WHERE env=%s", (env,))
        row = cur.fetchone()
    if row and row[0] == fingerprint:
        return
    ttl = shapes_turtle(store, graph)
    n = sum(1 for _ in store.quads_for_pattern(None, None, None, ox.NamedNode(graph)))
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO quality_shapes (env, graph_iri, fingerprint, triples, turtle_gz, loaded_at)
                       VALUES (%s,%s,%s,%s,%s,now()) ON CONFLICT (env) DO UPDATE SET graph_iri=EXCLUDED.graph_iri,
                       fingerprint=EXCLUDED.fingerprint, triples=EXCLUDED.triples, turtle_gz=EXCLUDED.turtle_gz,
                       loaded_at=now()""", (env, graph, fingerprint, n, gzip.compress(ttl)))
        cur.executemany("""INSERT INTO term (env, iri, label) VALUES (%s,%s,%s)
                           ON CONFLICT (env, iri) DO UPDATE SET label=EXCLUDED.label""",
                        [(env, k, v) for k, v in TERM_LABELS.items()])
    conn.commit()


def reset(conn: psycopg.Connection, env: str, graph: str) -> None:
    """Forget the validation of a graph whose data changed (it is validated again)."""
    with conn.cursor() as cur:
        cur.execute("UPDATE dataset SET qa_shapes=NULL WHERE env=%s AND graph_iri=%s", (env, graph))
    conn.commit()


def remove(conn: psycopg.Connection, env: str, graph: str) -> None:
    with conn.cursor() as cur:
        for t in ("violation", "quality_count", "quality_report"):
            cur.execute(f"DELETE FROM {t} WHERE env=%s AND graph_iri=%s", (env, graph))
    conn.commit()


def apply_flags(conn: psycopg.Connection, env: str, graph: str) -> None:
    """Write the severities found on each feature of a graph to its `quality` facet."""
    with conn.cursor() as cur:
        cur.execute("SELECT qa_status FROM dataset WHERE env=%s AND graph_iri=%s", (env, graph))
        row = cur.fetchone()
        if not row or row[0] not in ("ok", "partial"):
            cur.execute(f"""UPDATE feature SET facets = facets - '{FACET}'
                            WHERE env=%s AND graph_iri=%s AND facets ? '{FACET}'""", (env, graph))
        else:
            cur.execute(f"""
                WITH agg AS (
                    SELECT focus, jsonb_agg(CASE severity WHEN 3 THEN %(v)s WHEN 2 THEN %(w)s ELSE %(i)s END
                                            ORDER BY severity DESC) AS vals
                    FROM (SELECT DISTINCT focus, severity FROM violation
                          WHERE env=%(env)s AND graph_iri=%(g)s) d
                    GROUP BY focus)
                UPDATE feature f SET facets = jsonb_set(f.facets, '{{{FACET}}}',
                                                        coalesce(agg.vals, jsonb_build_array(%(ok)s::text)))
                FROM feature f2 LEFT JOIN agg ON agg.focus = f2.iri
                WHERE f.id = f2.id AND f2.env=%(env)s AND f2.graph_iri=%(g)s
            """, {"env": env, "g": graph, "v": SEVERITY_IRI[3], "w": SEVERITY_IRI[2], "i": SEVERITY_IRI[1],
                  "ok": NO_ISSUES})
    conn.commit()


def _summary_status(run: shacl.Run) -> str:
    return "partial" if run.partial else "ok"


def _set_outcome(conn, env: str, graph: str, status: str, fp: str | None, note: str | None,
                 run: shacl.Run | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute("""UPDATE dataset SET qa_status=%s, qa_shapes=%s, qa_note=%s, qa_checked_at=now(),
                         qa_violations=%s, qa_warnings=%s, qa_infos=%s, qa_focus=%s, qa_kept=%s, qa_seconds=%s
                       WHERE env=%s AND graph_iri=%s""",
                    (status, fp, note or None,
                     run.totals.get(3, 0) if run else None, run.totals.get(2, 0) if run else None,
                     run.totals.get(1, 0) if run else None, len(run.focus_with_results) if run else None,
                     run.kept() if run else None, round(run.seconds, 1) if run else None, env, graph))
    conn.commit()


def store_run(conn: psycopg.Connection, env: str, run: shacl.Run, fp: str, turtle: bytes,
              html: bytes | None) -> None:
    g = run.graph
    with conn.cursor() as cur:
        cur.execute("DELETE FROM violation WHERE env=%s AND graph_iri=%s", (env, g))
        cur.execute("DELETE FROM quality_count WHERE env=%s AND graph_iri=%s", (env, g))
        with cur.copy("COPY violation (env, graph_iri, focus, severity, shape, component, path, value, message) "
                      "FROM STDIN") as cp:
            for r in run.results:
                focus = r.focus[1:-1] if r.focus.startswith("<") else r.focus
                cp.write_row((env, g, focus, r.severity, r.shape, r.component, r.path, r.value, r.message))
        messages: dict[tuple[str, str], str] = {}
        for r in run.results:
            messages.setdefault((r.shape, r.component), r.message)
        with cur.copy("COPY quality_count (env, graph_iri, shape, component, severity, n, message) FROM STDIN") as cp:
            for (shape, comp, sev), n in run.counts.items():
                cp.write_row((env, g, shape, comp, sev, n, messages.get((shape, comp))))
        cur.execute("""INSERT INTO quality_report (env, graph_iri, turtle_gz, html_gz, created_at)
                       VALUES (%s,%s,%s,%s,now()) ON CONFLICT (env, graph_iri) DO UPDATE SET
                       turtle_gz=EXCLUDED.turtle_gz, html_gz=EXCLUDED.html_gz, created_at=now()""",
                    (env, g, gzip.compress(turtle), gzip.compress(html) if html else None))
    conn.commit()
    _set_outcome(conn, env, g, _summary_status(run), fp, run.note(), run)


def build_html(cfg: Settings, env: Env, turtle: bytes, shapes_ttl: bytes) -> bytes | None:
    """Render the report with shacl-report's self-contained HTML viewer, when its CLI is installed."""
    exe = shutil.which(cfg.quality.shacl_report_bin)
    if not exe:
        return None
    with tempfile.TemporaryDirectory(dir=cfg.tmp_dir if cfg.tmp_dir.exists() else None) as d:
        rep, shp, out = Path(d) / "report.ttl", Path(d) / "shapes.ttl", Path(d) / "report.html"
        rep.write_bytes(turtle)
        shp.write_bytes(shapes_ttl)
        try:
            subprocess.run([exe, str(rep), "--shapes", str(shp), "--endpoint", env.sparql, "--html", str(out),
                            "--quiet"], check=True, timeout=600, capture_output=True)
        except (subprocess.SubprocessError, OSError) as e:
            err = getattr(e, "stderr", b"") or b""
            log.warning("%s shacl-report HTML failed: %s %s", env.key, e, err.decode(errors="replace")[:500])
            return None
        return out.read_bytes() if out.exists() else None


def run_fingerprint(cfg: Settings, shapes_fp: str | None) -> str | None:
    """What a graph was validated with: the shapes, plus the rules left out by configuration
    (so changing `quality.exclude_shapes` validates every graph again)."""
    excl = sorted(cfg.quality.exclude_shapes)
    if shapes_fp is None or not excl:
        return shapes_fp
    return hashlib.sha256((shapes_fp + "\n" + "\n".join(excl)).encode()).hexdigest()


def validate_dataset(cfg: Settings, conn: psycopg.Connection, store: ox.Store, env: Env, graph: str,
                     fp: str | None = None) -> str:
    """Validate one provider graph and store the outcome. Returns the qa status."""
    q = cfg.quality
    fp = fp or run_fingerprint(cfg, shapes_fingerprint(store, q.shapes_graph))
    if fp is None:
        _set_outcome(conn, env.key, graph, "skipped", None, "ERA's shapes graph is not mirrored yet")
        apply_flags(conn, env.key, graph)
        return "skipped"
    with conn.cursor() as cur:
        cur.execute("SELECT triples FROM dataset WHERE env=%s AND graph_iri=%s", (env.key, graph))
        row = cur.fetchone()
    triples = row[0] if row and row[0] else sum(1 for _ in store.quads_for_pattern(None, None, None, ox.NamedNode(graph)))
    if triples > q.max_triples:
        remove(conn, env.key, graph)
        _set_outcome(conn, env.key, graph, "skipped", fp,
                     f"{triples:,} triples is above the validation limit of {q.max_triples:,}")
        apply_flags(conn, env.key, graph)
        return "skipped"
    limits = shacl.Limits(deadline=time.time() + q.time_budget_minutes * 60,
                          max_per_constraint=q.max_results_per_constraint, max_total=q.max_results_per_graph,
                          exclude=q.exclude_shapes)
    log.info("%s validating %s (%s triples) against %s", env.key, graph, f"{triples:,}", q.shapes_graph)
    # The checks run on an on-disk scratch copy of the graph (and the vocabularies), queried by a
    # child process that can be killed when a query runs away (see shacl.validate_graph).
    cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
    scratch = tempfile.mkdtemp(prefix=f"validate-{env.key}-", dir=cfg.tmp_dir)
    try:
        run = shacl.validate_graph(store, graph, q.shapes_graph, env.vocab_graphs, limits, scratch=scratch)
    except Exception as e:  # never let validation break ingestion
        conn.rollback()
        log.exception("%s %s validation failed", env.key, graph)
        _set_outcome(conn, env.key, graph, "failed", fp, str(e)[:2000])
        apply_flags(conn, env.key, graph)
        return "failed"
    created = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    code = graph.rstrip("/").rsplit("/", 1)[-1]
    about = (f"RINF Atlas validation of {graph} ({env.key}) against {q.shapes_graph}.\n"
             f"Open it with shacl-report (https://github.com/Matdata-eu/shacl-report) together with the shapes.")
    turtle = shacl.report_turtle(run, report_iri=f"{REPORT_BASE}{env.key}/{code}", created=created,
                                 about=about).encode()
    html = None
    if q.html_max_results and run.kept() <= q.html_max_results:
        html = build_html(cfg, env, turtle, shapes_turtle(store, q.shapes_graph))
    store_run(conn, env.key, run, fp, turtle, html)
    apply_flags(conn, env.key, graph)
    log.info("%s %s: %s violations, %s warnings, %s info on %s focus nodes in %.0fs%s", env.key, graph,
             run.totals.get(3, 0), run.totals.get(2, 0), run.totals.get(1, 0), len(run.focus_with_results),
             run.seconds, f" ({run.note()})" if run.note() else "")
    return _summary_status(run)


def validate_pending(cfg: Settings, conn: psycopg.Connection, store: ox.Store, env: Env, force: bool = False,
                     only: list[str] | None = None, should_stop=lambda: False) -> int:
    """Validate every loaded provider graph not yet validated with the current shapes. Returns how many."""
    if not cfg.quality.enabled:
        return 0
    fp = shapes_fingerprint(store, cfg.quality.shapes_graph)
    if fp is None:
        log.info("%s: ERA shapes graph %s not mirrored, data quality skipped", env.key, cfg.quality.shapes_graph)
        return 0
    publish_shapes(conn, store, env.key, cfg.quality.shapes_graph, fp)
    fp = run_fingerprint(cfg, fp)
    with conn.cursor() as cur:
        cur.execute("""SELECT graph_iri FROM dataset WHERE env=%s AND kind='provider' AND status='loaded'
                         AND (%s OR qa_shapes IS DISTINCT FROM %s) ORDER BY triples NULLS LAST, graph_iri""",
                    (env.key, force, fp))
        graphs = [r[0] for r in cur.fetchall()]
    if only:
        graphs = [g for g in graphs if g in only or g.rsplit("/", 1)[-1] in only]
    n = 0
    for g in graphs:
        if should_stop():
            break
        if not store.contains_named_graph(ox.NamedNode(g)):
            continue
        validate_dataset(cfg, conn, store, env, g, fp)
        n += 1
    return n
