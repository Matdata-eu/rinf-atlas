"""A SHACL validator that runs on the Oxigraph mirror.

ERA publishes the shapes it checks RINF uploads with in the named graph
/graph/rinf/shacl. pyshacl (rdflib, in memory) needs gigabytes and hours for
the large provider graphs, so this module validates one provider graph in
place, on the on-disk mirror:

* focus nodes and property values come from SPARQL on the provider graph,
  a few dozen predicates per query (the same batching as facet extraction);
* SHACL Core constraints are then checked in Python;
* SHACL-SPARQL constraints (most of ERA's business rules) and SPARQL targets
  run as they are, with ``$this`` left unbound, over the provider graph plus
  the vocabulary graphs (ontology, SKOS, countries), and their rows are kept
  for the shape's focus nodes only;
* ``sh:class`` looks up types in the whole mirror, so a value typed in the
  SKOS graph or by another provider's graph counts.

Supported: every target type (class, node, subjectsOf, objectsOf, SPARQL),
cardinality, value type (class, datatype, nodeKind), ranges, lengths,
pattern, in, hasValue, uniqueLang, property pairs (equals, disjoint,
lessThan, lessThanOrEquals), closed, qualified value shapes and the logical
constraints (not, and, or, xone, node) as long as the shapes they combine only
constrain the value itself. Anything else is reported as unsupported in the
run's notes instead of being guessed. A deadline stops the run between two
queries (or while rows stream in); the result is then marked partial.
"""
from __future__ import annotations

import logging
import os
import pickle
import re
import select
import shutil
import struct
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pyoxigraph as ox

log = logging.getLogger(__name__)

SH = "http://www.w3.org/ns/shacl#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
XSD = "http://www.w3.org/2001/XMLSchema#"
RDF_TYPE = RDF + "type"
SKOLEM = "urn:atlas:bnode:"  # blank nodes are rewritten to these IRIs when mirrored

VIOLATION, WARNING, INFO = 3, 2, 1
SEVERITY = {SH + "Violation": VIOLATION, SH + "Warning": WARNING, SH + "Info": INFO}
SEVERITY_IRI = {v: k for k, v in SEVERITY.items()}

# Value-level constraint parameters: they constrain one value node at a time.
_VALUE_PARAMS = {"class", "datatype", "nodeKind", "pattern", "minInclusive", "maxInclusive", "minExclusive",
                 "maxExclusive", "minLength", "maxLength", "in", "not", "and", "or", "xone", "node", "hasValue"}
_IGNORED = {"flags", "message", "severity", "name", "description", "order", "group", "defaultValue", "path",
            "targetClass", "targetNode", "targetSubjectsOf", "targetObjectsOf", "target", "deactivated",
            "property", "sparql", "closed", "ignoredProperties", "minCount", "maxCount", "uniqueLang",
            "equals", "disjoint", "lessThan", "lessThanOrEquals", "qualifiedValueShape",
            "qualifiedMinCount", "qualifiedMaxCount", "qualifiedValueShapesDisjoint", "prefixes", "declare"}

_NUMERIC = {"integer", "int", "long", "short", "byte", "decimal", "double", "float", "nonNegativeInteger",
            "positiveInteger", "negativeInteger", "nonPositiveInteger", "unsignedInt", "unsignedLong",
            "unsignedShort", "unsignedByte"}
_LEXICAL = {
    "integer": re.compile(r"^[+-]?\d+$"),
    "decimal": re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$"),
    "double": re.compile(r"^([+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?|[+-]?INF|NaN)$"),
    "float": re.compile(r"^([+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?|[+-]?INF|NaN)$"),
    "boolean": re.compile(r"^(true|false|1|0)$"),
    "date": re.compile(r"^-?\d{4,}-\d{2}-\d{2}(Z|[+-]\d{2}:\d{2})?$"),
    "dateTime": re.compile(r"^-?\d{4,}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})?$"),
    "nonNegativeInteger": re.compile(r"^\+?\d+$|^-0+$"),
    "positiveInteger": re.compile(r"^\+?0*[1-9]\d*$"),
}


class Unsupported(Exception):
    """A shape uses something this validator does not implement."""


@dataclass
class Result:
    focus: str               # N-Triples form of the focus node
    severity: int
    shape: str               # source shape IRI (or N-Triples blank node)
    component: str           # sh:...ConstraintComponent IRI
    path: str | None = None  # SPARQL property path, for display
    path_ttl: str | None = None  # the same path as Turtle, for the report
    value: str | None = None     # N-Triples form of the offending value
    message: str | None = None


@dataclass
class Run:
    graph: str
    results: list[Result] = field(default_factory=list)
    # (shape, component, severity) -> results found (all of them, also those not kept)
    counts: dict[tuple[str, str, int], int] = field(default_factory=lambda: defaultdict(int))
    totals: dict[int, int] = field(default_factory=lambda: defaultdict(int))
    focus_with_results: set[str] = field(default_factory=set)
    node_shapes: int = 0
    node_shapes_checked: int = 0
    focus_nodes: int = 0
    unsupported: dict[str, str] = field(default_factory=dict)  # shape -> why
    excluded: set[str] = field(default_factory=set)  # shapes / SPARQL constraints skipped by configuration
    partial: bool = False
    seconds: float = 0.0
    timings: dict[str, float] = field(default_factory=lambda: defaultdict(float))  # what took the time

    def slowest(self, n: int = 5) -> str:
        top = sorted(self.timings.items(), key=lambda kv: -kv[1])[:n]
        return ", ".join(f"{_local(k)} {v:.0f}s" for k, v in top)

    @property
    def conforms(self) -> bool:
        return not self.totals

    def kept(self) -> int:
        return len(self.results)

    def note(self) -> str:
        parts = []
        if self.partial:
            parts.append(f"time budget reached after {self.node_shapes_checked} of {self.node_shapes} node shapes")
        if self.unsupported:
            parts.append(f"{len(self.unsupported)} constraint(s) not checked (unsupported)")
        if self.excluded:
            parts.append(f"{len(self.excluded)} rule(s) left out by configuration: "
                         + ", ".join(sorted(_local(x) for x in self.excluded)))
        stored = self.kept()
        total = sum(self.totals.values())
        if stored < total:
            parts.append(f"{stored} of {total} results kept")
        return "; ".join(parts)


