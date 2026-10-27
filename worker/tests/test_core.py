"""Unit tests that need no database or network: `python -m pytest worker/tests`."""
from pathlib import Path

import pyoxigraph as ox
import pytest
from atlas import mirror
from atlas.era import Published
from atlas.extract import normalize_wkt
from atlas.mirror import skolemize
from atlas.shapes import ShapeError, load_shapes, position_rules
from atlas.sync import _fingerprint

SHAPES = Path(__file__).resolve().parents[2] / "shapes"


def test_wkt_lonlat_kept():
    assert normalize_wkt("POINT(4.33 50.83)") == "POINT(4.33 50.83)"


def test_wkt_crs_prefix_and_latlon_flipped():
    w = normalize_wkt("<http://www.opengis.net/def/crs/EPSG/0/4326> POINT(50.83 4.33)")
    assert w == "POINT(4.33 50.83)"


def test_wkt_linestring_flipped_when_latlon():
    assert normalize_wkt("LINESTRING (50.1 4.1, 50.2 4.2)") == "LINESTRING (4.1 50.1, 4.2 50.2)"


def test_wkt_plus_signs_stripped():
    assert normalize_wkt("POINT (+23.135931 54.710154)") == "POINT (23.135931 54.710154)"
    assert normalize_wkt("POINT (+5.4710154E+1 +2.3135931E+1)") == "POINT (2.3135931E+1 5.4710154E+1)"


def test_repo_shapes_compile():
    s = load_shapes(SHAPES)
    keys = {c.key for c in s.classes}
    assert {"operational-point", "section-of-line", "running-track", "tunnel"} <= keys
    sol = next(c for c in s.classes if c.key == "section-of-line")
    assert sol.line_between == ("<http://data.europa.eu/949/opStart>", "<http://data.europa.eu/949/opEnd>")
    track = next(c for c in s.classes if c.key == "running-track")
    lvl = next(f for f in track.facets if f.key == "etcs.etcsLevelType")
    assert lvl.path == "(<http://data.europa.eu/949/etcs>/<http://data.europa.eu/949/etcsLevelType>)"
    assert {f.key for f in s.shared} == {"country", "infrastructureManager"}
    speed = next(f for f in track.facets if f.key == "maximumPermittedSpeed")
    assert speed.color_by == 1 and speed.kind == "range"


def test_color_classes_give_fixed_bands_of_a_range_facet(tmp_path):
    shape = """
PREFIX atlas: <https://rinf-atlas.matdata.eu/ns#>
PREFIX sh: <http://www.w3.org/ns/shacl#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX ex: <http://ex/>
ex:s a sh:NodeShape ; atlas:key "k" ; sh:targetClass ex:C ;
  sh:property [ sh:path ex:p ; atlas:facet "p" ; atlas:kind "{kind}" ; atlas:colorClasses ( {classes} ) ] .
"""
    ok = '[ atlas:upTo 40 ; rdfs:label "Slow" ] [ atlas:upTo 160 ] [ rdfs:label "Fast" ]'
    (tmp_path / "x.ttl").write_text(shape.format(kind="range", classes=ok))
    s = load_shapes(tmp_path)
    assert s.classes[0].facets[0].color_classes == [
        {"up_to": 40.0, "label": "Slow"}, {"up_to": 160.0, "label": None}, {"up_to": None, "label": "Fast"}]
    # Display only: changing the bands needs no reindex.
    (tmp_path / "x.ttl").write_text(shape.format(kind="range", classes='[ atlas:upTo 50 ] [ ]'))
    assert load_shapes(tmp_path).digest() == s.digest()
    for kind, classes in (("terms", ok), ("range", '[ atlas:upTo 40 ]'),  # one class only
                          ("range", '[ atlas:upTo 40 ] [ atlas:upTo 30 ] [ ]'),  # not increasing
                          ("range", '[ atlas:upTo 40 ] [ atlas:upTo 80 ]'),  # last one bounded
                          ("range", '[ ] [ atlas:upTo 80 ] [ ]'),
                          ("range", '[ atlas:upTo "fast" ] [ ]'),
                          ("range", " ".join(f"[ atlas:upTo {i} ]" for i in range(8)) + " [ ]")):  # nine
        (tmp_path / "x.ttl").write_text(shape.format(kind=kind, classes=classes))
        with pytest.raises(ShapeError):
            load_shapes(tmp_path)


