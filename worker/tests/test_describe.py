"""Describe index: shapes compile and evaluate against a small mirror (no database)."""
import pyoxigraph as ox
import pytest
from atlas.describe import aggregate, build_docs, graph_triples, load_describe_shapes
from atlas.shapes import ShapeError

G = "http://data.europa.eu/949/graph/TEST"
ERA = "http://data.europa.eu/949/"

SHAPES = """
PREFIX sh:   <http://www.w3.org/ns/shacl#>
PREFIX dash: <http://datashapes.org/dash#>
PREFIX rd:   <https://rinf-describe.matdata.eu/ns#>
PREFIX era:  <http://data.europa.eu/949/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX ex:   <https://example.org/shapes/>

ex:Route  a sh:PropertyGroup ; rdfs:label "Route" ; sh:order 10 .
ex:Tracks a sh:PropertyGroup ; rdfs:label "Track characteristics" ; sh:order 30 .

ex:section-of-line a sh:NodeShape ;
  sh:targetClass era:SectionOfLine ;
  rdfs:label "Section of line" ;
  sh:property [ sh:path rdfs:label ; rd:key "title" ; dash:propertyRole dash:LabelRole ] ;
  sh:property [ sh:name "Route" ; dash:viewer rd:RouteDiagramViewer ;
                rd:start era:opStart ; rd:end era:opEnd ; rd:length era:lengthOfSectionOfLine ;
                rd:orientation "horizontal" ] ;
  sh:property [ sh:path era:opStart ; sh:name "From" ; sh:group ex:Route ; sh:order 10 ] ;
  sh:property [ sh:path era:maximumPermittedSpeed ; sh:name "Maximum permitted speed" ;
                rd:over [ sh:inversePath era:isPartOf ] ; rd:aggregate "range" ; rd:unit "km/h" ;
                sh:group ex:Tracks ; sh:order 20 ; dash:viewer rd:AggregateViewer ] ;
  sh:property [ sh:path era:wheelSetGauge ; sh:name "Nominal track gauge" ;
                rd:over [ sh:inversePath era:isPartOf ] ; rd:aggregate "distinct-count" ;
                sh:group ex:Tracks ; sh:order 30 ] ;
  sh:property [ sh:path [ sh:inversePath era:isPartOf ] ; sh:name "Tracks" ; sh:group ex:Tracks ; sh:order 90 ;
                dash:viewer dash:ValueTableViewer ;
                sh:node [ sh:property [ sh:path era:trackId ; sh:name "Track" ; sh:order 1 ] ,
                                      [ sh:path era:maximumPermittedSpeed ; sh:name "Max speed" ; sh:order 2 ] ] ] .
"""

DATA = f"""
@prefix era: <{ERA}> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
era:sol1 a era:SectionOfLine ; rdfs:label "A - B"@en ; era:opStart era:opA ; era:opEnd era:opB ;
    era:lengthOfSectionOfLine "3.646"^^xsd:double ; era:notApplicable era:tunnelIdentification .
era:t1 a era:RunningTrack ; era:isPartOf era:sol1 ; era:trackId "I" ;
    era:maximumPermittedSpeed 85 ; era:wheelSetGauge era:g1435 .
era:t2 a era:RunningTrack ; era:isPartOf era:sol1 ; era:trackId "II" ;
    era:maximumPermittedSpeed 140 ; era:wheelSetGauge era:g1668 .
era:t3 a era:RunningTrack ; era:isPartOf era:sol1 ; era:trackId "III" ;
    era:maximumPermittedSpeed 135 ; era:wheelSetGauge era:g1435 .
"""


@pytest.fixture
def dset(tmp_path):
    (tmp_path / "sol.ttl").write_text(SHAPES, encoding="utf-8")
    return load_describe_shapes(tmp_path)


@pytest.fixture
def store():
    st = ox.Store()
    st.load(DATA.encode(), format=ox.RdfFormat.TURTLE, to_graph=ox.NamedNode(G))
    return st


def test_shapes_compile_to_rows_in_order(dset):
    (ds,) = dset.shapes
    assert ds.class_iri == ERA + "SectionOfLine"
    assert [r.key for r in ds.rows] == ["from", "maximum-permitted-speed", "nominal-track-gauge", "tracks", "route",
                                        "title"]
    route = next(r for r in ds.rows if r.key == "route")
    assert route.path is None and route.viewer == "https://rinf-describe.matdata.eu/ns#RouteDiagramViewer"
    assert route.params == {"start": f"<{ERA}opStart>", "end": f"<{ERA}opEnd>",
                            "length": f"<{ERA}lengthOfSectionOfLine>"}
    assert route.settings == {"orientation": "horizontal"}
    speed = next(r for r in ds.rows if r.key == "maximum-permitted-speed")
    assert speed.over == f"^<{ERA}isPartOf>" and speed.aggregate == "range" and speed.unit == "km/h"
    assert [c.key for c in next(r for r in ds.rows if r.key == "tracks").columns] == ["track", "max-speed"]
    assert {g.label for g in dset.groups.values()} == {"Route", "Track characteristics"}


