"""Generate starter facet shapes from the ERA ontology (and real data, when mirrored).

The output is ordinary SHACL with atlas: annotations, one file per class, meant
to be edited by hand afterwards. ERA's own shapes are for validation; these
only describe what to show as facets and how to find geometry.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import pyoxigraph as ox

log = logging.getLogger(__name__)

ERA = "http://data.europa.eu/949/"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
XSD = "http://www.w3.org/2001/XMLSchema#"
SKOS = "http://www.w3.org/2004/02/skos/core#"

NUMERIC = {"integer", "int", "long", "decimal", "double", "float", "nonNegativeInteger", "positiveInteger"}

# Rendering defaults per class: key, colour, geometry hint, order, min zoom.
CLASS_META = {
    "OperationalPoint":   ("operational-point",   "#d62728", "point", 10, 0),
    "SectionOfLine":      ("section-of-line",     "#1f77b4", "line",  20, 0),
    "RunningTrack":       ("running-track",       "#2ca02c", "line",  30, 10),
    "Siding":             ("siding",              "#8fd17f", "line",  40, 11),
    "Track":              ("track",               "#2ca02c", "line",  45, 10),
    "Tunnel":             ("tunnel",              "#8c564b", "line",  50, 5),
    "Bridge":             ("bridge",              "#e377c2", "line",  55, 6),
    "PlatformEdge":       ("platform-edge",       "#ff7f0e", "any",   60, 10),
    "Switch":             ("switch",              "#9467bd", "point", 70, 11),
    "Signal":             ("signal",              "#bcbd22", "point", 75, 11),
    "LevelCrossing":      ("level-crossing",      "#17becf", "point", 80, 10),
    "KilometricPost":     ("kilometric-post",     "#7f7f7f", "point", 90, 12),
    "PrimaryLocation":    ("primary-location",    "#e8a33d", "point", 95, 8),
    "HABD":               ("hot-axle-box-detector", "#aec7e8", "point", 100, 9),
    "RadioBlockCenter":   ("radio-block-center",  "#c5b0d5", "point", 105, 5),
    "SpecialArea":        ("special-area",        "#c49c94", "any",   110, 7),
    "SpecialTunnelArea":  ("special-tunnel-area", "#a0786c", "any",   115, 7),
    "Station":            ("station",             "#b2182b", "point", 15, 0),
}

# Map draw order (atlas:drawOrder, higher on top among points, lines or areas): structures
# over the tracks they carry, tracks over their operational point and section of line, small
# equipment over the operational point it sits in. Every class gets its own number: a class
# can be drawn as points or lines depending on its data (an operational point's net reference
# can be a line), and two classes with the same number swap places from tile to tile.
DRAW_ORDER = {
    "SpecialArea": 5, "SpecialTunnelArea": 6, "SectionOfLine": 10, "RunningTrack": 20, "Track": 22,
    "Siding": 24, "PlatformEdge": 30, "Tunnel": 40, "Bridge": 42,
    "RadioBlockCenter": 8, "OperationalPoint": 15, "Station": 16, "PrimaryLocation": 32,
    "KilometricPost": 44, "LevelCrossing": 50, "HABD": 52, "Switch": 60, "Signal": 62,
}

# Display labels that differ from the ontology's rdfs:label.
CLASS_LABELS = {
    "Siding": "Siding track",
}

SKIP_PROPS = {
    RDFS + "label", ERA + "notApplicable", ERA + "notYetAvailable", ERA + "validity",
    ERA + "netReference", ERA + "hasPart", ERA + "isPartOf", ERA + "opStart", ERA + "opEnd",
    "http://www.opengis.net/ont/geosparql#hasGeometry", ERA + "hasTopoCoordinate",
    ERA + "hasLrsCoordinate", ERA + "inCountry", ERA + "infrastructureManager",
    ERA + "primaryLocation", ERA + "track", ERA + "hasLRS", ERA + "kmPost",
}
# Object property ranges never expanded into nested facets.
NO_NEST = {
    ERA + "Document", ERA + "TemporalFeature", "http://www.w3.org/2006/time#TemporalEntity",
    ERA + "NetPointReference", ERA + "NetLinearReference", ERA + "NetBasicReference",
    ERA + "OrganisationRole", ERA + "Body", "http://www.opengis.net/ont/geosparql#Geometry",
    RDFS + "Resource", ERA + "InfrastructureElement", ERA + "CommonCharacteristicsSubset",
}
ID_LIKE = re.compile(r"(Id|ID|Identification|Name|Code|uopid|Number|Reference)$")
MIN_USAGE = 10  # with data: drop facets used by fewer instances (test uploads)
TERMS_STRINGS = {ERA + "lineId"}  # strings that make good term facets (national line number)

# Alternatives are tried in order: the net reference's geometry wins over the feature's own.
GEOM_PATH = ("[ sh:alternativePath ( ( era:netReference gsp:hasGeometry gsp:asWKT ) "
             "( gsp:hasGeometry gsp:asWKT ) ) ]")
PART_OF = "[ sh:alternativePath ( era:isPartOf [ sh:inversePath era:hasPart ] ) ]"
# Link facets (atlas:kind "link") the line profile needs: (key, SHACL path, label).
LINKS = {
    "SectionOfLine": [("opStart", "era:opStart", "Start operational point"),
                      ("opEnd", "era:opEnd", "End operational point")],
}


@dataclass
class Prop:
    iri: str
    label: str
    kind: str                   # terms|range|boolean|text|object
    range: str | None
    datatype: str | None
    domains: set[str]
    rinf_index: tuple = (99,)
    unit: str | None = None


@dataclass
class Candidate:
    path: list[str]             # predicate chain
    label: str
    kind: str
    datatype: str | None
    rinf_index: tuple
    unit: str | None = None
    usage: int | None = None
    key: str = ""


@dataclass
class ClassInfo:
    iri: str
    label: str
    ancestors: set[str] = field(default_factory=set)
    candidates: list[Candidate] = field(default_factory=list)


def _local(iri: str) -> str:
    return re.split(r"[/#]", iri.rstrip("/"))[-1]


def _q(store: ox.Store, query: str, graphs: list[str] | None = None):
    kw = {"default_graph": [ox.NamedNode(g) for g in graphs]} if graphs else {"use_default_graph_as_union": True}
    res = store.query(query, **kw)
    names = [v.value for v in res.variables]
    for sol in res:
        yield {n: sol[n] for n in names}


def _rinf_index(vals: list[str]) -> tuple:
    best = None
    for v in vals:
        try:
            t = tuple(int(x) for x in v.split("."))
        except ValueError:
            continue
        if best is None or t < best:
            best = t
    return best or (99,)


def read_ontology(store: ox.Store, graph: str) -> tuple[dict[str, ClassInfo], dict[str, Prop]]:
    P = f"PREFIX rdfs:<{RDFS}> PREFIX owl:<{OWL}> PREFIX era:<{ERA}> PREFIX rdf:<http://www.w3.org/1999/02/22-rdf-syntax-ns#> PREFIX skos:<{SKOS}>\n"
    classes: dict[str, ClassInfo] = {}
    for r in _q(store, P + """SELECT ?c ?l ?anc WHERE {
            ?c rdfs:subClassOf+ era:InfrastructureElement .
            FILTER NOT EXISTS { ?c owl:deprecated true }
            OPTIONAL { ?c rdfs:label ?l FILTER(lang(?l) = 'en' || lang(?l) = '') }
            OPTIONAL { ?c rdfs:subClassOf+ ?anc FILTER(isIRI(?anc)) } }""", [graph]):
        if not isinstance(r["c"], ox.NamedNode):
            continue
        label = CLASS_LABELS.get(_local(r["c"].value)) or (r["l"].value if r["l"] else _local(r["c"].value))
        c = classes.setdefault(r["c"].value, ClassInfo(r["c"].value, label))
        if r["anc"] is not None:
            c.ancestors.add(r["anc"].value)

    props: dict[str, Prop] = {}
    for r in _q(store, P + """SELECT ?p ?l ?range ?dom ?scheme ?idx ?unit WHERE {
            ?p a ?t FILTER(?t IN (owl:ObjectProperty, owl:DatatypeProperty))
            FILTER NOT EXISTS { ?p owl:deprecated true }
            OPTIONAL { ?p rdfs:label ?l FILTER(lang(?l) = 'en' || lang(?l) = '') }
            OPTIONAL { ?p rdfs:range ?range FILTER(isIRI(?range)) }
            OPTIONAL { { ?p rdfs:domain ?dom FILTER(isIRI(?dom)) }
                       UNION { ?p rdfs:domain/owl:unionOf/rdf:rest*/rdf:first ?dom } }
            OPTIONAL { ?p era:inSkosConceptScheme ?scheme }
            OPTIONAL { ?p era:rinfIndex ?idx }
            OPTIONAL { ?p era:unitOfMeasure ?unit } }""", [graph]):
        if not isinstance(r["p"], ox.NamedNode):
            continue
        iri = r["p"].value
        rng = r["range"].value if r["range"] else None
        p = props.get(iri)
        if p is None:
            kind, dt = "object", None
            if r["scheme"] is not None or rng == SKOS + "Concept":
                kind = "terms"
            elif rng and rng.startswith(XSD):
                name = rng[len(XSD):]
                dt = rng
                kind = "boolean" if name == "boolean" else "range" if name in NUMERIC else "text"
            elif rng in (RDFS + "Literal", "http://www.w3.org/1999/02/22-rdf-syntax-ns#langString"):
                kind = "text"
            p = props[iri] = Prop(iri, r["l"].value if r["l"] else _local(iri), kind, rng, dt, set())
            p._idx = []  # type: ignore[attr-defined]
        if r["dom"] is not None and isinstance(r["dom"], ox.NamedNode):
            p.domains.add(r["dom"].value)
        if r["idx"] is not None:
            p._idx.append(r["idx"].value)  # type: ignore[attr-defined]
        if r["unit"] is not None and not p.unit:
            p.unit = _local(r["unit"].value)
    for p in props.values():
        p.rinf_index = _rinf_index(getattr(p, "_idx", []))
    return classes, props


def data_usage(store: ox.Store, graphs: list[str]) -> tuple[dict, dict]:
    """(class, predicate) -> subjects, and (class, p1, p2) -> subjects for one-hop nesting."""
    direct: dict[tuple[str, str], int] = defaultdict(int)
    nested: dict[tuple[str, str, str], int] = defaultdict(int)
    for g in graphs:
        for r in _q(store, f"""SELECT ?c ?p (COUNT(DISTINCT ?s) AS ?n) WHERE {{
                ?s a <{ERA}InfrastructureElement> ; a ?c ; ?p ?o }} GROUP BY ?c ?p""", [g]):
            direct[(r["c"].value, r["p"].value)] += int(r["n"].value)
        for r in _q(store, f"""SELECT ?c ?p ?q (COUNT(DISTINCT ?s) AS ?n) WHERE {{
                ?s a <{ERA}InfrastructureElement> ; a ?c ; ?p ?x . ?x ?q ?o
                FILTER(isIRI(?x) && ?q != <http://www.w3.org/1999/02/22-rdf-syntax-ns#type>)
                FILTER NOT EXISTS {{ ?x a <{ERA}InfrastructureElement> }} }} GROUP BY ?c ?p ?q""", [g]):
            nested[(r["c"].value, r["p"].value, r["q"].value)] += int(r["n"].value)
    return direct, nested


def build(classes: dict[str, ClassInfo], props: dict[str, Prop], usage: tuple[dict, dict] | None) -> None:
    direct, nested = usage if usage else ({}, {})
    by_domain: dict[str, list[Prop]] = defaultdict(list)
    for p in props.values():
        for d in p.domains:
            by_domain[d].append(p)

    def facet_kind(p: Prop) -> str | None:
        if p.iri in TERMS_STRINGS:
            return "terms"
        if p.kind in ("terms", "boolean", "range"):
            return p.kind
        if p.kind == "text" and ID_LIKE.search(_local(p.iri)):
            return "text"
        return None

    for c in classes.values():
        scope = {c.iri} | c.ancestors
        seen: set[tuple[str, ...]] = set()
        cands: list[Candidate] = []

        def add(path: list[str], p: Prop, kind: str, use: int | None, label: str | None = None):
            t = tuple(path)
            if t in seen:
                return
            seen.add(t)
            cands.append(Candidate(path, label or p.label, kind, p.datatype, p.rinf_index, p.unit, use))

        applicable = [p for d in scope for p in by_domain.get(d, [])]
        if usage:  # properties used on this class in data, even if the ontology domain says otherwise
            applicable += [props[p] for (cc, p), n in direct.items() if cc == c.iri and p in props and n]
        for p in applicable:
            if p.iri in SKIP_PROPS:
                continue
            use = direct.get((c.iri, p.iri)) if usage else None
            if usage and (use or 0) < MIN_USAGE:
                continue
            k = facet_kind(p)
            if k:
                add([p.iri], p, k, use)
            elif p.kind == "object" and p.range and p.range not in NO_NEST and p.range not in classes:
                inner = list(by_domain.get(p.range, []))
                if usage:
                    inner += [props[q] for (cc, pp, q), n in nested.items()
                              if cc == c.iri and pp == p.iri and q in props and n]
                for q in inner:
                    if q.iri in SKIP_PROPS:
                        continue
                    nuse = nested.get((c.iri, p.iri, q.iri)) if usage else None
                    if usage and (nuse or 0) < MIN_USAGE:
                        continue
                    kq = facet_kind(q)
                    if kq:
                        lbl = q.label if p.label.lower() in q.label.lower() else f"{q.label} ({p.label})"
                        add([p.iri, q.iri], q, kq, nuse, lbl)
        cands.sort(key=lambda x: (x.rinf_index, x.label.lower()))
        c.candidates = cands

    # Facet keys: the predicate's local name; same key = same path in every class.
    by_key: dict[str, tuple[str, ...]] = {}
    for c in classes.values():
        for cand in c.candidates:
            key = ".".join(_local(x) for x in cand.path)
            if by_key.setdefault(key, tuple(cand.path)) != tuple(cand.path):
                raise RuntimeError(f"facet key clash {key}")
            cand.key = key


def class_key(iri: str) -> str:
    name = _local(iri)
    if name in CLASS_META:
        return CLASS_META[name][0]
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def _ttl_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


def _curie(iri: str) -> str:
    for p, ns in (("era", ERA), ("rdfs", RDFS), ("xsd", XSD), ("skos", SKOS)):
        if iri.startswith(ns) and re.fullmatch(r"[A-Za-z_][\w\-]*", iri[len(ns):]):
            return f"{p}:{iri[len(ns):]}"
    return f"<{iri}>"


HEADER = """# {title}
# Generated by `atlas generate-shapes` from ERA ontology {version}{data_note}.
# Edit freely: add, remove or reorder sh:property blocks, change labels,
# kinds or paths. Annotations are documented in shapes/atlas.ttl.
# Run `atlas reindex` after editing (no download needed).
PREFIX atlas: <https://rinf-atlas.matdata.eu/ns#>
PREFIX shape: <https://rinf-atlas.matdata.eu/shapes/>
PREFIX sh:    <http://www.w3.org/ns/shacl#>
PREFIX era:   <http://data.europa.eu/949/>
PREFIX gsp:   <http://www.opengis.net/ont/geosparql#>
PREFIX rdfs:  <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd:   <http://www.w3.org/2001/XMLSchema#>

"""


def render_class(c: ClassInfo, version: str, with_data: bool) -> str:
    name = _local(c.iri)
    key, color, geom, order, minz = CLASS_META.get(name, (class_key(c.iri), "#888888", "any", 200, 8))
    out = [HEADER.format(title=c.label, version=version,
                         data_note=" and the mirrored RINF data" if with_data else "")]
    out.append(f"shape:{key}\n    a sh:NodeShape ;\n    atlas:key {_ttl_str(key)} ;\n"
               f"    rdfs:label {_ttl_str(c.label[:1].upper() + c.label[1:])} ;\n"
               f"    sh:targetClass {_curie(c.iri)} ;\n    atlas:color {_ttl_str(color)} ;\n"
               f"    atlas:geometry {_ttl_str(geom)} ;\n    atlas:order {order} ;\n    atlas:minZoom {minz} ;\n")
    if name in DRAW_ORDER:
        out.append(f"    atlas:drawOrder {DRAW_ORDER[name]} ;\n")
    out.append(f"    atlas:labelPath rdfs:label ;\n    atlas:geometryPath {GEOM_PATH} ;\n")
    if name == "SectionOfLine":
        out.append("    atlas:lineBetween ( era:opStart era:opEnd ) ;\n")
    elif name == "OperationalPoint":
        out.append("    atlas:geometryFrom era:primaryLocation ;\n")
    elif name == "PrimaryLocation":
        out.append("    atlas:geometryFrom [ sh:inversePath era:primaryLocation ] ;\n")
    elif name == "HABD":
        out.append("    atlas:geometryFrom [ sh:inversePath era:tracksideHabd ] ;\n")
    elif name != "KilometricPost":
        out.append(f"    atlas:geometryFrom {PART_OF} ;\n")
    for i, cand in enumerate(c.candidates):
        path = _curie(cand.path[0]) if len(cand.path) == 1 else "( " + " ".join(_curie(p) for p in cand.path) + " )"
        lines = [f"        sh:path {path} ;", f"        atlas:facet {_ttl_str(cand.key)} ;",
                 f"        rdfs:label {_ttl_str(cand.label[:1].upper() + cand.label[1:])} ;",
                 f"        atlas:kind {_ttl_str(cand.kind)} ;"]
        if cand.datatype and cand.kind == "range":
            lines.append(f"        sh:datatype {_curie(cand.datatype)} ;")
        if cand.unit and cand.kind == "range":
            lines.append(f"        atlas:unit {_ttl_str(cand.unit)} ;")
        lines.append(f"        sh:order {(i + 1) * 10} ;")
        note = []
        if cand.rinf_index != (99,):
            note.append("RINF " + ".".join(map(str, cand.rinf_index)))
        if cand.usage is not None:
            note.append(f"{cand.usage} instances in data")
        out.append("    sh:property [" + (f"  # {', '.join(note)}" if note else "") + "\n"
                   + "\n".join(lines) + "\n    ] ;\n")
    for j, (key, path, label) in enumerate(LINKS.get(name, [])):
        out.append(f"    sh:property [\n        sh:path {path} ;\n        atlas:facet {_ttl_str(key)} ;\n"
                   f"        rdfs:label {_ttl_str(label)} ;\n        atlas:kind \"link\" ;\n"
                   f"        sh:order {900 + j * 10} ;\n    ] ;\n")
    text = "".join(out).rstrip(" ;\n") + " .\n"
    return text


SHARED = HEADER + """shape:shared
    a sh:NodeShape ;
    atlas:key "shared" ;
    atlas:shared true ;
    rdfs:label "All classes" ;
    sh:property [
        sh:path era:inCountry ;
        atlas:facet "country" ;
        rdfs:label "Country" ;
        sh:order 1 ;
    ] ;
    sh:property [
        sh:path era:infrastructureManager ;
        atlas:facet "infrastructureManager" ;
        rdfs:label "Infrastructure manager" ;
        sh:order 2 ;
    ] .
"""


def generate(store_graphs: list[tuple[ox.Store, str, list[str]]], out_dir: Path, force: bool = False) -> list[Path]:
    """store_graphs: (mirror store, ontology graph, provider graphs) per environment."""
    classes: dict[str, ClassInfo] = {}
    props: dict[str, Prop] = {}
    versions = []
    direct: dict = defaultdict(int)
    nested: dict = defaultdict(int)
    with_data = False
    for store, onto, data_graphs in store_graphs:
        c, p = read_ontology(store, onto)
        for k, v in c.items():
            if k in classes:
                classes[k].ancestors |= v.ancestors
            else:
                classes[k] = v
        for k, v in p.items():
            if k in props:
                props[k].domains |= v.domains
            else:
                props[k] = v
        for r in _q(store, f"SELECT ?v WHERE {{ <{ERA}> <{OWL}versionInfo> ?v }}", [onto]):
            versions.append(r["v"].value)
        if data_graphs:
            with_data = True
            d, n = data_usage(store, data_graphs)
            for k, v in d.items():
                direct[k] += v
            for k, v in n.items():
                nested[k] += v
    if not classes:
        raise RuntimeError("no ontology in the mirror yet; run `atlas sync` first")
    build(classes, props, (direct, nested) if with_data else None)
    version = " / ".join(sorted(set(versions))) or "?"
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    files = {"shared.ttl": SHARED.format(title="Facets shared by every class", version=version, data_note="")}
    for c in classes.values():
        files[f"{class_key(c.iri)}.ttl"] = render_class(c, version, with_data)
    for name, text in files.items():
        path = out_dir / name
        if path.exists() and not force:
            log.info("keeping existing %s (use --force to overwrite)", path)
            continue
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written
