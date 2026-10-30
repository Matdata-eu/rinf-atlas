"""Describe index: precomputed pages for RINF Describe, driven by SHACL DASH shapes.

Optional and off by default (ATLAS_DESCRIBE=1 turns it on). When on, every provider
graph that is (re)indexed also gets:

  describe_triple  every triple of the graph, for "All properties", incoming links,
                   the graph view and exports, without asking ERA
  describe_doc     one JSON document per resource of a class that has a describe
                   shape: each property row of the shape evaluated against the mirror,
                   summaries over child resources (rd:over + rd:aggregate), linked
                   resources as tables (sh:node), and the data status

The shapes (ATLAS_DESCRIBE_SHAPES, a folder of *.ttl) belong to RINF Describe; their
layout (groups, order, viewers) is compiled into describe_def for the app. Nothing
here changes the Atlas index: the tables are separate and Atlas never reads them.

Vocabulary: standard SHACL (sh:path, sh:name, sh:order, sh:group, sh:node) and DASH
(dash:viewer, dash:propertyRole), plus rd: for what DASH lacks:
  rd:key        stable key of a property row (default: from sh:name or the path)
  rd:tab        tab of a sh:PropertyGroup ("overview" by default)
  rd:over       path to child resources; the row's sh:path is evaluated on each child
  rd:aggregate  "range" | "distinct-count" | "count": how child values are summed up
  rd:unit, rd:format  display hints, passed through
  any other rd: predicate whose object is an IRI or a path node is a named path the
  row's viewer needs (rd:start, rd:end, rd:length for the route diagram ...);
  literal rd: values are passed through as viewer settings.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

import psycopg
import pyoxigraph as ox

from .shapes import RDF, RDFS, SH, XSD, ShapeError, _iri, _Reader, compact_path

log = logging.getLogger(__name__)

RD = "https://rinf-describe.matdata.eu/ns#"
DASH = "http://datashapes.org/dash#"
ERA = "http://data.europa.eu/949/"
AGGREGATES = ("range", "distinct-count", "count")
_KNOWN_RD = {RD + k for k in ("key", "tab", "over", "aggregate", "unit", "format", "enabled")}
# Bumped when indexing writes something else for the same shapes (part of the digest).
DESCRIBE_VERSION = 1


@dataclass
class Group:
    iri: str
    label: str
    order: int = 100
    tab: str = "overview"


@dataclass
class Row:
    key: str
    name: str | None
    path: str | None                 # SPARQL property path, None for a viewer fed by params only
    group: str | None = None
    order: int = 100
    viewer: str | None = None
    roles: list[str] = field(default_factory=list)
    over: str | None = None
    aggregate: str | None = None
    unit: str | None = None
    format: str | None = None
    params: dict[str, str] = field(default_factory=dict)       # rd:name -> SPARQL path
    settings: dict[str, str] = field(default_factory=dict)     # rd:name -> literal
    columns: list[Row] = field(default_factory=list)           # from sh:node


@dataclass
class DocShape:
    iri: str
    class_iri: str
    label: str
    rows: list[Row] = field(default_factory=list)


@dataclass
class DescribeSet:
    shapes: list[DocShape]
    groups: dict[str, Group]

    def digest(self) -> str:
        blob = json.dumps([DESCRIBE_VERSION] + [asdict(s) for s in self.shapes]
                          + [asdict(g) for g in sorted(self.groups.values(), key=lambda g: g.iri)], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "row"


def _local(iri: str) -> str:
    return re.split(r"[#/]", iri.rstrip("/#"))[-1]


def _row(r: _Reader, prop, where: str, nested: bool = False) -> Row | None:
    if r.lit(prop, RD + "enabled", "true") == "false":
        return None
    pnode = r.one(prop, SH + "path")
    name = r.label(prop, SH + "name", RDFS + "label")
    path = r.path(pnode) if pnode is not None else None
    key = r.lit(prop, RD + "key") or (_slug(name) if name else _slug(compact_path(path)) if path else None)
    if not key:
        raise ShapeError(f"{where}: a property without sh:path needs sh:name or rd:key")
    row = Row(key=key, name=name, path=path,
              group=_iri(r.one(prop, SH + "group")),
              order=int(float(r.lit(prop, SH + "order", "100"))),
              viewer=_iri(r.one(prop, DASH + "viewer")),
              roles=sorted(i for i in (_iri(o) for o in r.objs(prop, DASH + "propertyRole")) if i),
              aggregate=r.lit(prop, RD + "aggregate"),
              unit=r.lit(prop, RD + "unit"),
              format=r.lit(prop, RD + "format"))
    over = r.one(prop, RD + "over")
    if over is not None:
        row.over = r.path(over)
    if row.aggregate and row.aggregate not in AGGREGATES:
        raise ShapeError(f"{where}.{key}: rd:aggregate must be one of {', '.join(AGGREGATES)}")
    if row.over and not row.path:
        raise ShapeError(f"{where}.{key}: rd:over needs a sh:path to evaluate on each child")
    if row.aggregate and not row.over:
        raise ShapeError(f"{where}.{key}: rd:aggregate needs rd:over")
    for q in r.s.quads_for_pattern(prop, None, None):
        p = q.predicate.value
        if not p.startswith(RD) or p in _KNOWN_RD:
            continue
        name_ = p[len(RD):]
        if isinstance(q.object, ox.Literal):
            row.settings[name_] = q.object.value
        else:
            row.params[name_] = r.path(q.object)
    if not row.path and not row.params:
        raise ShapeError(f"{where}.{key}: needs sh:path or rd: paths for its viewer")
    node = r.one(prop, SH + "node")
    if node is not None:
        if nested:
            raise ShapeError(f"{where}.{key}: sh:node inside sh:node is not supported")
        if row.over or not row.path:
            raise ShapeError(f"{where}.{key}: sh:node needs a plain sh:path to the linked resources")
        for col in r.objs(node, SH + "property"):
            c = _row(r, col, f"{where}.{key}", nested=True)
            if c is None:
                continue
            if not c.path or c.over or c.params:
                raise ShapeError(f"{where}.{key}.{c.key}: a column needs a plain sh:path")
            row.columns.append(c)
        _unique([c.key for c in row.columns], f"{where}.{key}")
        row.columns.sort(key=lambda c: (c.order, c.key))
    return row


def _unique(keys: list[str], where: str) -> None:
    dup = sorted({k for k in keys if keys.count(k) > 1})
    if dup:
        raise ShapeError(f"{where}: duplicate row key(s) {', '.join(dup)}; set rd:key")


def load_describe_shapes(shapes_dir: Path) -> DescribeSet:
    st = ox.Store()
    files = sorted(shapes_dir.glob("**/*.ttl"))
    if not files:
        raise ShapeError(f"no describe shapes found in {shapes_dir}")
    for f in files:
        try:
            st.load(path=str(f), format=ox.RdfFormat.TURTLE)
        except SyntaxError as e:
            raise ShapeError(f"{f.name}: {e}") from e
    r = _Reader(st)
    groups: dict[str, Group] = {}
    for q in st.quads_for_pattern(None, ox.NamedNode(RDF + "type"), ox.NamedNode(SH + "PropertyGroup")):
        g = q.subject.value
        groups[g] = Group(iri=g, label=r.label(q.subject, RDFS + "label", SH + "name") or _local(g),
                          order=int(float(r.lit(q.subject, SH + "order", "100"))),
                          tab=r.lit(q.subject, RD + "tab", "overview"))
    shapes: list[DocShape] = []
    for q in st.quads_for_pattern(None, ox.NamedNode(SH + "targetClass"), None):
        node = q.subject
        if not isinstance(node, ox.NamedNode) or r.lit(node, RD + "enabled", "true") == "false":
            continue
        target = _iri(q.object)
        where = _local(node.value)
        ds = DocShape(iri=node.value, class_iri=target, label=r.label(node, RDFS + "label", SH + "name") or where)
        for prop in r.objs(node, SH + "property"):
            row = _row(r, prop, where)
            if row is None:
                continue
            if row.group and row.group not in groups:
                raise ShapeError(f"{where}.{row.key}: sh:group {row.group} is not a sh:PropertyGroup")
            ds.rows.append(row)
        _unique([x.key for x in ds.rows], where)
        ds.rows.sort(key=lambda x: (x.order, x.key))
        shapes.append(ds)
    if not shapes:
        raise ShapeError(f"no node shape with sh:targetClass in {shapes_dir}")
    shapes.sort(key=lambda s: s.iri)
    return DescribeSet(shapes=shapes, groups=groups)


def load_enabled(cfg) -> DescribeSet | None:
    """The describe shapes when the describe index is switched on, else None.
    Invalid shapes are logged and skip describe indexing; Atlas's own index carries on."""
    if not cfg.describe:
        return None
    try:
        return load_describe_shapes(cfg.describe_shapes_dir)
    except ShapeError as e:
        log.error("describe shapes are invalid, describe index not updated: %s", e)
        return None


