"""Resolved exports from the mirror, no database: `python -m pytest worker/tests/test_export.py`."""
import gzip
import json
from pathlib import Path

import pyoxigraph as ox
import pytest
from atlas import rdfout
from atlas.exporter import LONG_HEADER, _long_rows
from atlas.mirror import prepare
from atlas.profiles import Column, cell, generate_profile, load_profiles, values_at, words
from atlas.resolve import Lit, Node, Ref, Resolver, element_classes

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"
ERA = "http://data.europa.eu/949/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
GRAPH = "http://data.europa.eu/949/graph/0082"
SHAPES_GRAPH = "http://data.europa.eu/949/graph/rinf/shacl"
ONTOLOGY_GRAPH = "http://data.europa.eu/949/graph/ontology"
ELEMENT_CLASSES = [ERA + c for c in ("OperationalPoint", "SectionOfLine", "RunningTrack", "Track", "PlatformEdge",
                                     "Tunnel", "KilometricPost", "PrimaryLocation")]


@pytest.fixture(scope="module")
def store(tmp_path_factory) -> ox.Store:
    """The Luxembourg sample as the mirror holds it (blank nodes skolemised), ERA's shapes and class tree."""
    st = ox.Store()
    nt = tmp_path_factory.mktemp("lu") / "lu.nt"
    nt.write_bytes(gzip.open(FIXTURES / "lu-sample.nt.gz").read() + (FIXTURES / "gc-op.nt").read_bytes())
    src = prepare(nt, GRAPH, drop_long=False)
    st.load(path=str(src), format=ox.RdfFormat.N_TRIPLES, to_graph=ox.NamedNode(GRAPH))
    st.load(gzip.open(FIXTURES / "era-shacl.nt.gz").read(), format=ox.RdfFormat.N_TRIPLES, to_graph=ox.NamedNode(SHAPES_GRAPH))
    st.load(path=str(FIXTURES / "era-classes.nt"), format=ox.RdfFormat.N_TRIPLES, to_graph=ox.NamedNode(ONTOLOGY_GRAPH))
    return st


def subjects(store: ox.Store, cls: str) -> list[str]:
    return sorted(q.subject.value for q in store.quads_for_pattern(None, ox.NamedNode(RDF_TYPE), ox.NamedNode(ERA + cls),
                                                                   ox.NamedNode(GRAPH)))


def resolver(store, **kw) -> Resolver:
    return Resolver(store, GRAPH, element_classes(store, ELEMENT_CLASSES),
                    element_labels=lambda iris: {i: "elem " + i.rsplit("/", 1)[-1] for i in iris}, **kw)


def test_components_are_resolved_and_elements_referenced(store):
    sol = subjects(store, "SectionOfLine")[0]
    node = resolver(store).resolve([sol])[sol]
    assert ERA + "SectionOfLine" in node.types
    # net reference -> start point -> LRS coordinate: three hops down, embedded as nodes
    net = node.values(ERA + "netReference")
    assert net and isinstance(net[0], Node) and ERA + "NetLinearReference" in net[0].types
    start = net[0].values(ERA + "startsAt")[0]
    assert isinstance(start, Node) and isinstance(start.values(ERA + "hasLrsCoordinate")[0], Node)
    wkt = values_at(node, [ERA + "netReference", "http://www.opengis.net/ont/geosparql#hasGeometry",
                           "http://www.opengis.net/ont/geosparql#asWKT"])
    assert wkt and isinstance(wkt[0], Lit) and wkt[0].value.startswith("LINESTRING")
    # the tracks and operational points it links to stay references, labelled by the index
    parts = node.values(ERA + "hasPart")
    assert parts and all(isinstance(p, Ref) and p.kind == "element" and p.label.startswith("elem ") for p in parts)
    assert all(isinstance(v, Ref) for v in node.values(ERA + "opStart"))
    # concepts are references too
    nature = node.values(ERA + "solNature")[0]
    assert isinstance(nature, Ref) and nature.kind in ("concept", "external")
    assert sum(1 for _ in node.walk()) > 8


def test_follow_resolves_parts_one_level(store):
    sol = subjects(store, "SectionOfLine")[0]
    node = resolver(store, follow=[ERA + "hasPart"]).resolve([sol])[sol]
    parts = node.values(ERA + "hasPart")
    assert parts and all(isinstance(p, Node) and ERA + "RunningTrack" in p.types for p in parts)
    # ...but the parts' own element links (platforms) are references again
    for track in parts:
        for v in track.values(ERA + "hasPart"):
            assert isinstance(v, Ref)


def test_roots_of_one_batch_are_never_embedded_in_each_other(store):
    sols = subjects(store, "SectionOfLine")
    ops = subjects(store, "OperationalPoint")
    nodes = resolver(store).resolve(sols + ops)
    assert set(nodes) == set(sols + ops)
    for s in sols:
        for v in nodes[s].values(ERA + "opStart") + nodes[s].values(ERA + "hasPart"):
            assert isinstance(v, Ref), "another root must stay a reference"


