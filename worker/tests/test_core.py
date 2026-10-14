"""Unit tests that need no database or network: `python -m pytest worker/tests`."""
from pathlib import Path

import pyoxigraph as ox
import pytest
from atlas.extract import normalize_wkt
from atlas.mirror import skolemize
from atlas.shapes import ShapeError, load_shapes

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
