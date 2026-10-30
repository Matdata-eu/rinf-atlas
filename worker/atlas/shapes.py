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
# Prefixes for showing IRIs and rules compactly (the rule's Turtle, property paths).
PREFIXES = {
    "era": "http://data.europa.eu/949/", "sh": SH, "atlas": ATLAS, "rdf": RDF, "rdfs": RDFS, "xsd": XSD,
    "shape": "https://rinf-atlas.matdata.eu/shapes/", "gsp": "http://www.opengis.net/ont/geosparql#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
}

# Tried in this order: the net reference's geometry wins over the feature's own.
DEFAULT_GEOMETRY_PATHS = [
    "(<http://data.europa.eu/949/netReference>/<http://www.opengis.net/ont/geosparql#hasGeometry>"
    "/<http://www.opengis.net/ont/geosparql#asWKT>)",
    "(<http://www.opengis.net/ont/geosparql#hasGeometry>/<http://www.opengis.net/ont/geosparql#asWKT>)",
]


@dataclass
class Facet:
    key: str
    label: str
    path: str               # SPARQL property path
    simple: str | None      # predicate IRI when the path is a single predicate
    kind: str = "terms"     # terms|range|boolean|text|link|record
    datatype: str | None = None
    unit: str | None = None
    order: int = 100
    color_by: int | None = None  # atlas:colorBy: suggested in the map's "Colour by" picker (rank)
    # atlas:colorClasses: fixed map colour classes of a range facet, low to high:
    # {"up_to": inclusive upper bound (None for the last class), "label": name or None}
    color_classes: list[dict] | None = None
    # A record facet (atlas:kind "record"): each value is one node reached by path, kept as
    # {name: value} with one entry per atlas:field [ atlas:name ; sh:path ], so values that
    # belong together stay together (e.g. raised pantographs: number and spacing at a speed).
    # [{"name": str, "path": SPARQL path, "kind": "range" | "terms" | "boolean"}]
    fields: list[dict] = field(default_factory=list)


@dataclass
class ClassShape:
    key: str
    class_iri: str | None   # None for the shared shape
    label: str
    color: str | None = None
    geometry: str = "any"
    order: int = 100
    min_zoom: int = 0
    draw_order: int | None = None  # atlas:drawOrder: higher is drawn on top; default -order
    shared: bool = False
    label_paths: list[str] = field(default_factory=list)
    geometry_paths: list[str] = field(default_factory=list)
    line_between: tuple[str, str] | None = None
    geometry_from: list[str] = field(default_factory=list)
    facets: list[Facet] = field(default_factory=list)


# Bumped when extraction changes what it writes for the same shapes. It is part
# of the digest, so an upgrade re-extracts every graph from the mirror once by
# itself (no download).
#   2: geometryFrom ("part of") links are kept for every feature, not only for
#      features without a geometry of their own (feature_link rule 'from'; used
#      by map colouring, vehicle compatibility and the line profile).
#   3: the branches of an alternative atlas:geometryPath are tried in order (first hit
#      wins) instead of merged, so a net reference's geometry replaces the feature's own.
INDEX_VERSION = 3

# SHACL constraint components a compatibility rule can use (see shapes/compatibility.ttl).
COMPAT_TERMS = {"hasValue", "in"}
COMPAT_NUMERIC = {"minInclusive", "maxInclusive", "minExclusive", "maxExclusive"}
# Constraints of the atlas: namespace (a vehicle profile is more than a SHACL constraint
# here): requires (the track requires a capability, the vehicle has it or not), record
# (rows of a record facet, e.g. raised pantographs), brakingDistance (computed per track).
COMPAT_ATLAS = {ATLAS + "requires": "requires", ATLAS + "record": "record",
                ATLAS + "brakingDistance": "brakingDistance"}


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
    # A vehicle option meaning "does not have the system" (atlas:withoutValue, e.g. no ETCS),
    # labelled without_label. In the comparison it stands for without_matches: the track
    # values such a vehicle runs on (e.g. no ETCS, level 0, NTC); other values fail.
    without_value: str | None = None
    without_label: str | None = None
    without_matches: list[str] = field(default_factory=list)
    # A track whose facet has a value counts as having another value for this rule
    # (atlas:impliedValue): [{"facet": key, "when": IRI, "value": IRI}], e.g. a track with
    # era:notApplicable era:etcs counts as ETCS level N.
    implied: list[dict] = field(default_factory=list)
    # A numeric rule compared with a facet of concepts: the number each concept stands
    # for (atlas:termValue, e.g. the axle load limit of an EN 15528 line category).
    term_values: dict[str, float] = field(default_factory=dict)
    # The ERA property along path holds the value times this factor (atlas:pathFactor).
    path_factor: float | None = None
    # Track rules: the compared facets also indexed on the section class. A track without
    # a value takes the value of its section of line (RINF puts some parameters there).
    section_facets: list[str] = field(default_factory=list)
    # For explaining the rule: each compared facet with its label, the RINF property path
    # it is read from (compact, e.g. era:contactLineSystem/era:energySupplySystem) and the
    # classes that index it; and the rule itself as Turtle.
    infra: list[dict] = field(default_factory=list)
    source: str = ""
    # Vehicle-side choices (atlas:option) of a rule compared by value: each stands for the
    # track values it is compatible with (matches) and those that make the result unknown
    # (maybe, e.g. "allowed under conditions"): [{"value": IRI, "label", "matches": [IRI],
    # "maybe": [IRI], "path_value": value written on sh:path in Turtle or None, "order"}].
    options: list[dict] = field(default_factory=list)
    # constraint "record": {"fields": [{"field", "constraint", "characteristic"}], "select":
    # {"field", "characteristic"} | None}: the rows of the record facet that apply (select
    # field >= the characteristic's value) must satisfy every field constraint, each with the
    # value of its characteristic (the first one is this rule's own).
    record: dict | None = None
    # A rule compared by value or number that reads one field of a record facet instead of
    # its facets when the select characteristic is filled in (e.g. the line categories whose
    # load capability speed is at least the vehicle's maximum speed):
    # {"facet", "field", "select": {"field", "characteristic"}}.
    record_filter: dict | None = None
    # constraint "brakingDistance": the vehicle value is a mean deceleration (m/s²); the
    # distance from min(speed_from characteristic, highest speed_facets value) must not
    # exceed the track's value. A caveat facet that is true makes the result unknown.
    braking: dict | None = None
    # The values come from the features of this class linked to the track ("part of" links,
    # e.g. its tunnels); a track without such a feature is compatible.
    linked_class: str | None = None
    # Composing a train (see api/app/compat.py, combine): the vehicles whose values count
    # (all | pantograph | leading | tail | train: entered for the whole train) and how they
    # combine (sum | max | min | intersection | union | all; None: from the constraint).
    combine_over: str = "all"
    combine_by: str | None = None
    group: str | None = None  # atlas:group: heading the characteristic is listed under
    # atlas:vehicleDefault: the value of a vehicle that counts but has none (e.g. 1 raised
    # pantograph for a unit with its pantograph up)
    vehicle_default: float | None = None
    # combine_by "spacing": the characteristic with each vehicle's length (atlas:lengthFrom)
    length_from: str | None = None