@dataclass
class Limits:
    deadline: float | None = None          # time.time() after which the run stops
    max_per_constraint: int = 10000        # results kept per (shape, component)
    max_total: int = 200000                # results kept per graph
    query_timeout: float = 900             # seconds one SPARQL constraint or target may run
    # IRIs (or regexes) of node shapes, property shapes or named sh:sparql constraints to skip
    exclude: list[str] = field(default_factory=list)


def nt(term) -> str:
    """N-Triples form of a term (blank nodes rewritten by the mirror stay IRIs)."""
    return str(term)


def _local(iri: str) -> str:
    return re.split(r"[#/]", iri.rstrip("/"))[-1]


class ShapesGraph:
    """Read-only index of the shapes graph."""

    def __init__(self, triples):
        self.spo: dict = defaultdict(lambda: defaultdict(list))
        for t in triples:
            self.spo[t.subject][t.predicate.value].append(t.object)

    def objs(self, s, p: str) -> list:
        d = self.spo.get(s)
        return d.get(p, []) if d else []

    def one(self, s, p: str):
        o = self.objs(s, p)
        return o[0] if o else None

    def lit(self, s, p: str) -> str | None:
        o = self.one(s, p)
        return o.value if isinstance(o, ox.Literal) else None

    def preds(self, s) -> set[str]:
        d = self.spo.get(s)
        return set(d) if d else set()

    def subjects(self, p: str, o) -> list:
        return [s for s, d in self.spo.items() if o in d.get(p, [])]

    def rdf_list(self, node) -> list:
        out, seen = [], set()
        while node is not None and not (isinstance(node, ox.NamedNode) and node.value == RDF + "nil"):
            if node in seen:
                raise Unsupported("cyclic RDF list")
            seen.add(node)
            first = self.one(node, RDF + "first")
            if first is None:
                raise Unsupported("malformed RDF list")
            out.append(first)
            node = self.one(node, RDF + "rest")
        return out

    def message(self, s) -> str | None:
        vals = [o for o in self.objs(s, SH + "message") if isinstance(o, ox.Literal)]
        for o in vals:
            if o.language in (None, "", "en"):
                return o.value
        return vals[0].value if vals else None

    def path(self, node) -> tuple[str, str]:
        """SHACL path node -> (SPARQL property path, Turtle)."""
        if isinstance(node, ox.NamedNode) and not node.value.startswith(SKOLEM):
            return f"<{node.value}>", f"<{node.value}>"
        if isinstance(node, ox.Literal):
            raise Unsupported("literal used as a path")
        if self.one(node, RDF + "first") is not None:
            items = [self.path(n) for n in self.rdf_list(node)]
            return "(" + "/".join(i[0] for i in items) + ")", "( " + " ".join(i[1] for i in items) + " )"
        for pred, fmt in (("inversePath", "^{}"), ("zeroOrMorePath", "{}*"), ("oneOrMorePath", "{}+"),
                          ("zeroOrOnePath", "{}?")):
            inner = self.one(node, SH + pred)
            if inner is not None:
                sp, tt = self.path(inner)
                if not sp.startswith("(") and not sp.startswith("<"):
                    sp = f"({sp})"
                return fmt.format(sp), f"[ sh:{pred} {tt} ]"
        alt = self.one(node, SH + "alternativePath")
        if alt is not None:
            items = [self.path(n) for n in self.rdf_list(alt)]
            return "(" + "|".join(i[0] for i in items) + ")", "[ sh:alternativePath ( " + " ".join(i[1] for i in items) + " ) ]"
        raise Unsupported("unknown SHACL path")


@dataclass
class PropShape:
    node: object
    iri: str
    path: str
    path_ttl: str
    simple: str | None
    severity: int
    message: str | None


@dataclass
class NodeShape:
    node: object
    iri: str
    severity: int
    message: str | None
    targets: list[tuple[str, object]]
    props: list[PropShape]


def _shape_id(node) -> str:
    return node.value if isinstance(node, ox.NamedNode) else nt(node)


def _severity(g: ShapesGraph, node) -> int:
    s = g.one(node, SH + "severity")
    return SEVERITY.get(s.value, VIOLATION) if isinstance(s, ox.NamedNode) else VIOLATION


def _deactivated(g: ShapesGraph, node) -> bool:
    return g.lit(node, SH + "deactivated") in ("true", "1")


