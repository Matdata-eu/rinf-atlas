"""Discover new/changed datasets per environment and keep mirror + index current."""
from __future__ import annotations

import logging
import time
import uuid

import psycopg

from . import era, mirror
from .config import Env, Settings
from .extract import derive_geometry, extract_graph, refresh_facet_cache, update_term_labels
from .shapes import ShapeSet

log = logging.getLogger(__name__)

IM_ROLE = "http://data.europa.eu/949/body/organisation/organisationRole/{code}_IM"


def publish_defs(conn: psycopg.Connection, shapes: ShapeSet) -> None:
    """Write compiled shapes to class_def / facet_def for the API."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM facet_def")
        cur.execute("DELETE FROM class_def")
        for c in shapes.classes:
            cur.execute("""INSERT INTO class_def (class, class_iri, label, color, geometry, position, min_zoom)
                           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                        (c.key, c.class_iri, c.label, c.color, c.geometry, c.order, c.min_zoom))
            for f in c.facets:
                cur.execute("""INSERT INTO facet_def (key, class, label, kind, datatype, position, path, unit)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (f.key, c.key, f.label, f.kind, f.datatype, f.order, f.path, f.unit))
        for f in shapes.shared:
            cur.execute("""INSERT INTO facet_def (key, class, label, kind, datatype, position, path, unit)
                           VALUES (%s,'*',%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                        (f.key, f.label, f.kind, f.datatype, f.order, f.path, f.unit))
    conn.commit()


def _fingerprint(pub: era.Published | None, size: int) -> str:
    return f"{pub.dataset_id if pub else ''}|{pub.published.isoformat() if pub and pub.published else ''}|{size}"


def _download_into_mirror(cfg: Settings, env: Env, store, graph: str) -> None:
    tmp = cfg.tmp_dir / f"{env.key}-{uuid.uuid4().hex}.nt"
    try:
        t0 = time.time()
        n = era.download_graph(env, graph, tmp)
        log.info("%s downloaded %s (%.1f MB) in %.1fs", env.key, graph, n / 1e6, time.time() - t0)
        t0 = time.time()
        mirror.replace_graph(store, graph, tmp)
        log.info("%s mirrored %s in %.1fs", env.key, graph, time.time() - t0)
    finally:
        tmp.unlink(missing_ok=True)


def _sync_graph(cfg, conn, shapes, env, store, g, published, known, force) -> tuple[int, int]:
    """Refresh one provider graph if its fingerprint changed. Returns (new, changed)."""
    code = g.rstrip("/").rsplit("/", 1)[-1]
    pub = published.get(code)
    size = era.graph_size(env, g)
    fp = _fingerprint(pub, size)
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO dataset (env, graph_iri, kind, title, provider, country, modified, triples,
                                            dataset_id, file_name)
                       VALUES (%s,%s,'provider',%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (env, graph_iri) DO UPDATE SET title=EXCLUDED.title, provider=EXCLUDED.provider,
                         country=EXCLUDED.country, modified=EXCLUDED.modified, triples=EXCLUDED.triples,
                         dataset_id=EXCLUDED.dataset_id, file_name=EXCLUDED.file_name""",
                    (env.key, g, (pub.file_name if pub else None) or code, pub.provider if pub else None,
                     pub.country if pub else None, pub.published if pub else None, size,
                     pub.dataset_id if pub else None, pub.file_name if pub else None))
        if pub and pub.provider:
            cur.execute("""INSERT INTO term (env, iri, label) VALUES (%s,%s,%s)
                           ON CONFLICT (env, iri) DO UPDATE SET label=EXCLUDED.label""",
                        (env.key, IM_ROLE.format(code=code), pub.provider))
    conn.commit()
    old = known.get(g)
    if not force and old and old[0] == fp and old[1] == "loaded" and mirror.has_graph(store, g):
        return 0, 0
    is_new = old is None or old[1] in ("pending", "removed")
    _set_status(conn, env.key, g, "loading")
    _download_into_mirror(cfg, env, store, g)
    extract_graph(conn, store, env.key, g, shapes)
    with conn.cursor() as cur:
        cur.execute("""UPDATE dataset SET status='loaded', error=NULL, fingerprint=%s, ingested_at=now()
                       WHERE env=%s AND graph_iri=%s""", (fp, env.key, g))
    conn.commit()
    return (1, 0) if is_new else (0, 1)


def sync_env(cfg: Settings, conn: psycopg.Connection, shapes: ShapeSet, env: Env, force: bool = False) -> dict:
    """One poll of one environment. Returns a summary dict."""
    store = mirror.store(cfg.mirror_dir, env.key)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO ingest_run (env) VALUES (%s) RETURNING id", (env.key,))
        run_id = cur.fetchone()[0]
    conn.commit()

    graphs = era.list_graphs(env)
    try:
        published = era.published_datasets(env)
    except Exception as e:  # explorer down: graphs alone still tell us what changed
        log.warning("%s dataset explorer unavailable: %s", env.key, e)
        published = {}

    with conn.cursor() as cur:
        cur.execute("SELECT graph_iri, fingerprint, status, kind FROM dataset WHERE env=%s", (env.key,))
        known = {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}

    # 1. Vocabularies (ontology, SKOS, countries) — labels only, no features.
    vocab_changed = False
    for g in env.vocab_graphs:
        if g not in graphs:
            log.warning("%s vocabulary graph %s not found", env.key, g)
            continue
        size = era.graph_size(env, g)
        fp = f"|{size}"
        old = known.get(g)
        if not force and old and old[0] == fp and old[1] == "loaded" and mirror.has_graph(store, g):
            continue
        _download_into_mirror(cfg, env, store, g)
        vocab_changed = True
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO dataset (env, graph_iri, kind, title, fingerprint, triples, status, ingested_at)
                           VALUES (%s,%s,'vocab',%s,%s,%s,'loaded',now())
                           ON CONFLICT (env, graph_iri) DO UPDATE SET kind='vocab', fingerprint=EXCLUDED.fingerprint,
                           triples=EXCLUDED.triples, status='loaded', error=NULL, ingested_at=now()""",
                        (env.key, g, g.rsplit("/", 1)[-1], fp, size))
        conn.commit()

    # 2. Provider graphs.
    provider = [g for g in graphs if cfg.is_provider_graph(env, g)]
    new = changed = removed = failed = 0
    for g in provider:
        try:
            n, c = _sync_graph(cfg, conn, shapes, env, store, g, published, known, force)
            new += n
            changed += c
        except Exception as e:
            conn.rollback()
            failed += 1
            log.exception("%s %s failed", env.key, g)
            _set_status(conn, env.key, g, "failed", str(e)[:2000])

    # 3. Graphs that disappeared (or were excluded by config).
    for g, (_, status, kind) in known.items():
        if kind == "provider" and g not in provider and status != "removed":
            removed += 1
            with conn.cursor() as cur:
                cur.execute("DELETE FROM feature WHERE env=%s AND graph_iri=%s", (env.key, g))
                cur.execute("DELETE FROM feature_link WHERE env=%s AND graph_iri=%s", (env.key, g))
                cur.execute("UPDATE dataset SET status='removed' WHERE env=%s AND graph_iri=%s", (env.key, g))
            conn.commit()
            mirror.remove_graph(store, g)

    if vocab_changed:
        with conn.cursor() as cur:
            cur.execute("SELECT iri FROM term WHERE env=%s", (env.key,))
            iris = {r[0] for r in cur.fetchall()} - {IM_ROLE.format(code=c) for c in published}
        update_term_labels(conn, store, env.key, iris, only_missing=False)

    derived = 0
    if new or changed or removed:
        derived = derive_geometry(conn, env.key)
        refresh_facet_cache(conn, env.key)
    msg = f"new={new} changed={changed} removed={removed} failed={failed} derived_geometry={derived}"
    with conn.cursor() as cur:
        cur.execute("""UPDATE ingest_run SET finished_at=now(), new_graphs=%s, changed=%s, removed=%s, message=%s
                       WHERE id=%s""", (new, changed, removed, msg, run_id))
        cur.execute("""INSERT INTO index_state (env, shapes_hash) VALUES (%s,%s)
                       ON CONFLICT (env) DO UPDATE SET shapes_hash=EXCLUDED.shapes_hash, updated_at=now()
                       WHERE index_state.shapes_hash IS NULL""", (env.key, shapes.digest()))
    conn.commit()
    log.info("%s sync done: %s", env.key, msg)
    return {"new": new, "changed": changed, "removed": removed, "failed": failed}


def reindex_env(cfg: Settings, conn: psycopg.Connection, shapes: ShapeSet, env: Env) -> None:
    """Re-extract every loaded graph from the mirror (after a shape change). No download."""
    store = mirror.store(cfg.mirror_dir, env.key)
    with conn.cursor() as cur:
        cur.execute("SELECT graph_iri FROM dataset WHERE env=%s AND kind='provider' AND status IN ('loaded','failed')",
                    (env.key,))
        graphs = [r[0] for r in cur.fetchall()]
    t0 = time.time()
    for g in graphs:
        if not mirror.has_graph(store, g):
            log.warning("%s %s not in mirror; it will be downloaded on the next sync", env.key, g)
            _set_status(conn, env.key, g, "pending")
            continue
        try:
            extract_graph(conn, store, env.key, g, shapes)
            _set_status(conn, env.key, g, "loaded")
        except Exception as e:
            conn.rollback()
            log.exception("%s %s reindex failed", env.key, g)
            _set_status(conn, env.key, g, "failed", str(e)[:2000])
    derive_geometry(conn, env.key)
    refresh_facet_cache(conn, env.key)
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO index_state (env, shapes_hash) VALUES (%s,%s)
                       ON CONFLICT (env) DO UPDATE SET shapes_hash=EXCLUDED.shapes_hash, updated_at=now()""",
                    (env.key, shapes.digest()))
    conn.commit()
    log.info("%s reindexed %d graphs in %.0fs", env.key, len(graphs), time.time() - t0)


def stale_envs(conn: psycopg.Connection, shapes: ShapeSet) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT env FROM index_state WHERE shapes_hash IS DISTINCT FROM %s", (shapes.digest(),))
        return [r[0] for r in cur.fetchall()]


def _set_status(conn, env: str, graph: str, status: str, error: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE dataset SET status=%s, error=%s WHERE env=%s AND graph_iri=%s", (status, error, env, graph))
    conn.commit()
