"""atlas command line.

  atlas run                      poll loop (the worker container's command)
  atlas sync [--env E] [--force] one discovery + ingest pass
  atlas reindex [--env E]        re-extract from the mirror after editing shapes
  atlas generate-shapes [--force] [--no-data]
  atlas load-file --env E --graph IRI file.nt   offline testing
  atlas check-shapes             parse shapes/ and print the compiled facets
  atlas validate [--env E] [--graph G] [--force]
                                 data quality: validate provider graphs against
                                 ERA's SHACL shapes (only those not validated
                                 with the current shapes, unless --force)
  atlas shapes-tags [--env E] [--limit N]
                                 list the tags of ERA's ontology repository that
                                 `quality.shapes_tag` can pin the shapes to
  atlas export --env E --format F [--class K]... [--profile P] [--out FILE]
                                 export the indexed features of an environment (or
                                 some classes) from the mirror, like the UI does
  atlas export-jobs [--once]     run the exports queued by the API (the poll loop
                                 does this by itself while it runs)
  atlas generate-export-profile [--env E] [--out shapes/export/full.ttl]
                                 write the "Full RINF" export profile from ERA's
                                 SHACL shapes and ontology in the mirror

While `atlas run` is active it owns the mirror, so sync / reindex / validate from a
second process are queued as jobs (pass --now to run in-process instead).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

import pyoxigraph as ox

from . import db, era_shapes, exporter, mirror, quality
from .config import settings
from .extract import refresh_facet_cache
from .generate import generate
from .profiles import generate_profile, load_profiles
from .shapes import ShapeError, load_shapes
from .sync import publish_defs, reindex_env, stale_envs, sync_env

log = logging.getLogger("atlas")
SQL = Path(__file__).resolve().parent.parent / "init.sql"
_stop = False


def _sql_path() -> Path:
    for p in (Path("/app/db/init.sql"), SQL, Path(__file__).resolve().parents[2] / "db" / "init.sql"):
        if p.exists():
            return p
    raise FileNotFoundError("init.sql not found")


def _conn():
    cfg = settings()
    conn = db.connect(cfg.database_url)
    db.migrate(conn, _sql_path())
    return conn


def _publish(conn, cfg, shapes) -> None:
    """class_def / facet_def from the facet shapes, export_def from the export profiles."""
    publish_defs(conn, shapes, cfg.quality.enabled)
    try:
        exporter.publish_profiles(conn, load_profiles(cfg.shapes_dir))
    except ShapeError as e:
        conn.rollback()
        log.error("export profiles are invalid, keeping the published ones: %s", e)


def _envs(arg: str | None):
    cfg = settings()
    return [cfg.env(arg)] if arg else list(cfg.envs.values())


def _loop_running(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(777001)")
        got = cur.fetchone()[0]
        if got:
            cur.execute("SELECT pg_advisory_unlock(777001)")
    conn.commit()
    return not got


def _enqueue(conn, kind: str, env: str | None) -> None:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO job (env, kind) VALUES (%s,%s) RETURNING id", (env, kind))
        jid = cur.fetchone()[0]
    conn.commit()
    print(f"queued {kind} job {jid} for {env or 'all environments'}; the running worker picks it up shortly")


def cmd_sync(a) -> None:
    cfg, conn = settings(), _conn()
    if not a.now and _loop_running(conn):
        return _enqueue(conn, "resync" if a.force else "sync", a.env)
    shapes = load_shapes(cfg.shapes_dir)
    _publish(conn, cfg, shapes)
    for env in _envs(a.env):
        sync_env(cfg, conn, shapes, env, force=a.force)


def cmd_reindex(a) -> None:
    cfg, conn = settings(), _conn()
    if not a.now and _loop_running(conn):
        return _enqueue(conn, "reindex", a.env)
    shapes = load_shapes(cfg.shapes_dir)
    _publish(conn, cfg, shapes)
    for env in _envs(a.env):
        reindex_env(cfg, conn, shapes, env)


def cmd_generate(a) -> None:
    conn = _conn()
    if not a.now and _loop_running(conn):
        return _enqueue(conn, "generate-force" if a.force else "generate", None)
    run_generate(conn, force=a.force, no_data=a.no_data, out=a.out)


def run_generate(conn, force=False, no_data=False, out=None) -> None:
    cfg = settings()
    inputs = []
    with conn.cursor() as cur:
        for env in cfg.envs.values():
            store = mirror.store(cfg.mirror_dir, env.key)
            onto = next((g for g in env.vocab_graphs if "ontology" in g), None)
            if not onto or not mirror.has_graph(store, onto):
                log.warning("%s: ontology not mirrored yet, skipped", env.key)
                continue
            graphs = []
            if not no_data:
                cur.execute("SELECT graph_iri FROM dataset WHERE env=%s AND kind='provider' AND status='loaded'", (env.key,))
                graphs = [r[0] for r in cur.fetchall() if mirror.has_graph(store, r[0])]
            inputs.append((store, onto, graphs))
    for p in generate(inputs, Path(out) if out else cfg.shapes_dir, force=force):
        log.info("wrote %s", p)


def cmd_load_file(a) -> None:
    cfg, conn = settings(), _conn()
    from .extract import derive_geometry, extract_graph
    shapes = load_shapes(cfg.shapes_dir)
    _publish(conn, cfg, shapes)
    store = mirror.store(cfg.mirror_dir, a.env)
    env = cfg.env(a.env)
    mirror.replace_graph(store, a.graph, Path(a.file))
    with conn.cursor() as cur:
        triples = sum(1 for _ in store.quads_for_pattern(None, None, None, ox.NamedNode(a.graph)))
        cur.execute("""INSERT INTO dataset (env, graph_iri, kind, title, status, triples, ingested_at)
                       VALUES (%s,%s,%s,%s,'loaded',%s,now()) ON CONFLICT (env, graph_iri)
                       DO UPDATE SET status='loaded', triples=EXCLUDED.triples, ingested_at=now()""",
                    (a.env, a.graph, "vocab" if a.vocab else "provider", Path(a.file).name, triples))
    conn.commit()
    if not a.vocab:
        quality.reset(conn, a.env, a.graph)
        extract_graph(conn, store, a.env, a.graph, shapes)
        derive_geometry(conn, a.env)
    quality.validate_pending(cfg, conn, store, env, only=None if a.vocab else [a.graph])
    refresh_facet_cache(conn, a.env)


def cmd_validate(a) -> None:
    cfg, conn = settings(), _conn()
    if not a.now and _loop_running(conn):
        return _enqueue(conn, "revalidate" if a.force else "validate", a.env)
    for env in _envs(a.env):
        store = mirror.store(cfg.mirror_dir, env.key)
        n = quality.validate_pending(cfg, conn, store, env, force=a.force, only=a.graph or None)
        if n:
            refresh_facet_cache(conn, env.key)
        log.info("%s: validated %d graph(s)", env.key, n)


def cmd_shapes_tags(a) -> None:
    """The tags `quality.shapes_tag` can name, newest first, marking the ones configured."""
    cfg = settings()
    envs = _envs(a.env)
    q = envs[0].quality or cfg.quality if envs else cfg.quality
    configured: dict[str, list[str]] = {}
    for e in envs:
        eq = e.quality or cfg.quality
        configured.setdefault(eq.shapes_tag or "", []).append(e.key)
    print(f"tags of {q.gitlab_url}/{q.shapes_repo} (shapes file: first of {', '.join(q.shapes_files)})")
    for name, date in era_shapes.list_tags(q, limit=a.limit):
        mark = f"   <- {', '.join(configured[name])}" if name in configured else ""
        print(f"  {name:24} {date}{mark}")
    if "" in configured:
        print(f"no tag (ERA's mirrored shapes graph {q.shapes_graph}): {', '.join(configured[''])}")


def cmd_export_jobs(a) -> None:
    cfg, conn = settings(), _conn()
    if a.once:
        n = exporter.run_queued(cfg, conn)
        print(f"ran {n} export(s)")
        return
    svc = exporter.ExportService(cfg)
    svc.start()
    signal.signal(signal.SIGTERM, lambda *_: _set_stop())
    signal.signal(signal.SIGINT, lambda *_: _set_stop())
    while not _stop:
        time.sleep(1)
    svc.stop()


def cmd_export(a) -> None:
    """One export from the command line, in this process (no queue): writes the file and prints its path."""
    import secrets
    cfg, conn = settings(), _conn()
    cfg.env(a.env)
    sql, params = "f.env = %s", [a.env]
    if a.cls:
        sql += " AND f.class = ANY(%s)"
        params.append(a.cls)
    if a.graph:
        sql += " AND f.graph_iri = ANY(%s)"
        params.append(a.graph)
    req = {"env": a.env, "format": a.format, "sql": sql, "params": params, "profile": a.profile,
           "values": a.values, "headers": a.headers, "shape": a.shape, "gzip": a.gzip,
           "follow": ["http://data.europa.eu/949/hasPart"] if a.parts else [],
           "state": {"env": a.env, "classes": a.cls or [], "graphs": a.graph or []}}
    exporter.Request.parse(req)
    eid = "cli-" + secrets.token_urlsafe(6)
    with conn.cursor() as cur:
        cur.execute("INSERT INTO export (id, env, request, status) VALUES (%s,%s,%s,'claimed')",
                    (eid, a.env, json.dumps(req)))
    conn.commit()
    exporter.run_export(cfg, conn, {"id": eid, "request": req}, load_profiles(cfg.shapes_dir))
    with conn.cursor() as cur:
        cur.execute("SELECT status, path, total, bytes, error, warning FROM export WHERE id=%s", (eid,))
        status, path, total, size, error, warning = cur.fetchone()
    if status != "done":
        sys.exit(f"export failed: {error}")
    src = cfg.export_dir / path
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        src.replace(a.out)
        src = Path(a.out)
        with conn.cursor() as cur:
            cur.execute("DELETE FROM export WHERE id=%s", (eid,))
        conn.commit()
    print(f"{src}: {total} entities, {size} bytes" + (f"\nwarning: {warning}" if warning else ""))


def cmd_generate_export_profile(a) -> None:
    cfg = settings()
    envs = _envs(a.env) if a.env else list(cfg.envs.values())
    for env in envs:
        store = mirror.store(cfg.mirror_dir, env.key)
        q = env.quality or cfg.quality
        shapes_graph = q.shapes_graph
        if q.shapes_tag:
            shapes_graph = era_shapes.tag_graph(q.shapes_tag)
        if not mirror.has_graph(store, shapes_graph):
            log.warning("%s: ERA's shapes graph %s is not mirrored yet, skipped", env.key, shapes_graph)
            continue
        onto = [g for g in env.vocab_graphs if mirror.has_graph(store, g)]
        version = era_shapes._describe(store, shapes_graph)[0]
        text = generate_profile(store, shapes_graph, onto or None, version=version)
        out = Path(a.out) if a.out else cfg.shapes_dir / "export" / "full.ttl"
        if out.exists() and not a.force:
            sys.exit(f"{out} exists; pass --force to overwrite it")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {out} from {env.key} ({shapes_graph})")
        return
    sys.exit("no environment has ERA's shapes graph in its mirror")


def cmd_check_shapes(a) -> None:
    shapes = load_shapes(settings().shapes_dir)
    print(f"shapes digest {shapes.digest()}")
    for p in load_profiles(settings().shapes_dir):
        print(f"export profile {p.key!r} ({p.label}): {len(p.classes)} classes, "
              f"{sum(len(c.leaves()) for c in p.classes.values())} columns")
    for f in shapes.shared:
        print(f"  * {f.key:32} {f.kind:8} {f.path}")
    for c in shapes.classes:
        print(f"{c.key} <{c.class_iri}> {len(c.facets)} facets, geometry={c.geometry_paths} "
              f"line={c.line_between} from={c.geometry_from}")
        if a.verbose:
            for f in c.facets:
                print(f"    {f.key:32} {f.kind:8} {f.path}")
    if shapes.compat:
        cp = shapes.compat
        print(f"compatibility: {len(cp.rules)} rules on {', '.join(cp.track_classes)} / {cp.section_class}")
        for r in cp.rules:
            extra = "".join([f"  missing={r.when_missing}" if r.when_missing != "unknown" else "",
                             f"  none={r.none_value}" if r.none_value else "",
                             f"  noneFrom={r.none_from}" if r.none_from else "",
                             f"  without={r.without_value}" if r.without_value else "",
                             f"  termValues={len(r.term_values)}" if r.term_values else "",
                             f"  section={','.join(r.section_facets)}" if r.section_facets else ""])
            print(f"  {r.key:32} sh:{r.constraint:13} {r.applies_to:7} {', '.join(r.facets)}{extra}")


def cmd_run(a) -> None:
    cfg, conn = settings(), _conn()
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(777001)")
        if not cur.fetchone()[0]:
            sys.exit("another worker loop is already running")
    conn.commit()
    if os.environ.get("ATLAS_DESCRIBE"):
        log.warning("ATLAS_DESCRIBE is ignored: the describe index was removed in 1.6.0 "
                    "(RINF Describe builds its pages live from ERA)")
    signal.signal(signal.SIGTERM, lambda *_: _set_stop())
    signal.signal(signal.SIGINT, lambda *_: _set_stop())
    exports = exporter.ExportService(cfg)
    exports.start()
    next_poll = 0.0
    while not _stop:
        try:
            shapes = load_shapes(cfg.shapes_dir)
        except ShapeError as e:
            log.error("shapes are invalid, keeping the current index: %s", e)
            _sleep(60)
            continue
        try:
            _publish(conn, cfg, shapes)
            for env in stale_envs(conn, shapes):
                if env in cfg.envs:
                    log.info("%s: shapes changed, reindexing from the mirror", env)
                    reindex_env(cfg, conn, shapes, cfg.envs[env])
            jobs = _take_jobs(conn)
            for jid, env, kind in jobs:
                if kind.startswith("generate"):
                    run_generate(conn, force=kind.endswith("force"))
                    _finish_job(conn, jid, "done")
                    continue
                for e in ([cfg.env(env)] if env else cfg.envs.values()):
                    if kind == "reindex":
                        reindex_env(cfg, conn, shapes, e)
                    elif kind in ("validate", "revalidate"):
                        if quality.validate_pending(cfg, conn, mirror.store(cfg.mirror_dir, e.key), e,
                                                    force=kind == "revalidate", should_stop=lambda: _stop):
                            refresh_facet_cache(conn, e.key)
                    else:
                        sync_env(cfg, conn, shapes, e, force=(kind == "resync"), should_stop=lambda: _stop)
                _finish_job(conn, jid, "done")
            if time.time() >= next_poll:
                for env in cfg.envs.values():
                    if _stop:
                        break
                    try:
                        sync_env(cfg, conn, shapes, env, should_stop=lambda: _stop)
                    except Exception:
                        conn.rollback()
                        log.exception("%s sync failed; retrying next poll", env.key)
                next_poll = time.time() + cfg.poll_interval_minutes * 60
        except Exception:
            conn.rollback()
            log.exception("worker iteration failed")
        _sleep(20)
    log.info("stopping")
    exports.stop()


def _take_jobs(conn):
    with conn.cursor() as cur:
        cur.execute("""UPDATE job SET started_at=now() WHERE finished_at IS NULL AND started_at IS NULL
                       RETURNING id, env, kind""")
        rows = cur.fetchall()
    conn.commit()
    return sorted(rows)


def _finish_job(conn, jid, msg):
    with conn.cursor() as cur:
        cur.execute("UPDATE job SET finished_at=now(), message=%s WHERE id=%s", (msg, jid))
    conn.commit()


def _set_stop():
    global _stop
    _stop = True


def _sleep(sec: float):
    end = time.time() + sec
    while not _stop and time.time() < end:
        time.sleep(1)


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(prog="atlas", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run").set_defaults(fn=cmd_run)
    s = sub.add_parser("sync")
    s.add_argument("--env")
    s.add_argument("--force", action="store_true")
    s.add_argument("--now", action="store_true")
    s.set_defaults(fn=cmd_sync)
    s = sub.add_parser("reindex")
    s.add_argument("--env")
    s.add_argument("--now", action="store_true")
    s.set_defaults(fn=cmd_reindex)
    s = sub.add_parser("generate-shapes")
    s.add_argument("--force", action="store_true", help="overwrite existing shape files")
    s.add_argument("--no-data", action="store_true", help="ontology only, ignore mirrored data")
    s.add_argument("--out", help="output folder (default: the shapes folder)")
    s.add_argument("--now", action="store_true")
    s.set_defaults(fn=cmd_generate)
    s = sub.add_parser("load-file")
    s.add_argument("--env", required=True)
    s.add_argument("--graph", required=True)
    s.add_argument("--vocab", action="store_true")
    s.add_argument("file")
    s.set_defaults(fn=cmd_load_file)
    s = sub.add_parser("validate", help="data quality: validate provider graphs against ERA's SHACL shapes")
    s.add_argument("--env")
    s.add_argument("--graph", action="append", help="graph IRI or IM code (repeatable); default: every pending graph")
    s.add_argument("--force", action="store_true", help="validate again even if nothing changed")
    s.add_argument("--now", action="store_true")
    s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("shapes-tags", help="list the ERA ontology tags quality.shapes_tag can use (GitLab API)")
    s.add_argument("--env")
    s.add_argument("--limit", type=int, default=100)
    s.set_defaults(fn=cmd_shapes_tags)
    s = sub.add_parser("check-shapes")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_check_shapes)
    s = sub.add_parser("export", help="export indexed features from the mirror to a file")
    s.add_argument("--env", required=True)
    s.add_argument("--format", required=True, choices=sorted(exporter.FORMATS))
    s.add_argument("--class", dest="cls", action="append", help="class key (repeatable); default: every class")
    s.add_argument("--graph", action="append", help="provider graph IRI (repeatable); default: every dataset")
    s.add_argument("--profile", help="export profile key for tables (default: full)")
    s.add_argument("--values", default="label", choices=["label", "iri", "both"])
    s.add_argument("--headers", default="label", choices=["label", "rinf", "path"])
    s.add_argument("--shape", default="wide", choices=["wide", "long"])
    s.add_argument("--parts", action="store_true", help="also resolve the parts (era:hasPart) of each element")
    s.add_argument("--gzip", action="store_true")
    s.add_argument("--out", help="write here instead of the exports folder")
    s.set_defaults(fn=cmd_export)
    s = sub.add_parser("export-jobs", help="run the exports queued by the API")
    s.add_argument("--once", action="store_true", help="run what is queued now, then exit")
    s.set_defaults(fn=cmd_export_jobs)
    s = sub.add_parser("generate-export-profile", help="write the Full RINF export profile from ERA's shapes")
    s.add_argument("--env")
    s.add_argument("--out")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_generate_export_profile)
    a = ap.parse_args(argv)
    try:
        a.fn(a)
    finally:
        mirror.close_all()


if __name__ == "__main__":
    main()