def parse_shapes(g: ShapesGraph) -> tuple[list[NodeShape], dict[str, str]]:
    """Node shapes that have targets, with their property shapes. Returns (shapes, unsupported)."""
    unsupported: dict[str, str] = {}
    target_preds = [SH + p for p in ("targetClass", "targetNode", "targetSubjectsOf", "targetObjectsOf", "target")]
    nodes = []
    for s, d in g.spo.items():
        if any(p in d for p in target_preds):
            nodes.append(s)
    out = []
    for n in sorted(nodes, key=_shape_id):
        if _deactivated(g, n):
            continue
        if g.objs(n, SH + "path"):
            unsupported[_shape_id(n)] = "property shape with its own target"
            continue
        targets: list[tuple[str, object]] = []
        for c in g.objs(n, SH + "targetClass"):
            targets.append(("class", c))
        for c in g.objs(n, SH + "targetNode"):
            targets.append(("node", c))
        for c in g.objs(n, SH + "targetSubjectsOf"):
            targets.append(("subjectsOf", c))
        for c in g.objs(n, SH + "targetObjectsOf"):
            targets.append(("objectsOf", c))
        for t in g.objs(n, SH + "target"):
            if g.lit(t, SH + "select"):
                targets.append(("sparql", t))
            else:
                unsupported[_shape_id(n)] = "custom target type"
        props = []
        for p in g.objs(n, SH + "property"):
            if _deactivated(g, p):
                continue
            pn = g.one(p, SH + "path")
            if pn is None:
                unsupported[_shape_id(p)] = "property shape without sh:path"
                continue
            try:
                sp, tt = g.path(pn)
            except Unsupported as e:
                unsupported[_shape_id(p)] = str(e)
                continue
            simple = pn.value if isinstance(pn, ox.NamedNode) and not pn.value.startswith(SKOLEM) else None
            props.append(PropShape(p, _shape_id(p), sp, tt, simple,
                                   _severity(g, p), g.message(p)))
        out.append(NodeShape(n, _shape_id(n), _severity(g, n), g.message(n), targets, props))
    return out, unsupported


# ---------------------------------------------------------------- value checks

def _num(lit: ox.Literal) -> Decimal | None:
    dt = lit.datatype.value
    if not dt.startswith(XSD) or dt[len(XSD):] not in _NUMERIC:
        return None
    try:
        return Decimal(lit.value.strip())
    except InvalidOperation:
        return None


def _compare(a, b) -> int | None:
    """Compare two literals the way sh:minInclusive & co. do; None when incomparable."""
    if not isinstance(a, ox.Literal) or not isinstance(b, ox.Literal):
        return None
    x, y = _num(a), _num(b)
    if x is not None and y is not None:
        if x.is_nan() or y.is_nan():
            return None
        return (x > y) - (x < y)
    if a.datatype == b.datatype and a.datatype.value in (XSD + "date", XSD + "dateTime", XSD + "string",
                                                        XSD + "time", XSD + "gYear"):
        return (a.value > b.value) - (a.value < b.value)
    return None


def _is_blank(t) -> bool:
    return isinstance(t, ox.BlankNode) or (isinstance(t, ox.NamedNode) and t.value.startswith(SKOLEM))


def _node_kind_ok(t, kind: str) -> bool:
    k = _local(kind)
    blank, lit = _is_blank(t), isinstance(t, ox.Literal)
    iri = not blank and not lit
    return {"IRI": iri, "BlankNode": blank, "Literal": lit, "BlankNodeOrIRI": blank or iri,
            "BlankNodeOrLiteral": blank or lit, "IRIOrLiteral": iri or lit}.get(k, True)


def _well_formed(lit: ox.Literal) -> bool:
    dt = lit.datatype.value
    rx = _LEXICAL.get(dt[len(XSD):]) if dt.startswith(XSD) else None
    return rx is None or bool(rx.match(lit.value.strip() if dt[len(XSD):] in _NUMERIC else lit.value))


_FLAGS = {"i": re.IGNORECASE, "m": re.MULTILINE, "s": re.DOTALL, "x": re.VERBOSE}


class Checker:
    """Value-level constraints, with class membership looked up in the whole mirror."""

    def __init__(self, g: ShapesGraph, store: ox.Store, vocab_graphs: list[str]):
        self.g = g
        self.store = store
        self._types: dict = {}
        self._regex: dict = {}
        self._supers: dict[str, set[str]] = {}
        self._direct: dict[str, set[str]] = defaultdict(set)
        sub = ox.NamedNode(RDFS + "subClassOf")
        for vg in vocab_graphs:
            for q in store.quads_for_pattern(None, sub, None, ox.NamedNode(vg)):
                if isinstance(q.object, ox.NamedNode):
                    self._direct[q.subject.value].add(q.object.value)

    def supers(self, c: str) -> set[str]:
        if c not in self._supers:
            self._supers[c] = {c}
            todo, seen = [c], {c}
            while todo:
                for s in self._direct.get(todo.pop(), ()):
                    if s not in seen:
                        seen.add(s)
                        todo.append(s)
            self._supers[c] = seen
        return self._supers[c]

    def subclasses(self, c: str) -> set[str]:
        out = {c}
        for s in list(self._direct):
            if c in self.supers(s):
                out.add(s)
        return out

    def types(self, t) -> set[str]:
        if t not in self._types:
            if len(self._types) > 500000:
                self._types.clear()
            self._types[t] = {q.object.value for q in self.store.quads_for_pattern(t, ox.NamedNode(RDF_TYPE), None)
                              if isinstance(q.object, ox.NamedNode)}
        return self._types[t]

    def is_instance(self, t, cls: str) -> bool:
        if isinstance(t, ox.Literal):
            return False
        return any(cls in self.supers(ty) for ty in self.types(t))

    def regex(self, pattern: str, flags: str | None):
        key = (pattern, flags)
        if key not in self._regex:
            f = 0
            for ch in flags or "":
                f |= _FLAGS.get(ch, 0)
            try:
                self._regex[key] = re.compile(pattern, f)
            except re.error as e:
                raise Unsupported(f"pattern {pattern!r}: {e}") from e
        return self._regex[key]

    def value_only(self, shape) -> bool:
        """Does this shape only constrain the value node itself?"""
        preds = {p[len(SH):] for p in self.g.preds(shape) if p.startswith(SH)}
        return not (preds & {"property", "sparql", "closed", "path"}) and all(
            p in _VALUE_PARAMS or p in _IGNORED for p in preds)

    def conforms(self, shape, v) -> bool:
        if not self.value_only(shape):
            raise Unsupported("nested shape constrains more than the value itself")
        return not self.check(shape, v)

    def check(self, shape, v) -> list[str]:
        """Failed value-level constraint components of `shape` for value `v`."""
        g, fails = self.g, []
        for c in g.objs(shape, SH + "class"):
            if isinstance(c, ox.NamedNode) and not self.is_instance(v, c.value):
                fails.append("Class")
                break
        dt = g.one(shape, SH + "datatype")
        if isinstance(dt, ox.NamedNode):
            if not (isinstance(v, ox.Literal) and v.datatype == dt and _well_formed(v)):
                fails.append("Datatype")
        nk = g.one(shape, SH + "nodeKind")
        if isinstance(nk, ox.NamedNode) and not _node_kind_ok(v, nk.value):
            fails.append("NodeKind")
        for pat in g.objs(shape, SH + "pattern"):
            if _is_blank(v) or not self.regex(pat.value, g.lit(shape, SH + "flags")).search(v.value):
                fails.append("Pattern")
                break
        for name, ok in (("minInclusive", lambda r: r >= 0), ("maxInclusive", lambda r: r <= 0),
                         ("minExclusive", lambda r: r > 0), ("maxExclusive", lambda r: r < 0)):
            b = g.one(shape, SH + name)
            if b is not None:
                r = _compare(v, b)
                if r is None or not ok(r):
                    fails.append(name[0].upper() + name[1:])
        for name, ok in (("minLength", lambda n, m: n >= m), ("maxLength", lambda n, m: n <= m)):
            b = g.lit(shape, SH + name)
            if b is not None:
                if _is_blank(v) or not ok(len(v.value), int(b)):
                    fails.append(name[0].upper() + name[1:])
        lst = g.one(shape, SH + "in")
        if lst is not None and v not in g.rdf_list(lst):
            fails.append("In")
        for s in g.objs(shape, SH + "not"):
            if self.conforms(s, v):
                fails.append("Not")
        for lst in g.objs(shape, SH + "and"):
            if not all(self.conforms(s, v) for s in g.rdf_list(lst)):
                fails.append("And")
        for lst in g.objs(shape, SH + "or"):
            if not any(self.conforms(s, v) for s in g.rdf_list(lst)):
                fails.append("Or")
        for lst in g.objs(shape, SH + "xone"):
            if sum(1 for s in g.rdf_list(lst) if self.conforms(s, v)) != 1:
                fails.append("Xone")
        for s in g.objs(shape, SH + "node"):
            if not self.conforms(s, v):
                fails.append("Node")
        return [SH + f + "ConstraintComponent" for f in fails]


