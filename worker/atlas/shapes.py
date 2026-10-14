"""Read the facet shapes (SHACL + atlas: annotations) from shapes/*.ttl.

See shapes/atlas.ttl for the annotation vocabulary. The result is a plain
Python model; SHACL paths are compiled to SPARQL property paths.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
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
    kind: str = "terms"     # terms|range|boolean|text
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


# Bumped when extraction changes what it writes for the same shapes (forces one
# reindex from the mirror). 2: geometryFrom links are kept for every feature.
INDEX_VERSION = 2

# SHACL constraint components a compatibility rule can use (see shapes/compatibility.ttl).
COMPAT_TERMS = {"hasValue", "in"}
COMPAT_NUMERIC = {"minInclusive", "maxInclusive", "minExclusive", "maxExclusive"}


@dataclass
class CompatRule:
    key: str                 # characteristic key in vehicle profiles
    label: str
    path: list[str]          # predicate IRIs from era:VehicleType (a sequence path)
    constraint: str          # local name of the SHACL constraint component
    facets: list[str]        # infrastructure facet keys compared with (values pooled)
    applies_to: str = "track"  # track | section
    kind: str = "terms"      # terms | number | boolean (what the vehicle value is)
    datatype: str | None = None
    unit: str | None = None
    comment: str | None = None
    order: int = 100
    # What a track without a value for the compared facets means: "unknown" (default)
    # or "incompatible" (atlas:whenMissing). A vehicle that has none_value (atlas:noneValue)
    # among its values for this rule, or for the rule named by none_from (atlas:noneFrom),
    # is compatible with such a track instead: e.g. "not electrified" for a bi-mode vehicle.
    when_missing: str = "unknown"
    none_value: str | None = None
    none_from: str | None = None
    missing_reason: str | None = None  # atlas:missingReason, shown for such a track
    # Track rules: the compared facets also indexed on the section class. A track without
    # a value takes the value of its section of line (RINF puts some parameters there).
    section_facets: list[str] = field(default_factory=list)


@dataclass
class CompatSet:
    track_classes: list[str]
    section_class: str
    rules: list[CompatRule]


@dataclass
class ShapeSet:
    classes: list[ClassShape]
    shared: list[Facet]
    compat: CompatSet | None = None  # not part of the digest: rules need no reindex

    def digest(self) -> str:
        blob = json.dumps([INDEX_VERSION] + [asdict(c) for c in self.classes] + [asdict(f) for f in self.shared],
                          sort_keys=True)
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
            if kind not in ("terms", "range", "boolean", "text"):
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
    out = ShapeSet(classes=classes, shared=shared)
    out.compat = _load_compat(st, r, out)
    if out.compat:
        _index_compat_facets(out)
    return out


def _index_compat_facets(shapes: ShapeSet) -> None:
    """Index every facet a track rule compares on every track class, also where that
    class's own shape does not list it (e.g. a parameter only in running-track.ttl but
    data typed era:Track), so a rule finds the value whatever class a track is indexed as."""
    by_key = {c.key: c for c in shapes.classes}
    tracks = [by_key[k] for k in shapes.compat.track_classes]
    for rule in shapes.compat.rules:
        if rule.applies_to != "track":
            continue
        for fk in rule.facets:
            src = next((f for c in tracks for f in shapes.all_facets(c) if f.key == fk), None)
            for c in tracks:
                if src and not any(f.key == fk for f in shapes.all_facets(c)):
                    c.facets.append(replace(src))
                    c.facets.sort(key=lambda f: (f.order, f.key))


def _load_compat(st: ox.Store, r: _Reader, shapes: ShapeSet) -> CompatSet | None:
    """The vehicle compatibility rules: the node shape with atlas:compatibility true."""
    found = None
    for q in st.quads_for_pattern(None, ox.NamedNode(ATLAS + "compatibility"), None):
        if not (isinstance(q.object, ox.Literal) and q.object.value == "true"):
            continue
        if found is not None:
            raise ShapeError("more than one shape has atlas:compatibility true")
        found = q.subject
    if found is None:
        return None
    node = found
    by_key = {c.key: c for c in shapes.classes}
    track_classes = sorted(o.value for o in r.objs(node, ATLAS + "trackClass") if isinstance(o, ox.Literal))
    section_class = r.lit(node, ATLAS + "sectionClass", "section-of-line")
    for c in track_classes + [section_class]:
        if c not in by_key:
            raise ShapeError(f"compatibility: class {c!r} has no facet shape")
    if not track_classes:
        raise ShapeError("compatibility: atlas:trackClass is required")

    def facet_keys(classes: list[str]) -> dict[str, Facet]:
        out: dict[str, Facet] = {}
        for c in classes:
            for f in shapes.all_facets(by_key[c]):
                out.setdefault(f.key, f)
        return out

    rules: list[CompatRule] = []
    for prop in r.objs(node, SH + "property"):
        key = r.lit(prop, ATLAS + "characteristic")
        if not key:
            continue
        if r.lit(prop, ATLAS + "enabled", "true") == "false":
            continue
        if any(x.key == key for x in rules):
            raise ShapeError(f"compatibility: duplicate characteristic {key!r}")
        pnode = r.one(prop, SH + "path")
        if isinstance(pnode, ox.NamedNode):
            path = [pnode.value]
        elif pnode is not None and r.one(pnode, RDF + "first") is not None:
            path = [_iri(n) for n in r.rdf_list(pnode)]
            if not all(path):
                raise ShapeError(f"compatibility.{key}: sh:path must be an IRI or a list of IRIs")
        else:
            raise ShapeError(f"compatibility.{key}: sh:path must be an IRI or a list of IRIs")
        cnode = _iri(r.one(prop, ATLAS + "constraint"))
        constraint = cnode[len(SH):] if cnode and cnode.startswith(SH) else None
        if constraint not in COMPAT_TERMS | COMPAT_NUMERIC:
            raise ShapeError(f"compatibility.{key}: atlas:constraint must be one of "
                             f"{', '.join('sh:' + c for c in sorted(COMPAT_TERMS | COMPAT_NUMERIC))}")
        applies = r.lit(prop, ATLAS + "appliesTo", "track")
        if applies not in ("track", "section"):
            raise ShapeError(f"compatibility.{key}: atlas:appliesTo must be \"track\" or \"section\"")
        facets = sorted(o.value for o in r.objs(prop, ATLAS + "compareTo") if isinstance(o, ox.Literal))
        if not facets:
            raise ShapeError(f"compatibility.{key}: atlas:compareTo is required")
        known = facet_keys(track_classes if applies == "track" else [section_class])
        for fk in facets:
            if fk not in known:
                raise ShapeError(f"compatibility.{key}: facet {fk!r} is not defined for {applies} classes")
            if (known[fk].kind == "range") != (constraint in COMPAT_NUMERIC):
                raise ShapeError(f"compatibility.{key}: sh:{constraint} does not fit the {known[fk].kind} facet {fk!r}")
        dt = _iri(r.one(prop, SH + "datatype"))
        kind = ("number" if constraint in COMPAT_NUMERIC
                else "boolean" if dt == XSD + "boolean" or known[facets[0]].kind == "boolean" else "terms")
        wm = _iri(r.one(prop, ATLAS + "whenMissing"))
        when_missing = {None: "unknown", ATLAS + "Unknown": "unknown", ATLAS + "Incompatible": "incompatible"}.get(wm)
        if when_missing is None:
            raise ShapeError(f"compatibility.{key}: atlas:whenMissing must be atlas:Unknown or atlas:Incompatible")
        none_value = _iri(r.one(prop, ATLAS + "noneValue"))
        if r.one(prop, ATLAS + "noneValue") is not None and (not none_value or kind != "terms"):
            raise ShapeError(f"compatibility.{key}: atlas:noneValue must be an IRI, on a rule compared by value")
        section_facets = []
        if applies == "track":
            on_section = facet_keys([section_class])
            section_facets = [fk for fk in facets if fk in on_section]
        rules.append(CompatRule(
            key=key, label=r.label(prop, RDFS + "label", SH + "name") or key, path=path, constraint=constraint,
            facets=facets, applies_to=applies, kind=kind, datatype=dt, unit=r.lit(prop, ATLAS + "unit"),
            comment=r.label(prop, RDFS + "comment", SH + "description"), order=int(r.lit(prop, SH + "order", "100")),
            when_missing=when_missing, none_value=none_value, none_from=r.lit(prop, ATLAS + "noneFrom"),
            missing_reason=r.label(prop, ATLAS + "missingReason"), section_facets=section_facets))
    by_rule = {x.key: x for x in rules}
    for x in rules:
        if x.none_from and not (x.none_from in by_rule and by_rule[x.none_from].none_value):
            raise ShapeError(f"compatibility.{x.key}: atlas:noneFrom {x.none_from!r} must name a rule with atlas:noneValue")
    rules.sort(key=lambda x: (x.order, x.key))
    return CompatSet(track_classes=track_classes, section_class=section_class, rules=rules)


def _numeric(dt: str) -> bool:
    return dt.startswith(XSD) and dt[len(XSD):] in {
        "integer", "int", "long", "short", "decimal", "double", "float", "nonNegativeInteger",
        "positiveInteger", "unsignedInt", "unsignedLong", "byte", "unsignedShort"}