# --- evaluating shapes against one graph of the mirror ---------------------------

def term_json(t) -> dict | None:
    if isinstance(t, ox.NamedNode):
        return {"iri": t.value}
    if isinstance(t, ox.Literal):
        out = {"value": t.value}
        if t.language:
            out["lang"] = t.language
        elif t.datatype.value != XSD + "string":
            out["datatype"] = t.datatype.value
        return out
    return None


def _num(v: str | None) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _add(lst: list, v: dict | None) -> None:
    if v is not None and v not in lst:
        lst.append(v)


class _Eval:
    def __init__(self, store: ox.Store, graph: str):
        self.store = store
        self.dg = [ox.NamedNode(graph)]
        self.iris: set[str] = set()  # IRIs shown on pages: their labels go to `term`

    def q(self, sparql: str):
        res = self.store.query(sparql, default_graph=self.dg)
        names = [v.value for v in res.variables]
        for sol in res:
            yield {n: sol[n] for n in names}

    def val(self, t) -> dict | None:
        v = term_json(t)
        if v and "iri" in v:
            self.iris.add(v["iri"])
        return v

    def subjects(self, cls: str) -> list[str]:
        return [r["s"].value for r in self.q(f"SELECT DISTINCT ?s WHERE {{ ?s a <{cls}> }}")
                if isinstance(r["s"], ox.NamedNode)]

    def values(self, cls: str, path: str) -> dict[str, list]:
        out: dict[str, list] = defaultdict(list)
        for r in self.q(f"SELECT ?s ?v WHERE {{ ?s a <{cls}> . ?s {path} ?v }}"):
            if isinstance(r["s"], ox.NamedNode):
                _add(out[r["s"].value], self.val(r["v"]))
        return out

    def child_values(self, cls: str, over: str, path: str) -> dict[str, dict[str, list]]:
        """subject -> child IRI -> values of `path` on that child (empty list: child without a value)."""
        out: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for r in self.q(f"SELECT ?s ?c ?v WHERE {{ ?s a <{cls}> . ?s {over} ?c . OPTIONAL {{ ?c {path} ?v }} }}"):
            if not isinstance(r["s"], ox.NamedNode) or not isinstance(r["c"], ox.NamedNode):
                continue
            lst = out[r["s"].value][r["c"].value]
            if r["v"] is not None:
                _add(lst, term_json(r["v"]))
        return out