# ---------------------------------------------------------------- the run

class _Deadline(Exception):
    pass


class QueryTimeout(Exception):
    pass


def _pack(t):
    if t is None:
        return None
    if isinstance(t, ox.NamedNode):
        return ("i", t.value)
    if isinstance(t, ox.BlankNode):
        return ("b", t.value)
    if isinstance(t, ox.Literal):
        return ("l", t.value, t.datatype.value, t.language)
    return ("l", str(t), XSD + "string", None)


def _unpack(t):
    if t is None:
        return None
    if t[0] == "i":
        return ox.NamedNode(t[1])
    if t[0] == "b":
        return ox.BlankNode(t[1])
    if t[3]:
        return ox.Literal(t[1], language=t[3])
    return ox.Literal(t[1], datatype=ox.NamedNode(t[2]))


def _send(f, obj) -> None:
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    f.write(struct.pack("<Q", len(data)) + data)
    f.flush()


def _recv(f):
    head = f.read(8)
    if len(head) < 8:
        raise EOFError
    (n,) = struct.unpack("<Q", head)
    data = f.read(n)
    if len(data) < n:
        raise EOFError
    return pickle.loads(data)


def _serve(path: str) -> None:  # pragma: no cover - runs in the child process
    """Child process (python -m atlas.shacl PATH): answer queries on stdin until EOF."""
    try:
        store = ox.Store.read_only(path)
    except Exception:
        store = ox.Store(path)
    fin, fout = sys.stdin.buffer, sys.stdout.buffer
    while True:
        try:
            query, max_rows = _recv(fin)
        except EOFError:
            return
        try:
            res = store.query(query)
            names = [v.value for v in res.variables]
            rows = []
            for sol in res:
                rows.append(tuple(_pack(sol[n]) for n in names))
                if len(rows) >= max_rows:
                    break
            _send(fout, ("ok", names, rows))
        except Exception as e:  # syntax errors & co. go back to the parent
            _send(fout, ("error", type(e).__name__, str(e)))


