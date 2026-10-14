"""Read the facet shapes (SHACL + atlas: annotations) from shapes/*.ttl.

See shapes/atlas.ttl for the annotation vocabulary. The result is a plain
Python model; SHACL paths are compiled to SPARQL property paths.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pyoxigraph as ox

SH = "http://www.w3.org/ns/shacl#"
ATLAS = "https://rinf-atlas.matdata.eu/ns#"
RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
XSD = "http://www.w3.org/2001/XMLSchema#"

DEFAULT_GEOMETRY_PATH = (
    "(<http://www.opengis.net/ont/geosparql#hasGeometry>/<http://www.opengis.net/ont/geosparql#asWKT>"
    "|<http://data.europa.eu/949/netReference>/<http://www.opengis.net/ont/geosparql#hasGeometry>"
    "/<http://www.opengis.net/ont/geosparql#asWKT>)"
)


@dataclass
class Facet:
    key: str
    label: str
    path: str               # SPARQL property path
    simple: str | None      # predicate IRI when the path is a single predicate
    kind: str = "terms"     # terms|range|boolean|text|link
    datatype: str | None = None
    unit: str | None = None
    order: int = 100


@dataclass
class ClassShape:
    key: str
    class_iri: str | None   # None for the shared shape
    label: str
    color: str | None = None
    geometry: str = "any"
    order: int = 100
    min_zoom: int = 0
    shared: bool = False
    label_paths: list[str] = field(default_factory=list)
    geometry_paths: list[str] = field(default_factory=list)
    line_between: tuple[str, str] | None = None
    geometry_from: list[str] = field(default_factory=list)
    facets: list[Facet] = field(default_factory=list)


@dataclass
class ShapeSet:
    classes: list[ClassShape]
    shared: list[Facet]

    def digest(self) -> str:
        blob = json.dumps([asdict(c) for c in self.classes] + [asdict(f) for f in self.shared], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def all_facets(self, cls: ClassShape) -> list[Facet]:
        seen = {f.key for f in cls.facets}
        return cls.facets + [f for f in self.shared if f.key not in seen]


class ShapeError(ValueError):
    pass


def _iri(t) -> str | None:
    return t.value if isinstance(t, ox.NamedNode) else None


class _Reader:
    def __init__(self, store: ox.Store):
        self.s = store

    def objs(self, subj, pred: str) -> list:
        return [q.object for q in self.s.quads_for_pattern(subj, ox.NamedNode(pred), None)]

    def one(self, subj, pred: str):
        o = self.objs(subj, pred)
        return o[0] if o else None

    def lit(self, subj, pred: str, default=None):
        o = self.one(subj, pred)
        return o.value if isinstance(o, ox.Literal) else default

    def label(self, subj, *preds: str) -> str | None:
        for p in preds:
            vals = [o for o in self.objs(subj, p) if isinstance(o, ox.Literal)]
            for o in vals:
                if o.language in (None, "en"):
                    return o.value
            if vals:
                return vals[0].value
        return None

    def rdf_list(self, node) -> list:
        out = []
        while node is not None and not (isinstance(node, ox.NamedNode) and node.value == RDF + "nil"):
            first = self.one(node, RDF + "first")
            if first is None:
                raise ShapeError("malformed RDF list in shapes")
            out.append(first)
            node = self.one(node, RDF + "rest")
        return out

    def path(self, node) -> str:
        """SHACL path node -> SPARQL property path."""
        if isinstance(node, ox.NamedNode):
            return f"<{node.value}>"
        if isinstance(node, ox.Literal):
            raise ShapeError(f"literal {node.value!r} used as a path")
        if self.one(node, RDF + "first") is not None:
            return "(" + "/".join(self.path(n) for n in self.rdf_list(node)) + ")"
        for pred, fmt in ((SH + "inversePath", "^{}"), (SH + "zeroOrMorePath", "{}*"),
                          (SH + "oneOrMorePath", "{}+"), (SH + "zeroOrOnePath", "{}?")):
            inner = self.one(node, pred)
            if inner is not None:
                p = self.path(inner)
                if not p.startswith("(") and not p.startswith("<"):
                    p = f"({p})"
                return fmt.format(p)
        alt = self.one(node, SH + "alternativePath")
        if alt is not None:
            return "(" + "|".join(self.path(n) for n in self.rdf_list(alt)) + ")"
        raise ShapeError("unsupported SHACL path")


def load_shapes(shapes_dir: Path) -> ShapeSet:
    st = ox.Store()
    files = sorted(shapes_dir.glob("**/*.ttl"))
    if not files:
        raise ShapeError(f"no shapes found in {shapes_dir}")
    for f in files:
        try:
            st.load(path=str(f), format=ox.RdfFormat.TURTLE)
        except SyntaxError as e:
            raise ShapeError(f"{f.name}: {e}") from e
    r = _Reader(st)

    classes: list[ClassShape] = []
    shared: list[Facet] = []
    keys: set[str] = set()
    for q in st.quads_for_pattern(None, ox.NamedNode(RDF + "type"), ox.NamedNode(SH + "NodeShape")):
        node = q.subject
        key = r.lit(node, ATLAS + "key")
        if not key:
            continue
        if r.lit(node, ATLAS + "enabled", "true") == "false":
            continue
        if key in keys:
            raise ShapeError(f"duplicate atlas:key {key!r}")
        keys.add(key)
        is_shared = r.lit(node, ATLAS + "shared", "false") == "true"
        target = _iri(r.one(node, SH + "targetClass"))
        if not is_shared and not target:
            raise ShapeError(f"shape {key!r} has no sh:targetClass")
        cs = ClassShape(
            key=key,
            class_iri=target,
            label=r.label(node, RDFS + "label", SH + "name") or key,
            color=r.lit(node, ATLAS + "color"),
            geometry=r.lit(node, ATLAS + "geometry", "any"),
            order=int(r.lit(node, ATLAS + "order", "100")),
            min_zoom=int(r.lit(node, ATLAS + "minZoom", "0")),
            shared=is_shared,
        )
        cs.label_paths = [r.path(p) for p in r.objs(node, ATLAS + "labelPath")] or [f"<{RDFS}label>"]
        cs.geometry_paths = [r.path(p) for p in r.objs(node, ATLAS + "geometryPath")]
        if not cs.geometry_paths and not is_shared:
            cs.geometry_paths = [DEFAULT_GEOMETRY_PATH]
        lb = r.one(node, ATLAS + "lineBetween")
        if lb is not None:
            pair = r.rdf_list(lb)
            if len(pair) != 2:
                raise ShapeError(f"{key}: atlas:lineBetween needs exactly two paths")
            cs.line_between = (r.path(pair[0]), r.path(pair[1]))
        cs.geometry_from = [r.path(p) for p in r.objs(node, ATLAS + "geometryFrom")]

        for prop in r.objs(node, SH + "property"):
            fkey = r.lit(prop, ATLAS + "facet")
            if not fkey:
                continue
            if r.lit(prop, ATLAS + "enabled", "true") == "false":
                continue
            pnode = r.one(prop, SH + "path")
            if pnode is None:
                raise ShapeError(f"{key}.{fkey}: missing sh:path")
            dt = _iri(r.one(prop, SH + "datatype"))
            kind = r.lit(prop, ATLAS + "kind")
            if not kind:
                kind = "boolean" if dt == XSD + "boolean" else "range" if dt and _numeric(dt) else "terms"
            if kind not in ("terms", "range", "boolean", "text", "link"):
                raise ShapeError(f"{key}.{fkey}: unknown atlas:kind {kind!r}")
            fc = Facet(
                key=fkey,
                label=r.label(prop, RDFS + "label", SH + "name") or fkey,
                path=r.path(pnode),
                simple=pnode.value if isinstance(pnode, ox.NamedNode) else None,
                kind=kind,
                datatype=dt,
                unit=r.lit(prop, ATLAS + "unit"),
                order=int(r.lit(prop, SH + "order", "100")),
            )
            (cs.facets if not is_shared else shared).append(fc)
        cs.facets.sort(key=lambda f: (f.order, f.key))
        if not is_shared:
            classes.append(cs)
    classes.sort(key=lambda c: (c.order, c.key))
    shared.sort(key=lambda f: (f.order, f.key))
    return ShapeSet(classes=classes, shared=shared)


def _numeric(dt: str) -> bool:
    return dt.startswith(XSD) and dt[len(XSD):] in {
        "integer", "int", "long", "short", "decimal", "double", "float", "nonNegativeInteger",
        "positiveInteger", "unsignedInt", "unsignedLong", "byte", "unsignedShort"}