def test_color_by_must_be_an_integer_on_a_facet(tmp_path):
    shape = """
PREFIX atlas: <https://rinf-atlas.matdata.eu/ns#>
PREFIX sh: <http://www.w3.org/ns/shacl#>
PREFIX ex: <http://ex/>
ex:s a sh:NodeShape ; atlas:key "k" ; sh:targetClass ex:C ;
  sh:property [ sh:path ex:p ; atlas:facet "p" ; atlas:colorBy {v} ; atlas:kind "{kind}" ] .
"""
    (tmp_path / "x.ttl").write_text(shape.format(v="3", kind="terms"))
    assert load_shapes(tmp_path).classes[0].facets[0].color_by == 3
    for v, kind in (('"soon"', "terms"), ("1", "text")):
        (tmp_path / "x.ttl").write_text(shape.format(v=v, kind=kind))
        with pytest.raises(ShapeError):
            load_shapes(tmp_path)


def test_shape_paths(tmp_path):
    (tmp_path / "x.ttl").write_text("""
PREFIX atlas: <https://rinf-atlas.matdata.eu/ns#>
PREFIX sh: <http://www.w3.org/ns/shacl#>
PREFIX ex: <http://ex/>
ex:s a sh:NodeShape ; atlas:key "k" ; sh:targetClass ex:C ;
  sh:property [ sh:path [ sh:inversePath ex:p ] ; atlas:facet "inv" ] ;
  sh:property [ sh:path [ sh:alternativePath ( ex:a ( ex:b ex:c ) ) ] ; atlas:facet "alt" ] ;
  sh:property [ sh:path [ sh:zeroOrMorePath ex:q ] ; atlas:facet "star" ; atlas:kind "range" ] .
""")
    s = load_shapes(tmp_path)
    paths = {f.key: f.path for f in s.classes[0].facets}
    assert paths == {"inv": "^<http://ex/p>", "alt": "(<http://ex/a>|(<http://ex/b>/<http://ex/c>))",
                     "star": "<http://ex/q>*"}


def test_skolemize_keeps_lists_intact(tmp_path):
    src = tmp_path / "o.nt"
    src.write_text(
        "<http://ex/p> <http://www.w3.org/2000/01/rdf-schema#domain> _:u .\n"
        "_:u <http://www.w3.org/2002/07/owl#unionOf> _:l1 .\n"
        "_:l1 <http://www.w3.org/1999/02/22-rdf-syntax-ns#first> <http://ex/A> .\n"
        "_:l1 <http://www.w3.org/1999/02/22-rdf-syntax-ns#rest> <http://www.w3.org/1999/02/22-rdf-syntax-ns#nil> .\n"
        '<http://ex/x> <http://ex/label> "a _:b ." .\n')
    out = skolemize(src, "http://ex/g")
    st = ox.Store()
    st.bulk_load(path=str(out), format=ox.RdfFormat.N_TRIPLES)
    q = ("SELECT ?d WHERE { <http://ex/p> <http://www.w3.org/2000/01/rdf-schema#domain>/"
         "<http://www.w3.org/2002/07/owl#unionOf>/<http://www.w3.org/1999/02/22-rdf-syntax-ns#first> ?d }")
    assert [r["d"].value for r in st.query(q)] == ["http://ex/A"]
    assert '"a _:b ."' in out.read_text()


