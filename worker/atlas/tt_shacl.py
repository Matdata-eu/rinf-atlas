"""Data quality with TripleTrail's SHACL validator (https://github.com/Matdata-eu/tripletrail).

`atlas.shacl` checks SHACL Core in Python and runs ERA's SHACL-SPARQL constraints one by one
on the mirror. TripleTrail's `tripletrail validate` implements SHACL Core and SHACL-SPARQL
completely and natively: on Infrabel's graph (0088, 676k triples, ERA shapes v3.3.4) it takes
17 s where `atlas.shacl` takes 6 minutes, with the same results, and the two rules
`quality.exclude_shapes` leaves out for their cost add 2 s instead of minutes.

The provider graph and the vocabulary graphs are written to one N-Triples file, the shapes to
Turtle (the mirror's skolem IRIs turned back into blank nodes, except for the shapes
themselves, so a result names a property shape as `atlas.shacl` does), and the report is read
back into a `shacl.Run`: the same dedup, caps and counts, so `quality.store_run` and the rest
of the data quality layer do not change.

Only results whose focus node appears in the provider graph are kept: the vocabularies are
data for lookups (`sh:class`, the SPARQL constraints), not data to validate.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import time
from collections import defaultdict
from pathlib import Path

import pyoxigraph as ox

from .shacl import (
    RDF,
    SEVERITY,
    SH,
    SKOLEM,
    VIOLATION,
    Limits,
    Result,
    Run,
    ShapesGraph,
    Unsupported,
    _default_message,
    _fill,
    nt,
)

# Properties whose objects are shapes: those keep their (skolem) IRI so results name them.
_SHAPE_LINKS = {SH + p for p in ("property", "node", "not", "qualifiedValueShape")}
_SHAPE_LISTS = {SH + p for p in ("and", "or", "xone")}
_TARGETS = {SH + p for p in ("targetClass", "targetNode", "targetSubjectsOf", "targetObjectsOf", "target")}


class TripleTrailError(Exception):
    """`tripletrail validate` failed or ran past the time budget."""


def binary(name: str = "tripletrail") -> str | None:
    """The tripletrail command, when installed."""
    return shutil.which(name)


def _list(g: ShapesGraph, node) -> list:
    try:
        return g.rdf_list(node)
    except Unsupported:
        return []


def shape_nodes(g: ShapesGraph) -> set:
    """Every shape of the shapes graph: node shapes with targets or `sh:property`, property
    shapes (`sh:path`), and the shapes `sh:node`, `sh:not`, `sh:and`... point to."""
    out = set()
    for s, d in g.spo.items():
        if SH + "path" in d or _TARGETS & d.keys() or SH + "property" in d or SH + "sparql" in d:
            out.add(s)
        for p in _SHAPE_LINKS:
            out.update(d.get(p, []))
        for p in _SHAPE_LISTS:
            for lst in d.get(p, []):
                out.update(_list(g, lst))
    return {s for s in out if not isinstance(s, ox.Literal)}


def _excluded(iri: str, exclude: list[str]) -> bool:
    return any(e == iri or (len(e) > 2 and re.search(e, iri)) for e in exclude)


def shapes_triples(triples, exclude: list[str]) -> tuple[list[ox.Triple], set[str]]:
    """The shapes graph of the mirror, ready for TripleTrail: skolem IRIs that are no shape
    (RDF lists, paths, SPARQL constraints) become blank nodes again, as SHACL reads a path or a
    list only from those, and the shapes and named SPARQL constraints `exclude` names are
    turned off (`sh:deactivated`, or the `sh:sparql` link dropped). Returns the triples and
    the IRIs left out."""
    triples = list(triples)
    g = ShapesGraph(triples)
    keep = {s.value for s in shape_nodes(g) if isinstance(s, ox.NamedNode)}
    bnodes: dict[str, ox.BlankNode] = {}

    def term(t):
        if isinstance(t, ox.NamedNode) and t.value.startswith(SKOLEM) and t.value not in keep:
            return bnodes.setdefault(t.value, ox.BlankNode())
        return t

    excluded: set[str] = set()
    out = []
    for t in triples:
        if (t.predicate.value == SH + "sparql" and isinstance(t.object, ox.NamedNode)
                and _excluded(t.object.value, exclude)):
            excluded.add(t.object.value)
            continue
        out.append(ox.Triple(term(t.subject), t.predicate, term(t.object)))
    true = ox.Literal("true", datatype=ox.NamedNode("http://www.w3.org/2001/XMLSchema#boolean"))
    for iri in sorted(keep):
        if _excluded(iri, exclude):
            excluded.add(iri)
            out.append(ox.Triple(ox.NamedNode(iri), ox.NamedNode(SH + "deactivated"), true))
    return out, excluded


def _english(g: ShapesGraph, r) -> str | None:
    vals = [o for o in g.objs(r, SH + "resultMessage") if isinstance(o, ox.Literal)]
    for o in vals:
        if o.language in (None, "", "en"):
            return o.value
    return vals[0].value if vals else None


def read_report(report: ShapesGraph, appears, limits: Limits, run: Run) -> None:
    """Results of a W3C validation report into `run`, as `atlas.shacl.Validator.add` keeps them:
    one per (focus, shape, component, value, path), counted in full, kept up to the caps.
    TripleTrail gives each row of a SPARQL constraint a result, also rows that repeat another
    (on Infrabel's graph, 3,296 of 125,896): those are counted once, as atlas.shacl does."""
    seen: set[tuple] = set()
    kept: dict[tuple[str, str], int] = defaultdict(int)
    in_graph: dict[str, bool] = {}
    results = [s for s, d in report.spo.items()
               if any(isinstance(o, ox.NamedNode) and o.value == SH + "ValidationResult"
                      for o in d.get(RDF + "type", []))]
    for r in results:
        focus = report.one(r, SH + "focusNode")
        shape = report.one(r, SH + "sourceShape")
        comp = report.one(r, SH + "sourceConstraintComponent")
        if focus is None or shape is None or comp is None:
            continue
        f = nt(focus)
        if f not in in_graph:
            in_graph[f] = appears(focus)
        if not in_graph[f]:
            continue
        sev = report.one(r, SH + "resultSeverity")
        severity = SEVERITY.get(sev.value, VIOLATION) if isinstance(sev, ox.NamedNode) else VIOLATION
        shape_id = shape.value if isinstance(shape, ox.NamedNode) else nt(shape)
        path = path_ttl = None
        pnode = report.one(r, SH + "resultPath")
        if pnode is not None:
            try:
                path, path_ttl = report.path(pnode)
            except Unsupported:
                pass
        value = report.one(r, SH + "value")
        v = nt(value) if value is not None else None
        message = _english(report, r)
        if message and "{" in message:
            # TripleTrail leaves a SPARQL constraint's message template as written: fill in the
            # focus node and the value, as atlas.shacl does (other variables stay as they are).
            message = _fill(message, {"this": focus, **({"value": value} if value is not None else {})})
        # Two SPARQL constraints of one node shape that bind no ?value both give the focus node
        # as the value: their messages tell them apart.
        key = (f, shape_id, comp.value, v, path, message if comp.value == SH + "SPARQLConstraintComponent" else None)
        if key in seen:
            continue
        seen.add(key)
        run.counts[(shape_id, comp.value, severity)] += 1
        run.totals[severity] += 1
        run.focus_with_results.add(f)
        kk = (shape_id, comp.value)
        if kept[kk] < limits.max_per_constraint and len(run.results) < limits.max_total:
            kept[kk] += 1
            run.results.append(Result(f, severity, shape_id, comp.value, path, path_ttl, v,
                                      message or _default_message(comp.value, path)))


def validate_graph(store: ox.Store, graph: str, shapes_graph: str, vocab_graphs: list[str],
                   limits: Limits, scratch: str, exe: str) -> Run:
    """Validate one provider graph of the mirror with `tripletrail validate` (in `scratch`, a
    directory this function may fill and the caller removes)."""
    t0 = time.time()
    run = Run(graph=graph)
    work = Path(scratch)
    work.mkdir(parents=True, exist_ok=True)
    g = ox.NamedNode(graph)
    vocab = [v for v in vocab_graphs if v != shapes_graph and store.contains_named_graph(ox.NamedNode(v))]

    data = work / "data.nt"
    with open(data, "wb") as f:
        ox.serialize((q.triple for name in [graph, *vocab]
                      for q in store.quads_for_pattern(None, None, None, ox.NamedNode(name))),
                     f, format=ox.RdfFormat.N_TRIPLES)
    shapes, run.excluded = shapes_triples(
        (q.triple for q in store.quads_for_pattern(None, None, None, ox.NamedNode(shapes_graph))), limits.exclude)
    sg = ShapesGraph(shapes)
    run.node_shapes = run.node_shapes_checked = sum(
        1 for s, d in sg.spo.items() if _TARGETS & d.keys() and SH + "deactivated" not in d)
    shapes_file = work / "shapes.nt"
    with open(shapes_file, "wb") as f:
        ox.serialize(shapes, f, format=ox.RdfFormat.N_TRIPLES)
    run.timings["(write data and shapes)"] += time.time() - t0

    report_file = work / "report.nt"
    left = (limits.deadline - time.time()) if limits.deadline is not None else None
    if left is not None and left <= 0:
        raise TripleTrailError("time budget reached before validation started")
    t1 = time.time()
    try:
        # Exit status 0: conforms, 1: does not conform (warnings included); others are errors.
        p = subprocess.run([exe, "validate", "--data", str(data), "--shapes", str(shapes_file),
                            "--report", str(report_file)], capture_output=True, timeout=left)
    except subprocess.TimeoutExpired as e:
        raise TripleTrailError(f"tripletrail validate ran past the time budget ({left / 60:.0f} min)") from e
    if p.returncode not in (0, 1) or not report_file.exists():
        err = (p.stderr or b"").decode(errors="replace").strip()
        raise TripleTrailError(f"tripletrail validate failed ({p.returncode}): {err[-1000:]}")
    run.timings["(tripletrail validate)"] += time.time() - t1
    data.unlink(missing_ok=True)

    t2 = time.time()
    report = ShapesGraph(ox.parse(path=str(report_file), format=ox.RdfFormat.N_TRIPLES))

    def appears(t) -> bool:
        if next(iter(store.quads_for_pattern(t, None, None, g)), None) is not None:
            return True
        if isinstance(t, ox.NamedNode) and next(iter(store.quads_for_pattern(None, t, None, g)), None) is not None:
            return True
        return next(iter(store.quads_for_pattern(None, None, t, g)), None) is not None

    read_report(report, appears, limits, run)
    run.timings["(read report)"] += time.time() - t2
    run.seconds = time.time() - t0
    return run