class RemoteQueries:
    """Runs SPARQL on the scratch store in a child process, so that a query that runs
    away (no row for minutes, e.g. a large self-join or a GROUP BY) can be killed."""

    def __init__(self, path: str, max_rows: int = 2_000_000):
        self.path = path
        self.max_rows = max_rows
        self.proc: subprocess.Popen | None = None

    def _start(self):
        self.proc = subprocess.Popen([sys.executable, "-m", "atlas.shacl", self.path],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def query(self, sparql: str, timeout: float) -> tuple[list[str], list[tuple]]:
        if self.proc is None or self.proc.poll() is not None:
            self._start()
        try:
            _send(self.proc.stdin, (sparql, self.max_rows))
            ready, _, _ = select.select([self.proc.stdout], [], [], max(timeout, 0.1))
            if not ready:
                self.kill()
                raise QueryTimeout(f"query timed out after {timeout:.0f}s")
            msg = _recv(self.proc.stdout)
        except (EOFError, BrokenPipeError) as e:
            self.kill()
            raise OSError("query process died (out of memory?)") from e
        if msg[0] == "error":
            raise (SyntaxError if msg[1] == "SyntaxError" else OSError)(msg[2])
        return msg[1], msg[2]

    def kill(self):
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait(10)
        self.proc = None

    def close(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.stdin.close()
                self.proc.wait(10)
            except (OSError, subprocess.TimeoutExpired):
                pass
        self.kill()


def _values_clause(var: str, iris) -> str:
    return f"VALUES ?{var} {{ " + " ".join(f"<{i}>" for i in sorted(iris)) + " }"


def _safe_iri(i: str) -> bool:
    return not any(c in i for c in '<>" {}|\\^`') and bool(i)


class Validator:
    """Validates one provider graph.

    SPARQL constraints and targets need the vocabularies next to the data. Oxigraph
    evaluates a default graph made of several named graphs many times slower than a
    single one, so with `scratch` the provider graph and the vocabularies are first
    copied into the default graph of a temporary store (on disk under `scratch`, or
    in memory when it is ""), which pays for itself after a few queries.
    """

    def __init__(self, store: ox.Store, graph: str, shapes_graph: str, vocab_graphs: list[str], limits: Limits,
                 scratch: str | None = None):
        self.store = store
        self.graph = graph
        self.shapes_graph = shapes_graph
        self.limits = limits
        self.g = ShapesGraph(store.quads_for_pattern(None, None, None, ox.NamedNode(shapes_graph)))
        self.vocab = [v for v in vocab_graphs if v != shapes_graph and store.contains_named_graph(ox.NamedNode(v))]
        self.data_dg = [ox.NamedNode(graph)]
        self.union_dg = [ox.NamedNode(graph)] + [ox.NamedNode(v) for v in self.vocab]
        self.checker = Checker(self.g, store, self.vocab)
        self.scratch_dir = scratch
        self.sstore: ox.Store | None = None
        self.remote: RemoteQueries | None = None
        self._seed: str | None = None
        self.run = Run(graph=graph)
        self._seen: set[tuple] = set()
        self._kept: dict[tuple[str, str], int] = defaultdict(int)

    # ---- helpers
    def _tick(self):
        if self.limits.deadline is not None and time.time() > self.limits.deadline:
            raise _Deadline()

    def _build_scratch(self):
        t0 = time.time()
        self.sstore = ox.Store(self.scratch_dir) if self.scratch_dir else ox.Store()
        for g in self.union_dg:
            if self.scratch_dir:
                f = f"{self.scratch_dir}.nt"
                try:
                    self.store.dump(f, ox.RdfFormat.N_TRIPLES, from_graph=g)
                    self.sstore.bulk_load(path=f, format=ox.RdfFormat.N_TRIPLES)
                finally:
                    if os.path.exists(f):
                        os.unlink(f)
            else:
                self.sstore.extend(ox.Quad(q.subject, q.predicate, q.object)
                                   for q in self.store.quads_for_pattern(None, None, None, g))
        self.run.timings["(copy to scratch store)"] += time.time() - t0
        if self.scratch_dir:
            # Queries on the scratch copy run in a child process that can be killed on timeout.
            self.sstore.flush()
            self.sstore = None
            self.remote = RemoteQueries(self.scratch_dir)

    def _query(self, sparql: str, union: bool):
        if union and self.remote is not None:
            left = (self.limits.deadline - time.time()) if self.limits.deadline is not None else 1e9
            timeout = min(left, self.limits.query_timeout)
            if timeout <= 0:
                raise _Deadline()
            names, rows = self.remote.query(sparql, timeout)
            for i, row in enumerate(rows):
                if i % 2000 == 0:
                    self._tick()
                yield {n: _unpack(v) for n, v in zip(names, row)}
            return
        if union and self.sstore is not None:
            res = self.sstore.query(sparql)
        else:
            res = self.store.query(sparql, default_graph=self.union_dg if union else self.data_dg)
        names = [v.value for v in res.variables]
        for i, sol in enumerate(res):
            if i % 2000 == 0:
                self._tick()
            yield {n: sol[n] for n in names}

    def _excluded(self, iri: str) -> bool:
        if any(e == iri or (len(e) > 2 and re.search(e, iri)) for e in self.limits.exclude):
            self.run.excluded.add(iri)
            return True
        return False

    def _appears(self, t) -> bool:
        g = ox.NamedNode(self.graph)
        if next(iter(self.store.quads_for_pattern(t, None, None, g)), None) is not None:
            return True
        if isinstance(t, ox.NamedNode) and next(iter(self.store.quads_for_pattern(None, t, None, g)), None) is not None:
            return True
        return next(iter(self.store.quads_for_pattern(None, None, t, g)), None) is not None

    def add(self, focus, shape: str, component: str, severity: int, message: str | None,
            path: str | None = None, path_ttl: str | None = None, value=None):
        f = nt(focus)
        v = nt(value) if value is not None else None
        key = (f, shape, component, v, path)
        if key in self._seen:
            return
        self._seen.add(key)
        r = self.run
        r.counts[(shape, component, severity)] += 1
        r.totals[severity] += 1
        r.focus_with_results.add(f)
        kk = (shape, component)
        if self._kept[kk] < self.limits.max_per_constraint and len(r.results) < self.limits.max_total:
            self._kept[kk] += 1
            r.results.append(Result(f, severity, shape, component, path, path_ttl, v,
                                    message or _default_message(component, path)))

    # ---- targets
    def class_targets(self, ns: NodeShape) -> set[str] | None:
        """Classes (with subclasses) when every target of the shape is a class target."""
        if not ns.targets or any(k != "class" for k, _ in ns.targets):
            return None
        out: set[str] = set()
        for _, c in ns.targets:
            if isinstance(c, ox.NamedNode):
                out |= self.checker.subclasses(c.value)
        return {c for c in out if _safe_iri(c)}

    def focus_nodes(self, ns: NodeShape) -> dict[str, object]:
        out: dict[str, object] = {}
        for kind, t in ns.targets:
            if kind == "class":
                if not isinstance(t, ox.NamedNode):
                    continue
                classes = [c for c in self.checker.subclasses(t.value) if _safe_iri(c)]
                q = f"SELECT DISTINCT ?this WHERE {{ {_values_clause('t', classes)} ?this a ?t }}"
                rows = self._query(q, union=False)
            elif kind == "node":
                rows = [{"this": t}] if self._appears(t) else []
            elif kind in ("subjectsOf", "objectsOf"):
                if not isinstance(t, ox.NamedNode):
                    continue
                pat = f"?this <{t.value}> ?o" if kind == "subjectsOf" else f"?s <{t.value}> ?this"
                rows = self._query(f"SELECT DISTINCT ?this WHERE {{ {pat} }}", union=False)
            else:  # SPARQL-based target
                q = self._sparql_text(t, None, ns.iri)
                rows = (r for r in self._query(q, union=True) if r.get("this") is not None and self._appears(r["this"]))
            for r in rows:
                x = r.get("this")
                if x is not None:
                    out.setdefault(nt(x), x)
        return out

    # ---- value fetching
    def fetch(self, classes: set[str] | None, props: list[PropShape]) -> dict[str, dict[str, list]]:
        """path -> focus (N-Triples) -> distinct values, for the given property shapes."""
        out: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        restrict = f"{_values_clause('t', classes)} ?this a ?t ." if classes else ""
        simple = sorted({p.simple for p in props if p.simple and _safe_iri(p.simple)})
        for i in range(0, len(simple), 40):
            chunk = simple[i:i + 40]
            q = f"SELECT ?this ?p ?v WHERE {{ {restrict} {_values_clause('p', chunk)} ?this ?p ?v }}"
            for r in self._query(q, union=False):
                lst = out[f"<{r['p'].value}>"][nt(r["this"])]
                if r["v"] not in lst:
                    lst.append(r["v"])
        for path in sorted({p.path for p in props if not p.simple}):
            q = f"SELECT DISTINCT ?this ?v WHERE {{ {restrict} ?this {path} ?v }}"
            for r in self._query(q, union=False):
                lst = out[path][nt(r["this"])]
                if r["v"] not in lst:
                    lst.append(r["v"])
        return out

    # ---- constraints
    def check_property(self, ps: PropShape, focus: dict[str, object], vals: dict[str, list],
                       other: dict[str, dict[str, list]]):
        g = self.g
        n = ps.node
        add = lambda f, comp, value=None: self.add(f, ps.iri, SH + comp + "ConstraintComponent", ps.severity,  # noqa: E731
                                                   ps.message, ps.path, ps.path_ttl, value)
        mn, mx = g.lit(n, SH + "minCount"), g.lit(n, SH + "maxCount")
        unique_lang = g.lit(n, SH + "uniqueLang") in ("true", "1")
        has_value = g.objs(n, SH + "hasValue")
        q_shape = g.one(n, SH + "qualifiedValueShape")
        q_min, q_max = g.lit(n, SH + "qualifiedMinCount"), g.lit(n, SH + "qualifiedMaxCount")
        pairs = [(c, g.one(n, SH + c)) for c in ("equals", "disjoint", "lessThan", "lessThanOrEquals")]
        pairs = [(c, f"<{p.value}>") for c, p in pairs if isinstance(p, ox.NamedNode)]
        value_level = any(g.objs(n, SH + p) for p in _VALUE_PARAMS - {"hasValue"})
        for i, (fk, f) in enumerate(focus.items()):
            if i % 5000 == 0:
                self._tick()
            vs = vals.get(fk, [])
            if mn is not None and len(vs) < int(mn):
                add(f, "MinCount")
            if mx is not None and len(vs) > int(mx):
                add(f, "MaxCount")
            for hv in has_value:
                if hv not in vs:
                    add(f, "HasValue")
            if unique_lang:
                langs = [v.language for v in vs if isinstance(v, ox.Literal) and v.language]
                if len(langs) != len(set(langs)):
                    add(f, "UniqueLang")
            for comp, p2 in pairs:
                ws = other.get(p2, {}).get(fk, [])
                if comp == "equals":
                    for v in set(vs) ^ set(ws):
                        add(f, "Equals", v)
                elif comp == "disjoint":
                    for v in set(vs) & set(ws):
                        add(f, "Disjoint", v)
                else:
                    for v in vs:
                        for w in ws:
                            r = _compare(v, w)
                            if r is None or r > 0 or (comp == "lessThan" and r == 0):
                                add(f, comp[0].upper() + comp[1:], v)
            if q_shape is not None and (q_min is not None or q_max is not None):
                k = sum(1 for v in vs if self.checker.conforms(q_shape, v))
                if q_min is not None and k < int(q_min):
                    add(f, "QualifiedMinCount")
                if q_max is not None and k > int(q_max):
                    add(f, "QualifiedMaxCount")
            if value_level:
                for v in vs:
                    for comp in self.checker.check(n, v):
                        self.add(f, ps.iri, comp, ps.severity, ps.message, ps.path, ps.path_ttl, v)

    def check_closed(self, ns: NodeShape, focus: dict[str, object], classes: set[str] | None):
        g = self.g
        allowed = {p.simple for p in ns.props if p.simple}
        for lst in g.objs(ns.node, SH + "ignoredProperties"):
            allowed |= {x.value for x in g.rdf_list(lst) if isinstance(x, ox.NamedNode)}
        allowed = {a for a in allowed if _safe_iri(a)}
        restrict = f"{_values_clause('t', classes)} ?this a ?t ." if classes else ""
        flt = ("FILTER(?p NOT IN (" + ", ".join(f"<{a}>" for a in sorted(allowed)) + "))") if allowed else ""
        for r in self._query(f"SELECT DISTINCT ?this ?p ?v WHERE {{ {restrict} ?this ?p ?v {flt} }}", union=False):
            if nt(r["this"]) in focus:
                self.add(r["this"], ns.iri, SH + "ClosedConstraintComponent", ns.severity, ns.message,
                         f"<{r['p'].value}>", f"<{r['p'].value}>", r["v"])

    def _sparql_text(self, node, path: str | None, shape: str) -> str:
        g = self.g
        sel = g.lit(node, SH + "select")
        if not sel:
            raise Unsupported("SPARQL constraint without sh:select")
        decls = []
        for p in g.objs(node, SH + "prefixes"):
            for d in g.objs(p, SH + "declare"):
                pre, ns = g.lit(d, SH + "prefix"), g.lit(d, SH + "namespace")
                if pre is not None and ns:
                    decls.append(f"PREFIX {pre}: <{ns}>")
        head, body = _split_where(sel)
        if "$PATH" in sel:
            if path is None:
                raise Unsupported("$PATH in a node shape")
            head = re.sub(r"\$PATH\b", "", head)  # only meaningful in triple patterns
            body = re.sub(r"\$PATH\b", path, body)
        q = head + body
        q = re.sub(r"\$currentShape\b", f"<{shape}>" if not shape.startswith("_:") else "?currentShape", q)
        q = re.sub(r"\$shapesGraph\b", f"<{self.shapes_graph}>", q)
        return "\n".join(dict.fromkeys(decls)) + "\n" + q

    def check_sparql(self, cnode, shape: str, severity: int, shape_msg: str | None, focus: dict[str, object],
                     path: str | None, path_ttl: str | None):
        if _deactivated(self.g, cnode):
            return
        if isinstance(cnode, ox.NamedNode) and self._excluded(cnode.value):
            return
        t0 = time.time()
        try:
            self._check_sparql(cnode, shape, severity, shape_msg, focus, path, path_ttl)
        finally:
            self.run.timings[f"{shape} ({_local(_shape_id(cnode))})"] += time.time() - t0

    def _check_sparql(self, cnode, shape: str, severity: int, shape_msg: str | None, focus: dict[str, object],
                      path: str | None, path_ttl: str | None):
        q = self._sparql_text(cnode, path, shape)
        msg_tmpl = self.g.message(cnode) or shape_msg
        if self._seed:
            # Class-targeted shape: bind $this to the instances of the target classes first
            # (pre-binding in the query itself). Without it, a constraint that starts with
            # OPTIONAL { $this rdfs:label ?l } reads every label of the graph first.
            rows = self._query(_inject(q, self._seed), union=True)
        elif _binds_this(q):
            # Every ERA constraint that matches $this in a plain triple pattern also works with
            # $this unbound: one query over the whole graph, rows kept for the focus nodes.
            rows = self._query(q, union=True)
        else:
            # $this only occurs inside FILTER (NOT) EXISTS: emulate pre-binding with VALUES.
            rows = self._prebound(q, focus)
        for r in rows:
            this = r.get("this")
            if this is None or nt(this) not in focus:
                continue
            rp = r.get("path")
            p, pt = (f"<{rp.value}>", f"<{rp.value}>") if isinstance(rp, ox.NamedNode) else (path, path_ttl)
            msg = _fill(msg_tmpl, r) if msg_tmpl else None
            self.add(this, shape, SH + "SPARQLConstraintComponent", severity, msg, p, pt, r.get("value"))

    def _prebound(self, q: str, focus: dict[str, object], batch: int = 400):
        nodes = [f for f in focus.values() if isinstance(f, ox.NamedNode)]
        for i in range(0, len(nodes), batch):
            vals = " ".join(nt(n) for n in nodes[i:i + batch])
            yield from self._query(_inject(q, f"VALUES ?this {{ {vals} }}"), union=True)

    def check_node_shape(self, ns: NodeShape):
        r = self.run
        focus = self.focus_nodes(ns)
        if not focus:
            return
        r.focus_nodes += len(focus)
        classes = self.class_targets(ns)
        self._seed = (f"{_values_clause('atlasTargetType', classes)} ?this <{RDF_TYPE}> ?atlasTargetType ."
                      if classes else None)
        g = self.g
        # constraints on the focus node itself
        try:
            if any(g.objs(ns.node, SH + p) for p in _VALUE_PARAMS):
                for f in focus.values():
                    for comp in self.checker.check(ns.node, f):
                        self.add(f, ns.iri, comp, ns.severity, ns.message, value=f)
            if g.lit(ns.node, SH + "closed") in ("true", "1"):
                self.check_closed(ns, focus, classes)
        except Unsupported as e:
            r.unsupported[ns.iri] = str(e)
        props = [p for p in ns.props if not self._excluded(p.iri)]
        pair_paths = []
        for p in props:
            for c in ("equals", "disjoint", "lessThan", "lessThanOrEquals"):
                o = g.one(p.node, SH + c)
                if isinstance(o, ox.NamedNode):
                    pair_paths.append(PropShape(o, "", f"<{o.value}>", f"<{o.value}>", o.value, VIOLATION, None))
        values = self.fetch(classes, props + pair_paths)
        for p in props:
            try:
                self.check_property(p, focus, values.get(p.path, {}), values)
            except Unsupported as e:
                r.unsupported[p.iri] = str(e)
            for c in g.objs(p.node, SH + "sparql"):
                try:
                    self.check_sparql(c, p.iri, p.severity, p.message, focus, p.path, p.path_ttl)
                except (Unsupported, QueryTimeout, SyntaxError, OSError, ValueError) as e:
                    r.unsupported[p.iri] = f"SPARQL: {str(e).splitlines()[0][:200]}"
        for c in g.objs(ns.node, SH + "sparql"):
            try:
                self.check_sparql(c, ns.iri, ns.severity, ns.message, focus, None, None)
            except (Unsupported, QueryTimeout, SyntaxError, OSError, ValueError) as e:
                r.unsupported[ns.iri] = f"SPARQL: {str(e).splitlines()[0][:200]}"

    def validate(self) -> Run:
        t0 = time.time()
        shapes, unsupported = parse_shapes(self.g)
        self.run.unsupported.update(unsupported)
        shapes = [s for s in shapes if not self._excluded(s.iri)]
        self.run.node_shapes = len(shapes)
        if self.scratch_dir is not None:
            self._build_scratch()
        try:
            for ns in shapes:
                self._tick()
                t1 = time.time()
                try:
                    self.check_node_shape(ns)
                except (QueryTimeout, SyntaxError, OSError, ValueError) as e:  # a target query that fails
                    self.run.unsupported[ns.iri] = str(e).splitlines()[0][:200]
                finally:
                    self.run.timings[ns.iri] += time.time() - t1
                self.run.node_shapes_checked += 1
        except _Deadline:
            self.run.partial = True
        self.run.seconds = time.time() - t0
        return self.run


def _split_where(sel: str) -> tuple[str, str]:
    """Split a SELECT query into its projection and the rest (from WHERE or the first '{')."""
    m = re.search(r"\bWHERE\b|\{", sel, re.IGNORECASE)
    return (sel[:m.start()], sel[m.start():]) if m else (sel, "")


def _inject(q: str, pattern: str) -> str:
    """Insert `pattern` at the start of the query's WHERE clause."""
    m = re.search(r"\bWHERE\s*\{", q, re.IGNORECASE) or re.search(r"\bSELECT\b[^{]*\{", q, re.IGNORECASE)
    if not m:
        raise Unsupported("cannot find the WHERE clause to pre-bind $this")
    return q[:m.end()] + f" {pattern} " + q[m.end():]


def _strip_exists(text: str) -> str:
    """Remove the bodies of (NOT) EXISTS { ... } and MINUS { ... } groups (brace-balanced)."""
    out, i = [], 0
    rx = re.compile(r"\b(?:NOT\s+)?EXISTS\s*\{|\bMINUS\s*\{", re.IGNORECASE)
    while True:
        m = rx.search(text, i)
        if not m:
            out.append(text[i:])
            return "".join(out)
        out.append(text[i:m.start()])
        depth, j = 1, m.end()
        while j < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            j += 1
        i = j


def _binds_this(q: str) -> bool:
    """Is $this matched by a pattern outside FILTER (NOT) EXISTS / MINUS, so it can run unbound?"""
    _, body = _split_where(re.sub(r"(^|\s)#[^\n]*", r"\1", q))
    return bool(re.search(r"[?$]this\b", _strip_exists(body)))


_TEMPLATE = re.compile(r"\{[?$]([A-Za-z_][A-Za-z0-9_]*)\}")


def _fill(tmpl: str, row: dict) -> str:
    def sub(m):
        v = row.get(m.group(1))
        return m.group(0) if v is None else v.value
    return _TEMPLATE.sub(sub, tmpl)


def _default_message(component: str, path: str | None) -> str:
    c = component[len(SH):].replace("ConstraintComponent", "") if component.startswith(SH) else component
    where = f" for {path}" if path else ""
    return f"{c} constraint not satisfied{where}"


def validate_graph(store: ox.Store, graph: str, shapes_graph: str, vocab_graphs: list[str],
                   limits: Limits | None = None, scratch: str | None = None) -> Run:
    """Validate one provider graph of the mirror against the shapes in `shapes_graph`.

    `scratch`: a directory for the temporary store (created and removed here), "" for an
    in-memory one, None to query the mirror directly (slower SPARQL constraints)."""
    v = Validator(store, graph, shapes_graph, vocab_graphs, limits or Limits(), scratch)
    try:
        return v.validate()
    finally:
        if v.remote is not None:
            v.remote.close()
        v.sstore = None
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)


# ---------------------------------------------------------------- report

REPORT_PREFIXES = {
    "sh": SH, "rdf": RDF, "rdfs": RDFS, "xsd": XSD, "era": "http://data.europa.eu/949/",
    "dct": "http://purl.org/dc/terms/", "atlas": "https://rinf-atlas.matdata.eu/ns#",
}


def report_turtle(run: Run, *, report_iri: str, created: str, about: str = "") -> str:
    """The run as a standard sh:ValidationReport in Turtle (readable by shacl-report, pyshacl & co.)."""
    out = [f"@prefix {p}: <{n}> ." for p, n in REPORT_PREFIXES.items()]
    out.append("")
    if about:
        out += [f"# {line}" for line in about.splitlines()]
        out.append("")
    conforms = "true" if run.conforms else "false"
    out.append(f"<{report_iri}> a sh:ValidationReport ;")
    out.append(f"    sh:conforms {conforms} ;")
    out.append(f"    dct:created {nt(ox.Literal(created, datatype=ox.NamedNode(XSD + 'dateTime')))} ;")
    out.append(f"    atlas:dataset <{run.graph}> ;")
    note = run.note()
    if note:
        out.append(f"    rdfs:comment {nt(ox.Literal(note))} ;")
    out.append(f"    atlas:resultCount {sum(run.totals.values())} .")
    out.append("")
    for r in run.results:
        parts = ["a sh:ValidationResult", f"sh:focusNode {r.focus}", f"sh:resultSeverity sh:{_local(SEVERITY_IRI[r.severity])}",
                 f"sh:sourceShape {r.shape if r.shape.startswith('_:') else '<' + r.shape + '>'}",
                 f"sh:sourceConstraintComponent sh:{r.component[len(SH):]}" if r.component.startswith(SH)
                 else f"sh:sourceConstraintComponent <{r.component}>"]
        if r.path_ttl:
            parts.append(f"sh:resultPath {r.path_ttl}")
        if r.value is not None:
            parts.append(f"sh:value {r.value}")
        if r.message:
            parts.append(f"sh:resultMessage {nt(ox.Literal(r.message))}")
        out.append(f"<{report_iri}> sh:result [ " + " ;\n    ".join(parts) + " ] .")
    out.append("")
    return "\n".join(out)


if __name__ == "__main__":  # the query process of RemoteQueries
    _serve(sys.argv[1])