def aggregate(kind: str, per_child: dict[str, list]) -> dict:
    """Sum up the values of a row over child resources ({child: [values]})."""
    n = len(per_child)
    if kind == "count":
        return {"count": n}
    if kind == "range":
        nums = [x for vs in per_child.values() for v in vs for x in [_num(v.get("value"))] if x is not None]
        with_value = sum(1 for vs in per_child.values() if vs)
        if not nums:
            return {"children": n, "withValue": with_value}
        lo, hi = min(nums), max(nums)
        return {"min": int(lo) if lo.is_integer() else lo, "max": int(hi) if hi.is_integer() else hi,
                "children": n, "withValue": with_value}
    counts: dict[str, tuple[dict, int]] = {}
    for vs in per_child.values():
        for v in vs:
            k = json.dumps(v, sort_keys=True)
            counts[k] = (v, counts.get(k, (v, 0))[1] + 1)
    values = sorted(({**v, "count": c} for v, c in counts.values()), key=lambda d: (-d["count"], json.dumps(d)))
    return {"values": values, "children": n, "withoutValue": sum(1 for vs in per_child.values() if not vs)}


def build_docs(store: ox.Store, graph: str, dset: DescribeSet) -> tuple[list[tuple[str, str, dict]], set[str]]:
    """Evaluate every describe shape on one graph. Returns ([(iri, shape iri, doc)], IRIs to label)."""
    ev = _Eval(store, graph)
    out: list[tuple[str, str, dict]] = []
    for ds in dset.shapes:
        subs = ev.subjects(ds.class_iri)
        if not subs:
            continue
        docs: dict[str, dict] = {s: {"class": ds.class_iri, "rows": {}} for s in subs}
        for row in ds.rows:
            entry: dict[str, dict] = defaultdict(dict)
            if row.over:
                for s, per_child in ev.child_values(ds.class_iri, row.over, row.path).items():
                    for c, vs in per_child.items():
                        ev.iris.add(c)
                        for v in vs:
                            if "iri" in v:
                                ev.iris.add(v["iri"])
                    entry[s]["aggregate"] = aggregate(row.aggregate or "distinct-count", per_child)
            elif row.columns:
                items: dict[str, dict[str, dict]] = defaultdict(dict)
                for s, vs in ev.values(ds.class_iri, row.path).items():
                    for v in vs:
                        if "iri" in v:
                            items[s].setdefault(v["iri"], {"iri": v["iri"], "cells": {}})
                for col in row.columns:
                    for s, per_child in ev.child_values(ds.class_iri, row.path, col.path).items():
                        for c, vs in per_child.items():
                            if c in items[s]:
                                items[s][c]["cells"][col.key] = vs
                                for v in vs:
                                    if "iri" in v:
                                        ev.iris.add(v["iri"])
                for s, by_iri in items.items():
                    entry[s]["items"] = sorted(by_iri.values(), key=lambda i: i["iri"])
            elif row.path:
                for s, vs in ev.values(ds.class_iri, row.path).items():
                    entry[s]["values"] = vs
            for name, path in row.params.items():
                for s, vs in ev.values(ds.class_iri, path).items():
                    entry[s].setdefault("params", {})[name] = vs
            for s, e in entry.items():
                if s in docs:
                    docs[s]["rows"][row.key] = e
        for name, pred in (("notApplicable", ERA + "notApplicable"), ("notYetAvailable", ERA + "notYetAvailable")):
            for s, vs in ev.values(ds.class_iri, f"<{pred}>").items():
                if s in docs:
                    docs[s].setdefault("dataStatus", {})[name] = sorted(v["iri"] for v in vs if "iri" in v)
        out.extend((s, ds.iri, d) for s, d in docs.items())
    return out, ev.iris