def test_digest_changes_with_the_shapes(tmp_path, dset):
    (tmp_path / "sol.ttl").write_text(SHAPES.replace('"km/h"', '"m/s"'), encoding="utf-8")
    assert load_describe_shapes(tmp_path).digest() != dset.digest()


def test_documents_hold_values_summaries_tables_and_data_status(dset, store):
    docs, iris = build_docs(store, G, dset)
    assert len(docs) == 1
    iri, shape, doc = docs[0]
    assert iri == ERA + "sol1" and shape == "https://example.org/shapes/section-of-line"
    rows = doc["rows"]
    assert rows["title"]["values"] == [{"value": "A - B", "lang": "en"}]
    assert rows["from"]["values"] == [{"iri": ERA + "opA"}]
    assert rows["route"]["params"]["end"] == [{"iri": ERA + "opB"}]
    assert rows["route"]["params"]["length"] == [{"value": "3.646", "datatype": "http://www.w3.org/2001/XMLSchema#double"}]
    assert rows["maximum-permitted-speed"]["aggregate"] == {"min": 85, "max": 140, "children": 3, "withValue": 3}
    gauge = rows["nominal-track-gauge"]["aggregate"]
    assert gauge["values"][0] == {"iri": ERA + "g1435", "count": 2}
    assert gauge["children"] == 3 and gauge["withoutValue"] == 0
    items = rows["tracks"]["items"]
    assert [i["cells"]["track"][0]["value"] for i in items] == ["I", "II", "III"]
    assert doc["dataStatus"] == {"notApplicable": [ERA + "tunnelIdentification"]}
    assert {ERA + "opA", ERA + "opB", ERA + "g1435", ERA + "t1"} <= iris


def test_triples_of_the_graph(store):
    rows = list(graph_triples(store, G))
    assert (ERA + "t1", ERA + "trackId", "I", False, None, None) in rows
    assert (ERA + "sol1", "http://www.w3.org/2000/01/rdf-schema#label", "A - B", False, None, "en") in rows
    assert (ERA + "t1", ERA + "isPartOf", ERA + "sol1", True, None, None) in rows


def test_aggregates():
    per = {"a": [{"value": "1435"}], "b": [], "c": [{"value": "1668"}, {"value": "1435"}]}
    assert aggregate("count", per) == {"count": 3}
    assert aggregate("range", per) == {"min": 1435, "max": 1668, "children": 3, "withValue": 2}
    d = aggregate("distinct-count", per)
    assert d["values"][0] == {"value": "1435", "count": 2} and d["withoutValue"] == 1


@pytest.mark.parametrize("bad, msg", [
    ('rd:aggregate "range" ; sh:path era:x', "needs rd:over"),
    ('rd:over era:x ; sh:name "x"', "needs a sh:path"),
    ('sh:path era:x ; rd:over era:y ; rd:aggregate "avg"', "rd:aggregate must be"),
    ('sh:path era:x ; sh:group <https://example.org/nope>', "not a sh:PropertyGroup"),
])
def test_invalid_shapes_are_refused(tmp_path, bad, msg):
    (tmp_path / "x.ttl").write_text(f"""
PREFIX sh: <http://www.w3.org/ns/shacl#>
PREFIX rd: <https://rinf-describe.matdata.eu/ns#>
PREFIX era: <http://data.europa.eu/949/>
<https://example.org/s> a sh:NodeShape ; sh:targetClass era:C ; sh:property [ {bad} ] .
""", encoding="utf-8")
    with pytest.raises(ShapeError, match=msg):
        load_describe_shapes(tmp_path)


def test_duplicate_row_keys_are_refused(tmp_path):
    (tmp_path / "x.ttl").write_text("""
PREFIX sh: <http://www.w3.org/ns/shacl#>
PREFIX era: <http://data.europa.eu/949/>
<https://example.org/s> a sh:NodeShape ; sh:targetClass era:C ;
  sh:property [ sh:path era:x ; sh:name "Speed" ] , [ sh:path era:y ; sh:name "Speed" ] .
""", encoding="utf-8")
    with pytest.raises(ShapeError, match="duplicate row key"):
        load_describe_shapes(tmp_path)
