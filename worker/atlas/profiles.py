"""Export profiles: which properties a tabular export has, as editable SHACL.

A profile is an `atlas:ExportProfile` plus one `sh:NodeShape` per class carrying
`atlas:profile`. Every `sh:property` of such a shape is a column: `sh:path` (a
predicate or a sequence of predicates), `rdfs:label`, optionally `era:rinfIndex`
(the RINF parameter number), `sh:datatype`, `sh:order`, and `sh:node` pointing at
a nested node shape whose properties become sub-columns ("Primary location /
Latitude"). See shapes/atlas.ttl for the vocabulary and shapes/export/ for the
shipped profiles; `atlas generate-export-profile` writes the "Full RINF" profile
from ERA's own SHACL shapes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pyoxigraph as ox

from .resolve import INFRA_ELEMENT, Lit, Node, Ref
from .shapes import ATLAS, RDF, RDFS, SH, XSD, ShapeError, _iri, _Reader

ERA = "http://data.europa.eu/949/"
RINF_INDEX = ERA + "rinfIndex"
SKOS_CONCEPT = "http://www.w3.org/2004/02/skos/core#Concept"
PROFILE_CLASS = ATLAS + "ExportProfile"
EXPORT_NS = "https://rinf-atlas.matdata.eu/export/"
MAX_NESTING = 5


@dataclass
class Column:
    key: str                     # dotted local names, unique within a class: "primaryLocation.latitude"
    label: str                   # "Primary location / Latitude"
    path: list[str]              # predicate IRIs, root to value
    rinf: str | None = None      # era:rinfIndex of the deepest property that has one
    datatype: str | None = None
    order: int = 100
    children: list[Column] = field(default_factory=list)   # from sh:node; a parent column itself has no cell

    def leaves(self):
        if not self.children:
            yield self
        for c in self.children:
            yield from c.leaves()

    def as_dict(self) -> dict:
        d = {"key": self.key, "label": self.label, "path": self.path}
        if self.rinf:
            d["rinf"] = self.rinf
        if self.datatype:
            d["datatype"] = self.datatype
        if self.children:
            d["children"] = [c.as_dict() for c in self.children]
        return d


@dataclass
class ClassProfile:
    class_iri: str
    label: str
    columns: list[Column]

    def leaves(self) -> list[Column]:
        return [leaf for c in self.columns for leaf in c.leaves()]

    def select(self, include: set[str] | None) -> list[Column]:
        """Leaf columns, restricted to `include` keys (a key selects its sub-columns too)."""
        if include is None:
            return self.leaves()
        out = []
        for leaf in self.leaves():
            parts = leaf.key.split(".")
            if any(".".join(parts[:i]) in include for i in range(1, len(parts) + 1)):
                out.append(leaf)
        return out


@dataclass
class Profile:
    key: str
    label: str
    comment: str | None = None
    order: int = 100
    classes: dict[str, ClassProfile] = field(default_factory=dict)   # by class IRI

    def as_dict(self) -> dict:
        return {"key": self.key, "label": self.label, "comment": self.comment, "order": self.order,
                "classes": {iri: {"label": c.label, "columns": [col.as_dict() for col in c.columns]}
                            for iri, c in self.classes.items()}}


def local(iri: str) -> str:
    return iri.rstrip("/").replace("#", "/").rsplit("/", 1)[-1]


def words(name: str) -> str:
    """camelCase local name -> words ("maxPermittedTrainLength" -> "Max permitted train length")."""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", local(name))
    return s[:1].upper() + s[1:].lower() if s else name


# --- loading -----------------------------------------------------------------------

def _path_preds(r: _Reader, node) -> list[str]:
    if isinstance(node, ox.NamedNode):
        return [node.value]
    items = r.rdf_list(node)
    if not items or not all(isinstance(i, ox.NamedNode) for i in items):
        raise ShapeError("export profiles support predicate and sequence paths only")
    return [i.value for i in items]


def _columns(r: _Reader, shape, prefix_key: str, prefix_label: str, prefix_path: list[str],
             stack: tuple, depth: int) -> list[Column]:
    cols = []
    for prop in r.objs(shape, SH + "property"):
        if r.lit(prop, ATLAS + "enabled", "true") == "false":
            continue
        pnode = r.one(prop, SH + "path")
        if pnode is None:
            raise ShapeError("export profile property without sh:path")
        preds = _path_preds(r, pnode)
        key = ".".join(local(p) for p in preds)
        label = r.label(prop, RDFS + "label", SH + "name") or words(preds[-1])
        col = Column(
            key=f"{prefix_key}.{key}" if prefix_key else key,
            label=f"{prefix_label} / {label}" if prefix_label else label,
            path=prefix_path + preds,
            rinf=r.lit(prop, RINF_INDEX),
            datatype=_iri(r.one(prop, SH + "datatype")),
            order=int(r.lit(prop, SH + "order", "100")),
        )
        nested = r.one(prop, SH + "node")
        if nested is not None and depth < MAX_NESTING and nested not in stack:
            col.children = _columns(r, nested, col.key, col.label, col.path, stack + (nested,), depth + 1)
        cols.append(col)
    cols.sort(key=lambda c: (c.order, _rinf_key(c.rinf), c.label))
    return cols


def _rinf_key(rinf: str | None) -> tuple:
    if not rinf:
        return (9999,)
    return tuple(int(x) if x.isdigit() else 9999 for x in rinf.split("."))


def load_profiles(shapes_dir: Path) -> list[Profile]:
    """Every profile under shapes_dir (any *.ttl, usually shapes/export/)."""
    st = ox.Store()
    for f in sorted(shapes_dir.glob("**/*.ttl")):
        try:
            st.load(path=str(f), format=ox.RdfFormat.TURTLE)
        except SyntaxError as e:
            raise ShapeError(f"{f.name}: {e}") from e
    r = _Reader(st)
    profiles: dict[str, Profile] = {}
    by_node: dict[str, Profile] = {}
    for q in st.quads_for_pattern(None, ox.NamedNode(RDF + "type"), ox.NamedNode(PROFILE_CLASS)):
        node = q.subject
        key = r.lit(node, ATLAS + "key") or local(node.value)
        if r.lit(node, ATLAS + "enabled", "true") == "false":
            continue
        if key in profiles:
            raise ShapeError(f"duplicate export profile {key!r}")
        p = Profile(key=key, label=r.label(node, RDFS + "label", SH + "name") or key,
                    comment=r.lit(node, RDFS + "comment"), order=int(r.lit(node, ATLAS + "order", "100")))
        profiles[key] = p
        by_node[node.value] = p
    for q in st.quads_for_pattern(None, ox.NamedNode(ATLAS + "profile"), None):
        shape, target = q.subject, q.object
        p = by_node.get(target.value) if isinstance(target, ox.NamedNode) else profiles.get(target.value)
        if p is None:
            continue   # a disabled profile
        if r.lit(shape, ATLAS + "enabled", "true") == "false":
            continue
        cls = _iri(r.one(shape, SH + "targetClass"))
        if not cls:
            raise ShapeError(f"profile {p.key}: shape {shape} has no sh:targetClass")
        if cls in p.classes:
            raise ShapeError(f"profile {p.key}: two shapes target {cls}")
        p.classes[cls] = ClassProfile(cls, r.label(shape, RDFS + "label", SH + "name") or words(cls),
                                      _columns(r, shape, "", "", [], (shape,), 0))
    return sorted(profiles.values(), key=lambda p: (p.order, p.key))


# --- values ------------------------------------------------------------------------

def values_at(node: Node, path: list[str]) -> list:
    """Every value (Lit, Ref or Node) reached from node along the predicate path."""
    cur: list = [node]
    for p in path:
        nxt = []
        for n in cur:
            if isinstance(n, Node):
                nxt.extend(n.props.get(p, []))
        cur = nxt
        if not cur:
            break
    return cur


def ref_text(v: Ref, values: str) -> str:
    if values == "iri" or not v.label:
        return v.iri
    if values == "both":
        return f"{v.label} ({v.iri})"
    return v.label


def node_text(n: Node, values: str, depth: int = 0) -> str:
    """A component without sub-columns, as compact text: `key: value; key: value`."""
    parts = []
    for p, vals in n.props.items():
        texts = [cell_text(v, values, depth + 1) for v in vals]
        parts.append(f"{local(p)}: {' | '.join(t for t in texts if t)}")
    return "{" + "; ".join(parts) + "}"


def cell_text(v, values: str = "label", depth: int = 0) -> str:
    if isinstance(v, Lit):
        return v.value
    if isinstance(v, Ref):
        return ref_text(v, values)
    if depth >= 3:
        return v.bnode and "_:" + v.bnode or v.id
    return node_text(v, values, depth)


def cell(node: Node, col: Column, values: str = "label", sep: str = "; ") -> str:
    texts = []
    for v in values_at(node, col.path):
        t = cell_text(v, values)
        if t and t not in texts:
            texts.append(t)
    return sep.join(texts)


def header_text(col: Column, headers: str = "label") -> str:
    if headers == "path":
        return col.key
    if headers == "rinf" and col.rinf:
        return f"{col.rinf} {col.label}"
    return col.label


# --- generation from ERA's SHACL shapes ----------------------------------------------

def _ontology_labels(store: ox.Store, graphs: list | None) -> dict[str, str]:
    kw = {"default_graph": graphs} if graphs else {"use_default_graph_as_union": True}
    out: dict[str, tuple[int, str]] = {}
    res = store.query(f"SELECT ?s ?l WHERE {{ ?s <{RDFS}label> ?l }}", **kw)
    for sol in res:
        s, lit = sol["s"], sol["l"]
        if not isinstance(s, ox.NamedNode) or not isinstance(lit, ox.Literal):
            continue
        rank = 0 if lit.language in ("en", None) else 1
        if s.value not in out or rank < out[s.value][0]:
            out[s.value] = (rank, lit.value)
    return {k: v for k, (_, v) in out.items()}


def _element_classes(store: ox.Store, graphs: list | None) -> set[str]:
    kw = {"default_graph": graphs} if graphs else {"use_default_graph_as_union": True}
    res = store.query(f"SELECT ?c WHERE {{ ?c <{RDFS}subClassOf>* <{INFRA_ELEMENT}> }}", **kw)
    return {sol["c"].value for sol in res if isinstance(sol["c"], ox.NamedNode)} | {INFRA_ELEMENT}


def _superclasses(store: ox.Store, graphs: list | None, classes: set[str]) -> dict[str, list[str]]:
    """Proper superclasses of each class, nearest first (ontology rdfs:subClassOf chains)."""
    kw = {"default_graph": graphs} if graphs else {"use_default_graph_as_union": True}
    direct: dict[str, list[str]] = {}
    for sol in store.query(f"SELECT ?c ?s WHERE {{ ?c <{RDFS}subClassOf> ?s }}", **kw):
        if isinstance(sol["c"], ox.NamedNode) and isinstance(sol["s"], ox.NamedNode):
            direct.setdefault(sol["c"].value, []).append(sol["s"].value)
    out = {}
    for c in classes:
        chain, todo = [], list(direct.get(c, []))
        while todo:
            x = todo.pop(0)
            if x not in chain and x != c:
                chain.append(x)
                todo.extend(direct.get(x, []))
        out[c] = chain
    return out


# Labels for the few non-ERA terms the shapes use, which the ontology does not label.
LABELS = {
    "http://www.opengis.net/ont/geosparql#hasGeometry": "Geometry",
    "http://www.opengis.net/ont/geosparql#asWKT": "WKT",
    "http://www.w3.org/2000/01/rdf-schema#label": "Label",
    "http://www.w3.org/2004/02/skos/core#prefLabel": "Preferred label",
}
# A component class with more properties than this (ERA's "common characteristics subset",
# which repeats every track parameter) is one column of compact text, not nested columns.
MAX_NESTED_PROPS = 60


def generate_profile(store: ox.Store, shapes_graph: str, ontology_graphs: list[str] | None = None, *,
                     key: str = "full", label: str = "Full RINF", version: str | None = None) -> str:
    """Turtle of a profile with every property ERA's shapes allow per infrastructure class.

    Property shapes of every node shape targeting a class are merged (ERA splits them over
    several shapes per class); `sh:class` to a component class whose shape exists becomes a
    nested shape, so the contact line system of a track, say, flattens into its own columns."""
    r = _Reader(store)
    sg = [ox.NamedNode(shapes_graph)]
    onto = [ox.NamedNode(g) for g in ontology_graphs] if ontology_graphs else None
    labels = {k: v[:1].upper() + v[1:] for k, v in _ontology_labels(store, onto).items()}
    elements = _element_classes(store, onto)
    supers = _superclasses(store, onto, elements)
    # property shapes per target class
    props: dict[str, list] = {}
    for q in store.quads_for_pattern(None, ox.NamedNode(SH + "targetClass"), None, sg[0]):
        if isinstance(q.object, ox.NamedNode):
            for p in r.objs(q.subject, SH + "property"):
                props.setdefault(q.object.value, []).append(p)
    if not props:
        raise ShapeError(f"no sh:targetClass shapes in {shapes_graph}")

    def prop_info(p) -> dict | None:
        pnode = r.one(p, SH + "path")
        if pnode is None or r.lit(p, SH + "deactivated") == "true":
            return None
        try:
            preds = _path_preds(r, pnode)
        except ShapeError:
            return None
        cls = _iri(r.one(p, SH + "class"))
        return {"preds": preds, "rinf": r.lit(p, RINF_INDEX), "datatype": _iri(r.one(p, SH + "datatype")),
                "class": cls, "comment": r.lit(p, RDFS + "comment")}

    merged: dict[str, dict[tuple, dict]] = {}
    for cls, plist in props.items():
        by_path: dict[tuple, dict] = {}
        for p in plist:
            info = prop_info(p)
            if not info:
                continue
            k = tuple(info["preds"])
            cur = by_path.setdefault(k, info)
            for f in ("rinf", "datatype", "class", "comment"):
                if not cur.get(f) and info.get(f):
                    cur[f] = info[f]
        merged[cls] = by_path
    # A class exports its own properties and those of its superclasses (ERA puts the common
    # ones, country, validity, net reference..., on the era:InfrastructureElement shape).
    inherited: dict[str, dict[tuple, dict]] = {}
    for cls in elements:
        by_path = dict(merged.get(cls, {}))
        for sup in supers.get(cls, []):
            for k, info in merged.get(sup, {}).items():
                by_path.setdefault(k, info)
        if by_path:
            inherited[cls] = by_path
    merged.update(inherited)

    def ttl_iri(iri: str) -> str:
        if iri.startswith(ERA) and _local_ok(iri[len(ERA):]):
            return "era:" + iri[len(ERA):]
        if iri.startswith(XSD):
            return "xsd:" + iri[len(XSD):]
        return f"<{iri}>"

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")

    nested_out: dict[str, str] = {}   # class IRI -> shape IRI (generated nested shapes)
    blocks: list[str] = []

    def shape_name(cls: str) -> str:
        return f"xp:{key}-{local(cls)}"

    def emit_shape(cls: str, is_class: bool, stack: tuple) -> None:
        name = shape_name(cls)
        lines = [f"{name}", "    a sh:NodeShape ;"]
        if is_class:
            lines.append(f"    atlas:profile xp:{key} ;")
        lines.append(f"    sh:targetClass {ttl_iri(cls)} ;")
        lines.append(f'    rdfs:label "{esc(LABELS.get(cls) or labels.get(cls) or words(cls))}" ;')
        items = sorted(merged[cls].values(), key=lambda i: (_rinf_key(i["rinf"]), i["preds"]))
        for i, info in enumerate(items, start=1):
            preds = info["preds"]
            plabel = " / ".join(LABELS.get(x) or labels.get(x) or words(x) for x in preds)
            path = ttl_iri(preds[0]) if len(preds) == 1 else "( " + " ".join(ttl_iri(p) for p in preds) + " )"
            body = [f"        sh:path {path} ;", f'        rdfs:label "{esc(plabel)}" ;']
            if info["rinf"]:
                body.append(f'        era:rinfIndex "{esc(info["rinf"])}" ;')
            if info["datatype"]:
                body.append(f"        sh:datatype {ttl_iri(info['datatype'])} ;")
            c = info["class"]
            if c and c in merged and c not in elements and c != SKOS_CONCEPT and len(stack) < MAX_NESTING - 1 \
                    and c not in stack and len(merged[c]) <= MAX_NESTED_PROPS:
                if c not in nested_out:
                    nested_out[c] = shape_name(c)
                    emit_shape(c, False, stack + (c,))
                body.append(f"        sh:node {nested_out[c]} ;")
            body.append(f"        sh:order {i * 10} ;")
            lines.append("    sh:property [\n" + "\n".join(body) + "\n    ] ;")
        lines[-1] = lines[-1].rstrip(" ;") + " ."
        blocks.append("\n".join(lines) + "\n")

    classes = sorted(c for c in merged if c in elements and c != INFRA_ELEMENT)
    for cls in classes:
        emit_shape(cls, True, (cls,))
    head = [
        f"# {label}: export profile generated by `atlas generate-export-profile` from ERA's RINF SHACL shapes"
        + (f" ({version})" if version else "") + ".",
        "# One shape per infrastructure class; every sh:property is a column of a tabular export.",
        "# Nested shapes (sh:node) flatten a component (contact line system, net reference...) into",
        "# sub-columns. Edit freely, or copy a shape into a new profile with its own atlas:ExportProfile.",
        "# Annotations are documented in shapes/atlas.ttl.",
        "PREFIX atlas: <https://rinf-atlas.matdata.eu/ns#>",
        f"PREFIX xp:    <{EXPORT_NS}>",
        "PREFIX sh:    <http://www.w3.org/ns/shacl#>",
        "PREFIX era:   <http://data.europa.eu/949/>",
        "PREFIX rdfs:  <http://www.w3.org/2000/01/rdf-schema#>",
        "PREFIX xsd:   <http://www.w3.org/2001/XMLSchema#>",
        "",
        f"xp:{key}",
        "    a atlas:ExportProfile ;",
        f'    atlas:key "{key}" ;',
        f'    rdfs:label "{esc(label)}" ;',
        f'    rdfs:comment "Every RINF parameter allowed by ERA\'s SHACL shapes{" " + version if version else ""}, '
        f'per class, with the RINF parameter numbers. Generated; edit as you like." ;',
        "    atlas:order 20 .",
        "",
    ]
    return "\n".join(head) + "\n".join(blocks)


def _local_ok(s: str) -> bool:
    return bool(s) and all(c.isalnum() or c in "-_." for c in s) and not s.startswith(".") and not s.endswith(".")