def test_rdf_outputs_parse_and_cover_the_source(store):
    tracks = subjects(store, "RunningTrack")[:5]
    nodes = resolver(store).resolve(tracks)
    roots = [nodes[t] for t in tracks]
    ttl = b"".join(rdfout.turtle(roots, {tracks[0]: "a comment"}))
    assert ttl.startswith(b"# a comment")
    parsed = ox.Store()
    parsed.load(rdfout.header(["x"]).encode() + ttl, format=ox.RdfFormat.TURTLE)
    nt = ox.Store()
    nt.load(b"".join(rdfout.ntriples(roots)), format=ox.RdfFormat.N_TRIPLES)
    assert len(parsed) == len(nt) > 100
    # every source triple of a root is in the export
    for t in tracks:
        for q in store.quads_for_pattern(ox.NamedNode(t), None, None, ox.NamedNode(GRAPH)):
            assert ox.Quad(q.subject, q.predicate, q.object, ox.DefaultGraph()) in nt, q
    doc = json.loads(b"".join(rdfout.jsonld(roots, meta={"n": 5}, extra={tracks[0]: {"atlas:class": "running-track"}})))
    assert doc["atlas:export"] == {"n": 5} and len(doc["@graph"]) == 5
    first = doc["@graph"][0]
    assert first["@id"] == tracks[0] and first["atlas:class"] == "running-track"
    assert "era:RunningTrack" in first["@type"] and isinstance(first["era:validity"], dict)


def test_blank_nodes_round_trip(tmp_path):
    nt = tmp_path / "b.nt"
    nt.write_text(f"<http://ex/a> <{RDF_TYPE}> <{ERA}OperationalPoint> .\n<http://ex/a> <http://ex/p> _:x .\n"
                  '_:x <http://ex/q> "v" .\n')
    st = ox.Store()
    st.load(path=str(prepare(nt, GRAPH, drop_long=False)), format=ox.RdfFormat.N_TRIPLES, to_graph=ox.NamedNode(GRAPH))
    node = Resolver(st, GRAPH, {ERA + "OperationalPoint"}).resolve(["http://ex/a"])["http://ex/a"]
    comp = node.values("http://ex/p")[0]
    assert isinstance(comp, Node) and comp.bnode
    out = b"".join(rdfout.ntriples([node])).decode()
    assert "_:b" in out and "urn:atlas:bnode" not in out
    assert "<http://ex/a> <http://ex/p> _:b" in out


def test_generated_profile_loads_and_flattens(store, tmp_path):
    text = generate_profile(store, SHAPES_GRAPH, [ONTOLOGY_GRAPH], version="test")
    (tmp_path / "full.ttl").write_text(text, encoding="utf-8")
    profiles = load_profiles(tmp_path)
    assert [p.key for p in profiles] == ["full"]
    full = profiles[0]
    assert ERA + "OperationalPoint" in full.classes and ERA + "RunningTrack" in full.classes
    op = full.classes[ERA + "OperationalPoint"]
    keys = {c.key for c in op.leaves()}
    assert {"opName", "uopid", "opType"} <= keys
    assert "inCountry" in keys, "properties of the era:InfrastructureElement shape are inherited"
    name = next(c for c in op.leaves() if c.key == "opName")
    assert name.rinf == "1.2.0.0.0.1" and name.path == [ERA + "opName"]
    track = full.classes[ERA + "RunningTrack"]
    nested = next(c for c in track.columns if c.key == "contactLineSystem")
    assert nested.children and all(c.key.startswith("contactLineSystem.") for c in nested.children)
    energy = next(c for c in nested.children if c.key.endswith("energySupplySystem"))
    assert energy.label.startswith("Contact line system / ") and energy.rinf == "1.1.1.2.2.1.2"
    # a huge component class (common characteristics subset) is one column, not hundreds
    belongs = next(c for c in track.columns if c.key == "belongsTo")
    assert not belongs.children
    # selecting a parent key selects its sub-columns
    sel = track.select({"contactLineSystem", "trackId"})
    assert {c.key for c in sel} == {"trackId"} | {c.key for c in nested.leaves()}
    # cells from the resolved description
    t = subjects(store, "RunningTrack")[0]
    node = resolver(store).resolve([t])[t]
    assert cell(node, next(c for c in track.leaves() if c.key == "trackId")) == node.values(ERA + "trackId")[0].value
    rows = list(_long_rows(node, {"iri": t, "class": "running-track", "title": None, "graph_iri": GRAPH}, "label"))
    assert rows and len(rows[0]) == len(LONG_HEADER)
    assert any(r[3].startswith("validity.") for r in rows), "long rows carry nested paths"


def test_column_helpers():
    assert words("maxPermittedTrainLength") == "Max permitted train length"
    c = Column(key="a.b", label="A / B", path=["http://ex/a", "http://ex/b"])
    n = Node(id="http://ex/r", props={"http://ex/a": [Node(id="_:1", props={"http://ex/b": [Lit("1"), Lit("2"), Ref("http://ex/c", "concept", "C")]})]})
    assert cell(n, c) == "1; 2; C"
    assert cell(n, c, values="iri") == "1; 2; http://ex/c"
    assert cell(n, c, values="both") == "1; 2; C (http://ex/c)"