@dataclass
class CompatSet:
    track_classes: list[str]
    section_class: str
    rules: list[CompatRule]


# The result of a view's requirement check, as the value of its facet (see atlas.ttl).
CHECK_MEETS, CHECK_NO_DATA, CHECK_FAILS = ATLAS + "Meets", ATLAS + "NoData", ATLAS + "DoesNotMeet"
CHECK_LABELS = {CHECK_MEETS: "Meets the requirement", CHECK_NO_DATA: "Data missing",
                CHECK_FAILS: "Does not meet the requirement"}
# SHACL constraint components a check can use: the compat ones, plus atlas:notIn
# (the feature has at least one value that is not in the list).
CHECK_TERMS = {"hasValue", "in", "notIn"}


@dataclass
class ViewCheck:
    """One requirement of a view (a sh:property with atlas:check): evaluated by the worker on
    the indexed facets of the view's check classes, the result stored as facet `key`."""
    key: str
    label: str
    constraint: str          # hasValue | in | notIn | minInclusive | maxInclusive | minExclusive | maxExclusive
    facets: list[str]        # compared facet keys (values pooled)
    values: list = field(default_factory=list)  # IRIs / strings / booleans, or [number]
    # A numeric check compared with a facet of concepts: the number each concept stands for.
    term_values: dict[str, float] = field(default_factory=dict)
    implied: list[dict] = field(default_factory=list)  # as CompatRule.implied
    when_missing: str = "unknown"  # unknown | fails
    # Only features matching every condition are checked: [{"facet": key, "values": [...]}].
    applies_when: list[dict] = field(default_factory=list)
    comment: str | None = None
    deadline: str | None = None    # atlas:deadline, display text
    unit: str | None = None
    order: int = 100


@dataclass
class View:
    """A named lens on the map (shapes/views/*.ttl): classes, filters, a group picker, the
    parameters that matter and optional requirement checks."""
    key: str
    label: str
    comment: str | None = None
    order: int = 100
    classes: list[str] = field(default_factory=list)
    group_by: str | None = None     # terms facet key whose values are the view's groups
    group_label: str | None = None
    color_by: str | None = None
    filters: dict = field(default_factory=dict)  # facet key -> [values] | {"min", "max"}
    params: list[str] = field(default_factory=list)  # facet keys, in order
    checks: list[ViewCheck] = field(default_factory=list)
    check_classes: list[str] = field(default_factory=list)
    section_class: str | None = None  # gets the best result of its parts (feature_link 'from')
    summary_key: str | None = None    # every check at once (worst result), when there are several
    summary_label: str | None = None
    file: str | None = None           # the shapes/ file it is defined in