def test_link_facets_for_the_line_profile():
    s = load_shapes(SHAPES)
    by = {c.key: {f.key: f for f in c.facets} for c in s.classes}
    assert by["section-of-line"]["opStart"].kind == "link"
    assert by["section-of-line"]["opEnd"].path == "<http://data.europa.eu/949/opEnd>"
    # Tracks find their section through the geometryFrom ("part of") links, not a link facet.
    for cls in ("track", "running-track"):
        assert "partOf" not in by[cls]
        shape = next(c for c in s.classes if c.key == cls)
        assert shape.geometry_from == ["(<http://data.europa.eu/949/isPartOf>|^<http://data.europa.eu/949/hasPart>)"]


def test_link_values_are_iris_only():
    from atlas.extract import _val
    assert _val(ox.NamedNode("http://ex/op"), "link") == "http://ex/op"
    assert _val(ox.Literal("x"), "link") is None


def test_shape_files_and_position_rules_for_the_app():
    s = load_shapes(SHAPES)
    assert "atlas.ttl" in s.files and s.files["track.ttl"].startswith("# Track")
    assert s.class_files["track"] == "track.ttl"
    assert position_rules(next(c for c in s.classes if c.key == "track"))["geometry_from"] == ["era:isPartOf|^era:hasPart"]
    sol = position_rules(next(c for c in s.classes if c.key == "section-of-line"))
    assert sol["line_between"] == ["era:opStart", "era:opEnd"] and sol["geometry_from"] == []


def test_fingerprint_survives_explorer_outage():
    from datetime import datetime, timezone
    pub = Published("ds1", "0087", "SNCF Réseau", "FR", "f.ttl", datetime(2026, 10, 1, tzinfo=timezone.utc), "x", "y")
    fp = _fingerprint(pub, 100)
    assert fp == "ds1|2026-10-01T00:00:00+00:00|100"
    # explorer down: same size keeps the fingerprint, a size change still counts as a change
    assert _fingerprint(None, 100, fp) == fp
    assert _fingerprint(None, 101, fp) != fp
    assert _fingerprint(None, 100, "") == "||100"


def test_clear_graph_in_batches(tmp_path):
    st = ox.Store(str(tmp_path / "st"))
    g, other = ox.NamedNode("http://ex.org/g"), ox.NamedNode("http://ex.org/other")
    p = ox.NamedNode("http://ex.org/p")
    st.extend([ox.Quad(ox.NamedNode(f"http://ex.org/s{i}"), p, ox.Literal(str(i)), g) for i in range(250)])
    st.add(ox.Quad(ox.NamedNode("http://ex.org/s"), p, ox.Literal("keep"), other))
    mirror.clear_graph(st, g, batch=100)
    assert not st.contains_named_graph(g)
    assert not list(st.quads_for_pattern(None, None, None, g))
    assert len(list(st.quads_for_pattern(None, None, None, other))) == 1


def test_geometry_alternatives_tried_in_order():
    # Issue #61: a section with a geometry of its own and one on its net reference shows
    # only the net reference's geometry, so the branches are kept apart and tried in order.
    s = load_shapes(SHAPES)
    sol = next(c for c in s.classes if c.key == "section-of-line")
    assert sol.geometry_paths == [
        "(<http://data.europa.eu/949/netReference>/<http://www.opengis.net/ont/geosparql#hasGeometry>"
        "/<http://www.opengis.net/ont/geosparql#asWKT>)",
        "(<http://www.opengis.net/ont/geosparql#hasGeometry>/<http://www.opengis.net/ont/geosparql#asWKT>)",
    ]
    assert position_rules(sol)["geometry_path"] == ["era:netReference/gsp:hasGeometry/gsp:asWKT",
                                                    "gsp:hasGeometry/gsp:asWKT"]


def test_draw_order():
    s = load_shapes(SHAPES)
    draw = {c.key: c.draw_order for c in s.classes}
    # Issue #53: structures over tracks, tracks over sections, equipment over operational points.
    assert draw["bridge"] > draw["running-track"] > draw["section-of-line"]
    assert draw["tunnel"] > draw["siding"]
    assert draw["signal"] > draw["primary-location"] > draw["operational-point"]
    # Without atlas:drawOrder: below the classes that have one, earlier in the list on top.
    assert draw["paid-up-to"] == -200 < draw["section-of-line"]