def graph_triples(store: ox.Store, graph: str):
    """Rows for describe_triple: (s, p, o, o_iri, datatype, lang)."""
    for q in store.quads_for_pattern(None, None, None, ox.NamedNode(graph)):
        s, p, o = q.subject, q.predicate, q.object
        if not isinstance(s, ox.NamedNode):
            continue
        if isinstance(o, ox.NamedNode):
            yield s.value, p.value, o.value, True, None, None
        elif isinstance(o, ox.Literal):
            dt = None if o.language or o.datatype.value == XSD + "string" else o.datatype.value
            yield s.value, p.value, o.value, False, dt, o.language


# --- writing to PostGIS ----------------------------------------------------------

def publish_defs(conn: psycopg.Connection, dset: DescribeSet) -> None:
    """Compiled describe shapes for the app: layout only, values are in describe_doc."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM describe_def")
        for ds in dset.shapes:
            used = {r.group for r in ds.rows if r.group}
            d = {"label": ds.label, "rows": [asdict(r) for r in ds.rows],
                 "groups": [asdict(dset.groups[g]) for g in sorted(used, key=lambda g: (dset.groups[g].order, g))]}
            cur.execute("INSERT INTO describe_def (shape, class_iri, def) VALUES (%s,%s,%s)",
                        (ds.iri, ds.class_iri, json.dumps(d, ensure_ascii=False)))
    conn.commit()


def index_graph(conn: psycopg.Connection, store: ox.Store, env: str, graph: str, dset: DescribeSet,
                triples: bool = True) -> int:
    """Replace the describe rows of (env, graph). Commits. Returns the number of documents."""
    from .extract import update_term_labels  # labels go to Atlas's term table, shared with the map

    t0 = time.time()
    docs, iris = build_docs(store, graph, dset)
    n_triples = 0
    with conn.cursor() as cur:
        cur.execute("DELETE FROM describe_doc WHERE env=%s AND graph_iri=%s", (env, graph))
        with cur.copy("COPY describe_doc (env, iri, shape, graph_iri, doc) FROM STDIN") as cp:
            for iri, shape, doc in docs:
                cp.write_row((env, iri, shape, graph, json.dumps(doc, ensure_ascii=False)))
        if triples:
            cur.execute("DELETE FROM describe_triple WHERE env=%s AND graph_iri=%s", (env, graph))
            with cur.copy("COPY describe_triple (env, graph_iri, s, p, o, o_iri, datatype, lang) FROM STDIN") as cp:
                for row in graph_triples(store, graph):
                    cp.write_row((env, graph) + row)
                    n_triples += 1
    conn.commit()
    update_term_labels(conn, store, env, iris, graph)
    log.info("%s %s: %d describe documents%s in %.1fs", env, graph, len(docs),
             f", {n_triples} triples" if triples else "", time.time() - t0)
    return len(docs)


def remove_graph(conn: psycopg.Connection, env: str, graph: str) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM describe_doc WHERE env=%s AND graph_iri=%s", (env, graph))
        cur.execute("DELETE FROM describe_triple WHERE env=%s AND graph_iri=%s", (env, graph))
    conn.commit()


def stale_envs(conn: psycopg.Connection, dset: DescribeSet, envs) -> dict[str, bool]:
    """Environments whose describe rows were built with other shapes: env -> whether the
    triples are missing too (describe was just switched on) or only the documents changed."""
    with conn.cursor() as cur:
        cur.execute("SELECT env, describe_hash FROM index_state WHERE shapes_hash IS NOT NULL")
        done = dict(cur.fetchall())
    return {e: done[e] is None for e in envs if e in done and done[e] != dset.digest()}


def mark_done(conn: psycopg.Connection, env: str, dset: DescribeSet) -> None:
    """Record the shapes the describe rows of `env` were built with (Atlas's sync creates the row)."""
    with conn.cursor() as cur:
        cur.execute("UPDATE index_state SET describe_hash=%s WHERE env=%s", (dset.digest(), env))
    conn.commit()


def reindex(conn: psycopg.Connection, store: ox.Store, env: str, dset: DescribeSet, triples: bool) -> None:
    """Rebuild the describe rows of every loaded graph from the mirror. No download."""
    from .mirror import has_graph

    with conn.cursor() as cur:
        cur.execute("SELECT graph_iri FROM dataset WHERE env=%s AND kind='provider' AND status='loaded'", (env,))
        graphs = [r[0] for r in cur.fetchall()]
    t0 = time.time()
    for g in graphs:
        if not has_graph(store, g):
            continue
        try:
            index_graph(conn, store, env, g, dset, triples=triples)
        except Exception:
            conn.rollback()
            log.exception("%s %s: describe indexing failed", env, g)
    mark_done(conn, env, dset)
    log.info("%s: describe index rebuilt for %d graphs in %.0fs", env, len(graphs), time.time() - t0)