@dataclass
class ShapeSet:
    classes: list[ClassShape]
    shared: list[Facet]
    compat: CompatSet | None = None  # not part of the digest: rules need no reindex
    # Views and their checks: not part of the digest either; changed checks are re-evaluated
    # on the index without a reindex (see views_digest).
    views: list[View] = field(default_factory=list)
    # The shape files as written (name relative to the shapes folder -> Turtle) and the
    # file each class shape is in, so the app can show them. Not part of the digest either.
    files: dict[str, str] = field(default_factory=dict)
    class_files: dict[str, str] = field(default_factory=dict)

    def digest(self) -> str:
        def strip(d: dict) -> dict:  # map colour classes are display only: no reindex
            for f in d.get("facets", [d]):
                f.pop("color_classes", None)
            return d
        blob = json.dumps([INDEX_VERSION] + [strip(asdict(c)) for c in self.classes]
                          + [strip(asdict(f)) for f in self.shared], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def views_digest(self) -> str:
        """Hash of what the worker evaluates for the views (their checks)."""
        return views_digest([asdict(v) for v in self.views])

    def all_facets(self, cls: ClassShape) -> list[Facet]:
        seen = {f.key for f in cls.facets}
        return cls.facets + [f for f in self.shared if f.key not in seen]


def views_digest(views: list[dict]) -> str:
    """Hash of the view checks, from views as dicts (asdict(View), or view_def.def rows)."""
    blob = json.dumps([[v["checks"], v["check_classes"], v["section_class"], v["summary_key"]]
                       for v in sorted(views, key=lambda v: (v["order"], v["key"]))], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


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

    def alternatives(self, node) -> list[str]:
        """A path as its sh:alternativePath branches, in list order (a single path otherwise)."""
        alt = self.one(node, SH + "alternativePath") if not isinstance(node, (ox.NamedNode, ox.Literal)) else None
        return [self.path(n) for n in self.rdf_list(alt)] if alt is not None else [self.path(node)]

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
    texts: dict[str, str] = {}
    class_files: dict[str, str] = {}
    view_files: dict[str, str] = {}
    for f in files:
        name = f.relative_to(shapes_dir).as_posix()
        texts[name] = f.read_text(encoding="utf-8")
        try:
            st.load(path=str(f), format=ox.RdfFormat.TURTLE)
            one = ox.Store()
            one.load(path=str(f), format=ox.RdfFormat.TURTLE)
        except SyntaxError as e:
            raise ShapeError(f"{f.name}: {e}") from e
        for q in one.quads_for_pattern(None, ox.NamedNode(ATLAS + "key"), None):
            class_files.setdefault(q.object.value, name)
        for q in one.quads_for_pattern(None, ox.NamedNode(ATLAS + "view"), None):
            view_files.setdefault(q.object.value, name)
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
        draw = r.lit(node, ATLAS + "drawOrder")
        cs.draw_order = int(draw) if draw is not None else -cs.order
        cs.label_paths = [r.path(p) for p in r.objs(node, ATLAS + "labelPath")] or [f"<{RDFS}label>"]
        # The branches of an alternative path are tried one by one: the first that gives a
        # feature a geometry wins (e.g. its net reference's geometry over its own).
        cs.geometry_paths = [a for p in r.objs(node, ATLAS + "geometryPath") for a in r.alternatives(p)]
        if not cs.geometry_paths and not is_shared:
            cs.geometry_paths = list(DEFAULT_GEOMETRY_PATHS)
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
            if kind not in ("terms", "range", "boolean", "text", "link", "record"):
                raise ShapeError(f"{key}.{fkey}: unknown atlas:kind {kind!r}")
            fields = []
            for fn in r.objs(prop, ATLAS + "field"):
                name, fpath = r.lit(fn, ATLAS + "name"), r.one(fn, SH + "path")
                if not name or fpath is None or not name.isidentifier():
                    raise ShapeError(f"{key}.{fkey}: atlas:field needs an atlas:name (letters, digits, _) and a sh:path")
                fdt = _iri(r.one(fn, SH + "datatype"))
                fkind = r.lit(fn, ATLAS + "kind") or ("boolean" if fdt == XSD + "boolean"
                                                       else "range" if fdt and _numeric(fdt) else "terms")
                if fkind not in ("terms", "range", "boolean"):
                    raise ShapeError(f"{key}.{fkey}: atlas:field {name!r} has unknown atlas:kind {fkind!r}")
                fields.append({"name": name, "path": r.path(fpath), "kind": fkind})
            fields.sort(key=lambda x: x["name"])
            if (kind == "record") != bool(fields):
                raise ShapeError(f"{key}.{fkey}: a record facet needs atlas:field, and only a record facet has them")
            fc = Facet(
                key=fkey,
                label=r.label(prop, RDFS + "label", SH + "name") or fkey,
                path=r.path(pnode),
                simple=pnode.value if isinstance(pnode, ox.NamedNode) else None,
                kind=kind,
                datatype=dt,
                unit=r.lit(prop, ATLAS + "unit"),
                order=int(r.lit(prop, SH + "order", "100")),
                color_by=_int(r.lit(prop, ATLAS + "colorBy"), f"{key}.{fkey}: atlas:colorBy"),
                fields=fields,
            )
            if kind == "record":
                fc.simple = None  # extracted node by node (extract.Extractor.records)
            if fc.color_by is not None and kind in ("text", "record"):
                raise ShapeError(f"{key}.{fkey}: a {kind} facet cannot be used to colour the map")
            cc = r.one(prop, ATLAS + "colorClasses")
            if cc is not None:
                if kind != "range":
                    raise ShapeError(f"{key}.{fkey}: atlas:colorClasses needs a range facet")
                fc.color_classes = _color_classes(r, r.rdf_list(cc), f"{key}.{fkey}: atlas:colorClasses")
            (cs.facets if not is_shared else shared).append(fc)
        cs.facets.sort(key=lambda f: (f.order, f.key))
        if not is_shared:
            classes.append(cs)
    classes.sort(key=lambda c: (c.order, c.key))
    shared.sort(key=lambda f: (f.order, f.key))
    out = ShapeSet(classes=classes, shared=shared, files=texts, class_files=class_files)
    out.compat = _load_compat(st, r, out)
    if out.compat:
        _index_compat_facets(out)
    out.views = _load_views(r, out, view_files)
    return out


def compact_iri(iri: str) -> str:
    for p, ns in PREFIXES.items():
        if iri.startswith(ns) and all(c.isalnum() or c in "-_." for c in iri[len(ns):]) and iri[len(ns):]:
            return f"{p}:{iri[len(ns):]}"
    return f"<{iri}>"


def position_rules(cs: ClassShape) -> dict:
    """How a class gets its position on the map, as compact SPARQL paths (tried in this order)."""
    return {"geometry_path": [compact_path(p) for p in cs.geometry_paths],
            "line_between": [compact_path(p) for p in cs.line_between] if cs.line_between else None,
            "geometry_from": [compact_path(p) for p in cs.geometry_from]}


def compact_path(path: str) -> str:
    """A SPARQL property path with prefixed names, without redundant outer parentheses."""
    import re
    out = re.sub(r"<([^>]+)>", lambda m: compact_iri(m.group(1)), path)
    while out.startswith("(") and out.endswith(")") and out.count("(") == 1:
        out = out[1:-1]
    return out


_ORDER = [SH + "path", ATLAS + "characteristic", RDFS + "label", RDFS + "comment", ATLAS + "compareTo",
          ATLAS + "constraint", ATLAS + "appliesTo", ATLAS + "whenMissing", ATLAS + "noneValue",
          ATLAS + "noneFrom", ATLAS + "missingReason", ATLAS + "withoutValue", ATLAS + "withoutLabel",
          ATLAS + "withoutMatches", ATLAS + "impliedValue", ATLAS + "termValue", SH + "datatype", ATLAS + "unit", SH + "order"]


def _turtle_term(st: ox.Store, t, indent: str) -> str:
    if isinstance(t, ox.NamedNode):
        return compact_iri(t.value)
    if isinstance(t, ox.Literal):
        v = t.value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        dt = t.datatype.value if t.datatype else XSD + "string"
        if t.language:
            return f'"{v}"@{t.language}'
        if dt == XSD + "string":
            return f'"{v}"'
        if dt in (XSD + "integer", XSD + "boolean") or (dt == XSD + "decimal" and "." in v):
            return v
        return f'"{v}"^^{compact_iri(dt)}'
    # blank node: an RDF list or a nested node
    first = next(iter(st.quads_for_pattern(t, ox.NamedNode(RDF + "first"), None)), None)
    if first is not None:
        items, node = [], t
        while isinstance(node, ox.BlankNode):
            f = next(iter(st.quads_for_pattern(node, ox.NamedNode(RDF + "first"), None)), None)
            if f is None:
                break
            items.append(_turtle_term(st, f.object, indent))
            node = next(iter(st.quads_for_pattern(node, ox.NamedNode(RDF + "rest"), None))).object
        return "( " + " ".join(items) + " )"
    return _turtle_node(st, t, indent)


def _turtle_node(st: ox.Store, node, indent: str) -> str:
    """A blank node as an inline Turtle [ ... ] block, its predicates in a readable order."""
    preds: dict[str, list] = {}
    for q in st.quads_for_pattern(node, None, None):
        preds.setdefault(q.predicate.value, []).append(q.object)
    order = sorted(preds, key=lambda p: (_ORDER.index(p) if p in _ORDER else len(_ORDER), p))
    inner = indent + "    "
    lines = [f"{inner}{compact_iri(p)} {', '.join(_turtle_term(st, o, inner) for o in preds[p])} ;" for p in order]
    return "[\n" + "\n".join(lines) + f"\n{indent}]"


def rule_turtle(st: ox.Store, shape, prop) -> str:
    """One compatibility rule (a sh:property of the rule shape) as standalone Turtle."""
    body = f"{compact_iri(shape.value) if isinstance(shape, ox.NamedNode) else '[]'} sh:property {_turtle_node(st, prop, '')} ."
    used = sorted({p for p in PREFIXES if f"{p}:" in body})
    return "".join(f"PREFIX {p}: <{PREFIXES[p]}>\n" for p in used) + "\n" + body + "\n"


def _index_compat_facets(shapes: ShapeSet) -> None:
    """Index every facet a track rule compares on every track class, also where that
    class's own shape does not list it (e.g. a parameter only in running-track.ttl but
    data typed era:Track), so a rule finds the value whatever class a track is indexed as."""
    by_key = {c.key: c for c in shapes.classes}
    tracks = [by_key[k] for k in shapes.compat.track_classes]
    for rule in shapes.compat.rules:
        if rule.applies_to != "track":
            continue
        keys = [] if rule.linked_class else rule.facets + [x["facet"] for x in rule.implied]
        keys += [rule.record_filter["facet"]] if rule.record_filter else []
        keys += (rule.braking["speed_facets"] + rule.braking["caveat_facets"]) if rule.braking else []
        for fk in keys:
            src = next((f for c in tracks for f in shapes.all_facets(c) if f.key == fk), None)
            for c in tracks:
                if src and not any(f.key == fk for f in shapes.all_facets(c)):
                    c.facets.append(replace(src))
                    c.facets.sort(key=lambda f: (f.order, f.key))
    # what each rule compares, for explaining it
    section = by_key[shapes.compat.section_class]
    for rule in shapes.compat.rules:
        classes = ([by_key[rule.linked_class]] if rule.linked_class else tracks if rule.applies_to == "track"
                   else [section])
        rule.infra = []
        extra = [rule.record_filter["facet"]] if rule.record_filter else []
        extra += (rule.braking["speed_facets"] + rule.braking["caveat_facets"]) if rule.braking else []
        for fk in rule.facets + [x for x in extra if x not in rule.facets]:
            defs = [(c, f) for c in classes for f in shapes.all_facets(c) if f.key == fk]
            if not defs:
                continue
            f = defs[0][1]
            rule.infra.append({"key": fk, "label": f.label, "path": compact_path(f.path),
                               "classes": [c.key for c, _ in defs], "kind": f.kind, "unit": f.unit})


def _number_or_error(v: str | None, what: str) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        raise ShapeError(f"{what} must be a number") from None


def _select_by(r: _Reader, node, names: set[str], what: str) -> dict | None:
    """atlas:selectBy [ atlas:name field ; atlas:characteristic key ] of a record rule or filter."""
    sel = r.one(node, ATLAS + "selectBy")
    if sel is None:
        return None
    out = {"field": r.lit(sel, ATLAS + "name"), "characteristic": r.lit(sel, ATLAS + "characteristic")}
    if out["field"] not in names or not out["characteristic"]:
        raise ShapeError(f"{what}: atlas:selectBy needs an atlas:name of a field and an atlas:characteristic")
    return out


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
        is_input = r.lit(prop, ATLAS + "input") == "true"
        constraint = (cnode[len(SH):] if cnode and cnode.startswith(SH)
                      else COMPAT_ATLAS.get(cnode or "") if not is_input else "input")
        if is_input and cnode:
            raise ShapeError(f"compatibility.{key}: an atlas:input characteristic has no atlas:constraint")
        if constraint not in COMPAT_TERMS | COMPAT_NUMERIC | set(COMPAT_ATLAS.values()) | {"input"}:
            raise ShapeError(f"compatibility.{key}: atlas:constraint must be one of "
                             f"{', '.join('sh:' + c for c in sorted(COMPAT_TERMS | COMPAT_NUMERIC))}, "
                             f"{', '.join('atlas:' + c for c in sorted(COMPAT_ATLAS.values()))}")
        applies = r.lit(prop, ATLAS + "appliesTo", "track")
        if applies not in ("track", "section"):
            raise ShapeError(f"compatibility.{key}: atlas:appliesTo must be \"track\" or \"section\"")
        linked_class = r.lit(prop, ATLAS + "linkedClass")
        if linked_class and (linked_class not in by_key or applies != "track"):
            raise ShapeError(f"compatibility.{key}: atlas:linkedClass must name a class with a facet shape, "
                             f"on a track rule")
        facets = sorted(o.value for o in r.objs(prop, ATLAS + "compareTo") if isinstance(o, ox.Literal))
        if not facets and not is_input:
            raise ShapeError(f"compatibility.{key}: atlas:compareTo is required")
        if facets and is_input:
            raise ShapeError(f"compatibility.{key}: an atlas:input characteristic is not compared (no atlas:compareTo)")
        known = facet_keys([linked_class] if linked_class else track_classes if applies == "track" else [section_class])
        term_values: dict[str, float] = {}
        for tv in r.objs(prop, ATLAS + "termValue"):
            t, v = _iri(r.one(tv, ATLAS + "term")), r.lit(tv, ATLAS + "value")
            try:
                term_values[t] = float(v)
            except (TypeError, ValueError):
                t = None
            if not t:
                raise ShapeError(f"compatibility.{key}: atlas:termValue needs an atlas:term IRI and a numeric atlas:value")
        implied = []
        for iv in r.objs(prop, ATLAS + "impliedValue"):
            vnode = r.one(iv, ATLAS + "value")
            x = {"facet": r.lit(iv, ATLAS + "facet"), "when": _iri(r.one(iv, ATLAS + "when")),
                 "value": _term(vnode) if isinstance(vnode, (ox.NamedNode, ox.Literal)) else None}
            if any(v is None for v in x.values()) or constraint not in COMPAT_TERMS | {"requires"}:
                raise ShapeError(f"compatibility.{key}: atlas:impliedValue needs atlas:facet, an atlas:when IRI and "
                                 f"an atlas:value, on a rule compared by value")
            if x["facet"] not in known or known[x["facet"]].kind != "terms":
                raise ShapeError(f"compatibility.{key}: atlas:impliedValue facet {x['facet']!r} is not a terms "
                                 f"facet of {linked_class or applies} classes")
            implied.append(x)
        if term_values and constraint not in COMPAT_NUMERIC:
            raise ShapeError(f"compatibility.{key}: atlas:termValue needs a numeric constraint")
        record = None
        if constraint == "record":
            if len(facets) != 1 or known.get(facets[0]) is None or known[facets[0]].kind != "record":
                raise ShapeError(f"compatibility.{key}: atlas:record compares one record facet")
            names = {x["name"] for x in known[facets[0]].fields}
            record = {"fields": [], "select": None}
            for fn in r.objs(prop, ATLAS + "recordField"):
                c = _iri(r.one(fn, ATLAS + "constraint")) or ""
                fx = {"field": r.lit(fn, ATLAS + "name"), "constraint": c[len(SH):] if c.startswith(SH) else None,
                      "characteristic": r.lit(fn, ATLAS + "characteristic") or key}
                if fx["field"] not in names or fx["constraint"] not in COMPAT_NUMERIC:
                    raise ShapeError(f"compatibility.{key}: atlas:recordField needs an atlas:name of a field of "
                                     f"{facets[0]!r} and a numeric atlas:constraint")
                record["fields"].append(fx)
            record["fields"].sort(key=lambda fx: (fx["characteristic"] != key, fx["field"]))
            if not record["fields"] or record["fields"][0]["characteristic"] != key:
                raise ShapeError(f"compatibility.{key}: an atlas:recordField must compare the characteristic itself")
            record["select"] = _select_by(r, prop, names, f"compatibility.{key}")
        elif any(known.get(fk) is not None and known[fk].kind == "record" for fk in facets):
            raise ShapeError(f"compatibility.{key}: a record facet is compared with atlas:constraint atlas:record")
        record_filter = None
        rf = r.one(prop, ATLAS + "recordFilter")
        if rf is not None:
            rfk, field_ = r.lit(rf, ATLAS + "facet"), r.lit(rf, ATLAS + "field")
            names = {x["name"] for x in known[rfk].fields} if rfk in known and known[rfk].kind == "record" else set()
            if field_ not in names or constraint not in COMPAT_TERMS | COMPAT_NUMERIC:
                raise ShapeError(f"compatibility.{key}: atlas:recordFilter needs the atlas:facet of a record facet, "
                                 f"one of its fields as atlas:field, on a rule compared by value or number")
            record_filter = {"facet": rfk, "field": field_, "select": _select_by(r, rf, names, f"compatibility.{key}")}
            if record_filter["select"] is None:
                raise ShapeError(f"compatibility.{key}: atlas:recordFilter needs atlas:selectBy")
        braking = None
        if constraint == "brakingDistance":
            braking = {"speed_facets": sorted(o.value for o in r.objs(prop, ATLAS + "speedFacet") if isinstance(o, ox.Literal)),
                       "speed_from": r.lit(prop, ATLAS + "speedFrom"),
                       "caveat_facets": sorted(o.value for o in r.objs(prop, ATLAS + "caveatFacet") if isinstance(o, ox.Literal))}
            if not braking["speed_facets"] or any(known.get(fk) is None or known[fk].kind != "range"
                                                  for fk in facets + braking["speed_facets"]):
                raise ShapeError(f"compatibility.{key}: atlas:brakingDistance compares range facets and needs "
                                 f"atlas:speedFacet (a range facet)")
            if any(known.get(fk) is None or known[fk].kind != "boolean" for fk in braking["caveat_facets"]):
                raise ShapeError(f"compatibility.{key}: atlas:caveatFacet must be a boolean facet")
        for fk in facets:
            if fk not in known:
                raise ShapeError(f"compatibility.{key}: facet {fk!r} is not defined for {linked_class or applies} classes")
            want = ("record" if constraint == "record" else "boolean" if constraint == "requires"
                    else "range" if constraint in COMPAT_NUMERIC | {"brakingDistance"} and not term_values else None)
            if want and known[fk].kind != want or not want and known[fk].kind in ("range", "record"):
                raise ShapeError(f"compatibility.{key}: {constraint} does not fit the {known[fk].kind} facet {fk!r}")
        dt = _iri(r.one(prop, SH + "datatype"))
        kind = ("number" if constraint in COMPAT_NUMERIC | {"record", "brakingDistance", "input"}
                else "boolean" if constraint == "requires" or dt == XSD + "boolean" or known[facets[0]].kind == "boolean"
                else "terms")
        options = []
        for on in r.objs(prop, ATLAS + "option"):
            o = {"value": _iri(r.one(on, ATLAS + "value")), "label": r.label(on, RDFS + "label"),
                 "matches": sorted(x for x in (_iri(m) for m in r.objs(on, ATLAS + "matches")) if x),
                 "maybe": sorted(x for x in (_iri(m) for m in r.objs(on, ATLAS + "maybe")) if x),
                 "any": r.lit(on, ATLAS + "anyTrack") == "true", "order": int(r.lit(on, SH + "order", "100"))}
            pv = r.one(on, ATLAS + "pathValue")
            o["path_value"] = _term(pv) if isinstance(pv, (ox.NamedNode, ox.Literal)) else None
            if not o["value"] or not o["label"] or kind != "terms" or not (o["any"] or o["matches"]):
                raise ShapeError(f"compatibility.{key}: atlas:option needs an atlas:value IRI, rdfs:label and "
                                 f"atlas:matches (or atlas:anyTrack true), on a rule compared by value")
            options.append(o)
        options.sort(key=lambda o: (o["order"], o["label"]))
        wm = _iri(r.one(prop, ATLAS + "whenMissing"))
        when_missing = {None: "unknown", ATLAS + "Unknown": "unknown", ATLAS + "Incompatible": "incompatible"}.get(wm)
        if when_missing is None:
            raise ShapeError(f"compatibility.{key}: atlas:whenMissing must be atlas:Unknown or atlas:Incompatible")
        none_value = _iri(r.one(prop, ATLAS + "noneValue"))
        if r.one(prop, ATLAS + "noneValue") is not None and (not none_value or kind != "terms"):
            raise ShapeError(f"compatibility.{key}: atlas:noneValue must be an IRI, on a rule compared by value")
        without_value = _iri(r.one(prop, ATLAS + "withoutValue"))
        without_matches = sorted(x for x in (_iri(o) for o in r.objs(prop, ATLAS + "withoutMatches")) if x)
        if r.one(prop, ATLAS + "withoutValue") is not None and (not without_value or kind != "terms"
                                                               or constraint != "hasValue" or not without_matches):
            raise ShapeError(f"compatibility.{key}: atlas:withoutValue must be an IRI, on a sh:hasValue rule "
                             f"compared by value, with atlas:withoutMatches IRIs")
        combine_over = {None: "all", ATLAS + "AllVehicles": "all", ATLAS + "RaisedPantographs": "pantograph",
                        ATLAS + "LeadingCab": "leading", ATLAS + "TailVehicle": "tail",
                        ATLAS + "Train": "train"}.get(_iri(r.one(prop, ATLAS + "combineOver")))
        combine_by = {None: None, ATLAS + "Sum": "sum", ATLAS + "Max": "max", ATLAS + "Min": "min",
                      ATLAS + "Intersection": "intersection", ATLAS + "Union": "union",
                      ATLAS + "All": "all", ATLAS + "PantographSpacing": "spacing"}.get(
                          _iri(r.one(prop, ATLAS + "combineBy")), "bad")
        if combine_over is None or combine_by == "bad":
            raise ShapeError(f"compatibility.{key}: atlas:combineOver must be atlas:AllVehicles, "
                             f"atlas:RaisedPantographs, atlas:LeadingCab, atlas:TailVehicle or atlas:Train; "
                             f"atlas:combineBy atlas:Sum, atlas:Max, atlas:Min, atlas:Intersection, atlas:Union or atlas:All")
        vehicle_default = r.lit(prop, ATLAS + "vehicleDefault")
        if (combine_by in ("sum", "max", "min", "spacing") and kind != "number"
                or combine_by in ("intersection", "union") and kind != "terms"):
            raise ShapeError(f"compatibility.{key}: atlas:combineBy does not fit a {kind} characteristic")
        section_facets = []
        if applies == "track" and not linked_class:
            on_section = facet_keys([section_class])
            section_facets = [fk for fk in facets if fk in on_section]
        rules.append(CompatRule(
            key=key, label=r.label(prop, RDFS + "label", SH + "name") or key, path=path, constraint=constraint,
            facets=facets, applies_to=applies, kind=kind, datatype=dt, unit=r.lit(prop, ATLAS + "unit"),
            comment=r.label(prop, RDFS + "comment", SH + "description"), order=int(r.lit(prop, SH + "order", "100")),
            when_missing=when_missing, none_value=none_value, none_from=r.lit(prop, ATLAS + "noneFrom"),
            missing_reason=r.label(prop, ATLAS + "missingReason"), section_facets=section_facets,
            without_value=without_value, without_label=r.label(prop, ATLAS + "withoutLabel"),
            without_matches=without_matches if without_value else [], term_values=term_values, implied=implied,
            path_factor=float(r.lit(prop, ATLAS + "pathFactor")) if r.lit(prop, ATLAS + "pathFactor") else None,
            options=options, record=record, record_filter=record_filter, braking=braking, linked_class=linked_class,
            combine_over=combine_over, combine_by=combine_by, group=r.label(prop, ATLAS + "group"),
            vehicle_default=_number_or_error(vehicle_default, f"compatibility.{key}: atlas:vehicleDefault"),
            length_from=r.lit(prop, ATLAS + "lengthFrom"),
            source=rule_turtle(st, node, prop)))
    by_rule = {x.key: x for x in rules}
    for x in rules:
        refs = [f["characteristic"] for f in (x.record or {}).get("fields", [])[1:]]
        refs += [s["characteristic"] for s in ((x.record or {}).get("select"), (x.record_filter or {}).get("select")) if s]
        refs += [x.braking["speed_from"]] if x.braking and x.braking["speed_from"] else []
        for ref in refs:
            if ref not in by_rule or by_rule[ref].kind != "number":
                raise ShapeError(f"compatibility.{x.key}: characteristic {ref!r} must be a numeric characteristic")
    for x in rules:
        if x.none_from and not (x.none_from in by_rule and by_rule[x.none_from].none_value):
            raise ShapeError(f"compatibility.{x.key}: atlas:noneFrom {x.none_from!r} must name a rule with atlas:noneValue")
    rules.sort(key=lambda x: (x.order, x.key))
    return CompatSet(track_classes=track_classes, section_class=section_class, rules=rules)


def _term(t):
    """An RDF term as a facet value: IRI or string, boolean, number."""
    if isinstance(t, ox.NamedNode):
        return t.value
    if not isinstance(t, ox.Literal):
        raise ShapeError("blank node used as a value")
    dt = t.datatype.value if t.datatype else XSD + "string"
    if dt == XSD + "boolean":
        return t.value.strip().lower() in ("true", "1")
    if _numeric(dt):
        f = float(t.value)
        return int(f) if f.is_integer() else f
    return t.value


def _values_of(r: _Reader, node, what: str) -> tuple[str | None, list]:
    """The SHACL constraint on a filter, condition or check node: (component, values).
    sh:hasValue (repeatable), sh:in ( ... ), atlas:notIn ( ... ), or one numeric bound."""
    found = []
    has = r.objs(node, SH + "hasValue")
    if has:
        found.append(("hasValue", [_term(t) for t in has]))
    for comp, ns in (("in", SH), ("notIn", ATLAS)):
        lst = r.one(node, ns + comp)
        if lst is not None:
            found.append((comp, [_term(t) for t in r.rdf_list(lst)]))
    for comp in sorted(COMPAT_NUMERIC):
        o = r.one(node, SH + comp)
        if o is not None:
            v = _term(o)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ShapeError(f"{what}: sh:{comp} needs a number")
            found.append((comp, [v]))
    if len(found) > 1 and not all(c in COMPAT_NUMERIC for c, _ in found):
        raise ShapeError(f"{what}: give one of sh:hasValue, sh:in, atlas:notIn or numeric bounds")
    if not found:
        return None, []
    if len(found) > 1:  # a range: both bounds, for filters only
        return "range", found
    comp, vals = found[0]
    # value lists are sets: a stable order keeps the views digest stable
    return comp, vals if comp in COMPAT_NUMERIC else sorted(vals, key=lambda x: (str(type(x)), str(x)))


def _load_views(r: _Reader, shapes: ShapeSet, files: dict[str, str]) -> list[View]:
    """The views: node shapes with atlas:view (see shapes/atlas.ttl)."""
    by_key = {c.key: c for c in shapes.classes}
    known: dict[str, Facet] = {f.key: f for f in shapes.shared}
    for c in shapes.classes:
        for f in c.facets:
            known.setdefault(f.key, f)
    views: list[View] = []
    check_keys: set[str] = set()
    for q in r.s.quads_for_pattern(None, ox.NamedNode(ATLAS + "view"), None):
        node, key = q.subject, q.object.value
        if r.lit(node, ATLAS + "enabled", "true") == "false":
            continue
        if any(v.key == key for v in views):
            raise ShapeError(f"duplicate atlas:view {key!r}")
        what = f"view {key!r}"
        classes = sorted({o.value for o in r.objs(node, ATLAS + "viewClass") if isinstance(o, ox.Literal)},
                         key=lambda c: (by_key[c].order, c) if c in by_key else (0, c))
        for c in classes:
            if c not in by_key:
                raise ShapeError(f"{what}: atlas:viewClass {c!r} has no facet shape")

        def facet(k: str | None, role: str, kinds=("terms", "range", "boolean")) -> str | None:
            if k is None:
                return None
            if k not in known or known[k].kind not in kinds:
                raise ShapeError(f"{what}: {role} {k!r} is not a {' / '.join(kinds)} facet")
            return k

        v = View(key=key, label=r.label(node, RDFS + "label", SH + "name") or key,
                 comment=r.label(node, RDFS + "comment", SH + "description"),
                 order=int(r.lit(node, SH + "order", "100")), classes=classes, file=files.get(key))
        v.group_by = facet(r.lit(node, ATLAS + "groupBy"), "atlas:groupBy", ("terms",))
        v.group_label = r.label(node, ATLAS + "groupLabel")
        for fnode in r.objs(node, ATLAS + "filter"):
            fk = facet(r.lit(fnode, ATLAS + "facet"), "atlas:filter facet")
            if fk is None:
                raise ShapeError(f"{what}: atlas:filter needs atlas:facet")
            comp, vals = _values_of(r, fnode, f"{what} filter {fk}")
            if comp in ("hasValue", "in"):
                v.filters[fk] = vals
            elif comp == "range" or comp in ("minInclusive", "maxInclusive"):
                bounds = dict(vals) if comp == "range" else {comp: vals[0]}
                if set(bounds) - {"minInclusive", "maxInclusive"}:
                    raise ShapeError(f"{what} filter {fk}: only sh:minInclusive / sh:maxInclusive bound a range")
                v.filters[fk] = {"min": bounds.get("minInclusive"), "max": bounds.get("maxInclusive")}
            else:
                raise ShapeError(f"{what} filter {fk}: use sh:hasValue, sh:in or sh:minInclusive / sh:maxInclusive")
        v.check_classes = sorted({o.value for o in r.objs(node, ATLAS + "checkClass") if isinstance(o, ox.Literal)})
        v.section_class = r.lit(node, ATLAS + "checkSection")
        for c in v.check_classes + ([v.section_class] if v.section_class else []):
            if c not in by_key:
                raise ShapeError(f"{what}: check class {c!r} has no facet shape")
        params: list[tuple[int, str]] = []
        for prop in r.objs(node, SH + "property"):
            if r.lit(prop, ATLAS + "enabled", "true") == "false":
                continue
            order = int(r.lit(prop, SH + "order", "100"))
            fk, ck = r.lit(prop, ATLAS + "facet"), r.lit(prop, ATLAS + "check")
            if fk and ck:
                raise ShapeError(f"{what}: a property is either atlas:facet or atlas:check")
            if fk:
                params.append((order, facet(fk, "parameter", ("terms", "range", "boolean", "link"))))
                continue
            if not ck:
                continue
            v.checks.append(_load_check(r, prop, ck, order, known, f"{what} check {ck!r}"))
        v.params = [k for _, k in sorted(params, key=lambda x: x[0])]
        v.checks.sort(key=lambda c: (c.order, c.key))
        if v.checks and not v.check_classes:
            raise ShapeError(f"{what}: checks need atlas:checkClass")
        if len(v.checks) > 1:
            v.summary_key = r.lit(node, ATLAS + "summaryCheck") or f"{key}.all"
            v.summary_label = r.label(node, ATLAS + "summaryLabel") or f"{v.label}: every requirement"
        for k in [c.key for c in v.checks] + ([v.summary_key] if v.summary_key else []):
            if k in known or k in check_keys or "/" in k:
                raise ShapeError(f"{what}: check key {k!r} is already used (or has a /)")
            check_keys.add(k)
        v.color_by = r.lit(node, ATLAS + "colorBy")
        if v.color_by and v.color_by not in check_keys:
            facet(v.color_by, "atlas:colorBy")
        views.append(v)
    views.sort(key=lambda v: (v.order, v.key))
    _index_check_facets(shapes, views)
    return views


def _load_check(r: _Reader, prop, key: str, order: int, known: dict[str, Facet], what: str) -> ViewCheck:
    facets = sorted(o.value for o in r.objs(prop, ATLAS + "compareTo") if isinstance(o, ox.Literal))
    if not facets:
        raise ShapeError(f"{what}: atlas:compareTo is required")
    for fk in facets:
        if fk not in known or known[fk].kind not in ("terms", "range", "boolean"):
            raise ShapeError(f"{what}: atlas:compareTo {fk!r} is not a terms, range or boolean facet")
    comp, vals = _values_of(r, prop, what)
    if comp is None or comp == "range":
        raise ShapeError(f"{what}: give one constraint (sh:hasValue, sh:in, atlas:notIn, sh:minInclusive …)")
    term_values: dict[str, float] = {}
    for tv in r.objs(prop, ATLAS + "termValue"):
        t, n = _iri(r.one(tv, ATLAS + "term")), r.lit(tv, ATLAS + "value")
        try:
            term_values[t] = float(n)
        except (TypeError, ValueError):
            t = None
        if not t:
            raise ShapeError(f"{what}: atlas:termValue needs an atlas:term IRI and a numeric atlas:value")
    numeric = comp in COMPAT_NUMERIC
    for fk in facets:
        if (known[fk].kind == "range") != (numeric and not term_values):
            raise ShapeError(f"{what}: sh:{comp} does not fit the {known[fk].kind} facet {fk!r}")
    implied = []
    for iv in r.objs(prop, ATLAS + "impliedValue"):
        x = {"facet": r.lit(iv, ATLAS + "facet"), "when": _iri(r.one(iv, ATLAS + "when")),
             "value": _iri(r.one(iv, ATLAS + "value"))}
        if not all(x.values()) or numeric or x["facet"] not in known:
            raise ShapeError(f"{what}: atlas:impliedValue needs a known atlas:facet, atlas:when and atlas:value IRIs, "
                             f"on a check compared by value")
        implied.append(x)
    wm = _iri(r.one(prop, ATLAS + "whenMissing"))
    when_missing = {None: "unknown", CHECK_NO_DATA: "unknown", ATLAS + "Unknown": "unknown",
                    CHECK_FAILS: "fails", ATLAS + "Incompatible": "fails"}.get(wm)
    if when_missing is None:
        raise ShapeError(f"{what}: atlas:whenMissing must be atlas:NoData or atlas:DoesNotMeet")
    applies = []
    for cond in r.objs(prop, ATLAS + "appliesWhen"):
        fk = r.lit(cond, ATLAS + "facet")
        c, cv = _values_of(r, cond, f"{what} atlas:appliesWhen")
        if fk not in known or known[fk].kind != "terms" or c not in ("hasValue", "in"):
            raise ShapeError(f"{what}: atlas:appliesWhen needs a terms atlas:facet and sh:hasValue or sh:in")
        applies.append({"facet": fk, "values": cv})
    return ViewCheck(key=key, label=r.label(prop, RDFS + "label", SH + "name") or key, constraint=comp,
                     facets=facets, values=vals, term_values=term_values, implied=implied,
                     when_missing=when_missing, applies_when=applies,
                     comment=r.label(prop, RDFS + "comment", SH + "description"),
                     deadline=r.label(prop, ATLAS + "deadline"), unit=r.lit(prop, ATLAS + "unit"), order=order)


def _index_check_facets(shapes: ShapeSet, views: list[View]) -> None:
    """Index every facet a check reads on every check class of its view, also where that
    class's own shape does not list it (as for the vehicle check rules)."""
    by_key = {c.key: c for c in shapes.classes}
    for v in views:
        classes = [by_key[k] for k in v.check_classes]
        for chk in v.checks:
            for fk in chk.facets + [x["facet"] for x in chk.implied] + [x["facet"] for x in chk.applies_when]:
                src = next((f for c in shapes.classes for f in shapes.all_facets(c) if f.key == fk), None)
                for c in classes:
                    if src and not any(f.key == fk for f in shapes.all_facets(c)):
                        c.facets.append(replace(src))
                        c.facets.sort(key=lambda f: (f.order, f.key))


MAX_COLOR_CLASSES = 8  # the web map's sequential ramp has eight steps


def _color_classes(r: _Reader, nodes: list, what: str) -> list[dict]:
    """atlas:colorClasses ( [ atlas:upTo 40 ; rdfs:label "..." ] ... [ rdfs:label "..." ] ):
    increasing inclusive upper bounds; the last class has none and takes every higher value."""
    if not 2 <= len(nodes) <= MAX_COLOR_CLASSES:
        raise ShapeError(f"{what}: give 2 to {MAX_COLOR_CLASSES} classes, got {len(nodes)}")
    out: list[dict] = []
    for i, n in enumerate(nodes):
        raw = r.lit(n, ATLAS + "upTo")
        last = i == len(nodes) - 1
        if (raw is None) != last:
            raise ShapeError(f"{what}: every class but the last needs atlas:upTo, the last none")
        try:
            up = float(raw) if raw is not None else None
        except ValueError:
            raise ShapeError(f"{what}: atlas:upTo must be a number, got {raw!r}") from None
        if up is not None and out and up <= out[-1]["up_to"]:
            raise ShapeError(f"{what}: atlas:upTo values must increase")
        out.append({"up_to": up, "label": r.label(n, RDFS + "label", SH + "name")})
    return out


def _int(v: str | None, what: str) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except ValueError:
        raise ShapeError(f"{what} must be an integer, got {v!r}") from None


def _numeric(dt: str) -> bool:
    return dt.startswith(XSD) and dt[len(XSD):] in {
        "integer", "int", "long", "short", "decimal", "double", "float", "nonNegativeInteger",
        "positiveInteger", "unsignedInt", "unsignedLong", "byte", "unsignedShort"}
