"""Resolved descriptions of infrastructure elements, read from the mirror.

An element's *resolved description* is every triple about it plus, recursively,
every triple about the nodes that only exist for it (its components): net
references and their geometries, validity periods, contact line systems, ETCS,
load capabilities, documents... Three kinds of object stop the recursion:

* other infrastructure elements (`era:hasPart`, `era:opStart`, ...), kept as a
  reference with its label, so that exporting one section of line does not
  pull in the whole network (``follow`` lists the predicates whose elements are
  resolved one level anyway, e.g. the tracks of a section);
* concepts and other vocabulary resources (SKOS values, countries,
  organisations), kept as a reference with the label found in the vocabulary
  graphs;
* anything without triples in the provider graph (external documents, classes).

Everything comes from one provider graph of the mirror, in `VALUES` batches of
several hundred subjects per hop, so a resolution never touches ERA.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

import pyoxigraph as ox

from .extract import RDFS_LABEL, SKOS_PREF
from .mirror import select

RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
SKOS_CONCEPT = "http://www.w3.org/2004/02/skos/core#Concept"
ERA = "http://data.europa.eu/949/"
INFRA_ELEMENT = ERA + "InfrastructureElement"
_SKOLEM = re.compile(r"^urn:atlas:bnode:([0-9a-f]+):(.+)$")
BATCH = 400
MAX_DEPTH = 8


@dataclass(slots=True)
class Lit:
    value: str
    datatype: str | None = None
    lang: str | None = None


@dataclass(slots=True)
class Ref:
    """An IRI that is not resolved further: another element, a concept, a class, an external resource."""
    iri: str
    kind: str                 # element | concept | class | external
    label: str | None = None


@dataclass(slots=True)
class Node:
    """A resolved resource: the root element or one of its components."""
    id: str                   # IRI, or the mirror's skolem IRI of a blank node
    types: list[str] = field(default_factory=list)
    props: dict[str, list] = field(default_factory=dict)   # predicate -> [Lit | Ref | Node]

    @property
    def bnode(self) -> str | None:
        """Blank node label when the resource was a blank node in the source data."""
        m = _SKOLEM.match(self.id)
        return "b" + re.sub(r"[^A-Za-z0-9]", "_", m.group(1) + "_" + m.group(2)) if m else None

    def values(self, pred: str) -> list:
        return self.props.get(pred, [])

    def walk(self):
        """This node and every component node below it, each once, depth first."""
        seen: set[str] = set()
        stack = [self]
        while stack:
            n = stack.pop()
            if n.id in seen:
                continue
            seen.add(n.id)
            yield n
            for vals in n.props.values():
                for v in vals:
                    if isinstance(v, Node):
                        stack.append(v)


def _safe(iri: str) -> bool:
    return bool(iri) and not any(c in iri for c in '<>" {}|\\^`\n')


def _values(var: str, iris: Iterable[str]) -> str:
    return f"VALUES ?{var} {{ " + " ".join(f"<{i}>" for i in iris if _safe(i)) + " }"


def element_classes(store: ox.Store, extra: Iterable[str] = ()) -> set[str]:
    """era:InfrastructureElement and its subclasses in the mirrored ontology, plus `extra`
    (the shaped classes, so the result is usable before the ontology is mirrored)."""
    out = set(extra) | {INFRA_ELEMENT}
    q = f"SELECT ?c WHERE {{ ?c <http://www.w3.org/2000/01/rdf-schema#subClassOf>* <{INFRA_ELEMENT}> }}"
    try:
        out |= {r["c"].value for r in select(store, q, union=True) if isinstance(r["c"], ox.NamedNode)}
    except Exception:  # noqa: BLE001 - an empty mirror has no ontology; the shaped classes still work
        pass
    return out


def vocab_labels(store: ox.Store, iris: Iterable[str]) -> dict[str, str]:
    """skos:prefLabel / rdfs:label of resources anywhere in the mirror (English first)."""
    todo = [i for i in sorted(set(iris)) if _safe(i)]
    found: dict[str, tuple[int, str]] = {}
    for i in range(0, len(todo), BATCH):
        q = f"SELECT ?i ?p ?l WHERE {{ {_values('i', todo[i:i + BATCH])} VALUES ?p {{ <{SKOS_PREF}> <{RDFS_LABEL}> }} ?i ?p ?l }}"
        for r in select(store, q, union=True):
            lit = r["l"]
            if not isinstance(lit, ox.Literal):
                continue
            rank = (0 if r["p"].value == SKOS_PREF else 2) + (0 if lit.language in ("en", None) else 1)
            iri = r["i"].value
            if iri not in found or rank < found[iri][0]:
                found[iri] = (rank, lit.value)
    return {k: v for k, (_, v) in found.items()}


class Resolver:
    """Resolves elements of one provider graph.

    ``elements``: class IRIs whose instances are infrastructure elements (references, not
    components). ``element_labels(iris)`` returns labels for referenced elements (the index
    knows them); ``follow``: predicates whose element objects are resolved one level deep
    (their own element links are then references)."""

    def __init__(self, store: ox.Store, graph: str, elements: set[str],
                 element_labels: Callable[[set[str]], dict[str, str]] | None = None,
                 follow: Iterable[str] = (), max_depth: int = MAX_DEPTH):
        self.store = store
        self.graph = graph
        self.dg = [ox.NamedNode(graph)]
        self.elements = elements
        self.element_labels = element_labels or (lambda iris: {})
        self.follow = set(follow)
        self.max_depth = max_depth
        self._label_cache: dict[str, str | None] = {}

    def _q(self, sparql: str):
        res = self.store.query(sparql, default_graph=self.dg)
        names = [v.value for v in res.variables]
        for sol in res:
            yield {n: sol[n] for n in names}

    def _triples(self, subjects: list[str]) -> dict[str, list[tuple[str, object]]]:
        out: dict[str, list[tuple[str, object]]] = {s: [] for s in subjects}
        for i in range(0, len(subjects), BATCH):
            q = f"SELECT ?s ?p ?o WHERE {{ {_values('s', subjects[i:i + BATCH])} ?s ?p ?o }}"
            for r in self._q(q):
                out[r["s"].value].append((r["p"].value, r["o"]))
        return out

    def _types(self, iris: list[str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for i in range(0, len(iris), BATCH):
            q = f"SELECT ?o ?t WHERE {{ {_values('o', iris[i:i + BATCH])} ?o <{RDF_TYPE}> ?t }}"
            for r in self._q(q):
                if isinstance(r["t"], ox.NamedNode):
                    out.setdefault(r["o"].value, []).append(r["t"].value)
        return out

    def resolve(self, roots: list[str]) -> dict[str, Node]:
        """Resolved description of each root IRI (roots without triples get an empty Node)."""
        nodes: dict[str, Node] = {}
        refs: dict[str, Ref] = {}   # IRIs referenced but not resolved, labelled at the end
        frontier = [r for r in roots if _safe(r)]
        followed: set[str] = set()   # elements resolved through `follow`
        depth = 0
        while frontier and depth <= self.max_depth:
            triples = self._triples(frontier)
            objs: set[str] = set()
            for s in frontier:
                node = nodes.setdefault(s, Node(id=s))
                for p, o in triples[s]:
                    if isinstance(o, ox.Literal):
                        v = Lit(o.value, None if o.language else o.datatype.value, o.language)
                    elif isinstance(o, ox.NamedNode):
                        if p == RDF_TYPE:
                            node.types.append(o.value)
                            continue
                        v = o.value
                        objs.add(v)
                    else:
                        continue  # a raw blank node (the mirror skolemises them; none expected)
                    node.props.setdefault(p, []).append(v)
            types = self._types([o for o in objs if o not in nodes])
            for o in objs:
                if o in nodes:
                    types[o] = nodes[o].types
            nxt: list[str] = []
            for s in frontier:
                node = nodes[s]
                for p, vals in node.props.items():
                    for v in vals:
                        if not isinstance(v, str):
                            continue
                        t = types.get(v, [])
                        is_element = any(c in self.elements for c in t)
                        if is_element and v not in followed and not (depth == 0 and p in self.follow):
                            # Another element (a root of this batch included): a reference, never embedded.
                            refs.setdefault(v, Ref(v, "element"))
                            continue
                        if v in nodes or v in nxt:
                            continue  # resolved already, or queued
                        if not t and depth > 0 and not _SKOLEM.match(v):
                            # Untyped IRI below the root: a document URL, a concept... Its triples (if any)
                            # live in the vocabularies, not here; resolved as a reference.
                            refs.setdefault(v, Ref(v, "external"))
                            continue
                        nxt.append(v)
                        if is_element:
                            followed.add(v)
            frontier = nxt
            depth += 1
        for v in frontier:   # depth limit reached: leave as references
            refs.setdefault(v, Ref(v, "external"))
        # Link: strings -> Node or Ref.
        for node in nodes.values():
            for p, vals in node.props.items():
                for i, v in enumerate(vals):
                    if isinstance(v, str):
                        if v in refs and refs[v].kind == "element":
                            vals[i] = refs[v]
                        elif v in nodes:
                            vals[i] = nodes[v]
                        else:
                            vals[i] = refs.setdefault(v, Ref(v, "external"))
        # Nodes resolved but without triples are external references after all.
        for node in list(nodes.values()):
            if not node.props and not node.types and node.id not in roots:
                r = Ref(node.id, "external")
                refs[node.id] = r
                for other in nodes.values():
                    for vals in other.props.values():
                        for i, v in enumerate(vals):
                            if v is node:
                                vals[i] = r
                del nodes[node.id]
        self._label(refs)
        return {r: nodes.get(r, Node(id=r)) for r in roots}

    def _label(self, refs: dict[str, Ref]) -> None:
        elems = {i for i, r in refs.items() if r.kind == "element"}
        others = {i for i, r in refs.items() if r.kind != "element"}
        labels = dict(self.element_labels(elems)) if elems else {}
        todo = [i for i in others if i not in self._label_cache]
        if todo:
            found = vocab_labels(self.store, todo)
            for i in todo:
                self._label_cache[i] = found.get(i)
        for i, r in refs.items():
            if r.kind == "element":
                r.label = labels.get(i)
            else:
                r.label = self._label_cache.get(i)
                if r.label is not None and r.kind == "external":
                    r.kind = "concept"
