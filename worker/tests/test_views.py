"""Views and their requirement checks (shapes/views/*.ttl): loading and the check SQL. No database."""
from dataclasses import asdict
from pathlib import Path

import pytest
from atlas.shapes import ShapeError, load_shapes
from atlas.views import status_sql

ROOT = Path(__file__).resolve().parents[2]
ERA = "http://data.europa.eu/949/"
BASE = """
PREFIX atlas: <https://rinf-atlas.matdata.eu/ns#>
PREFIX sh: <http://www.w3.org/ns/shacl#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX ex: <http://ex/>
ex:t a sh:NodeShape ; atlas:key "track" ; sh:targetClass ex:Track ;
  sh:property [ sh:path ex:gauge ; atlas:facet "gauge" ] ;
  sh:property [ sh:path ex:level ; atlas:facet "level" ] ;
  sh:property [ sh:path ex:len ; atlas:facet "len" ; atlas:kind "range" ] .
ex:s a sh:NodeShape ; atlas:key "sol" ; sh:targetClass ex:Sol .
"""
VIEW = """
ex:v a sh:NodeShape ; atlas:view "v" ; rdfs:label "V" ;
  atlas:viewClass "sol", "track" ; atlas:groupBy "gauge" ;
  atlas:checkClass "track" ; atlas:checkSection "sol" ;
  sh:property [ atlas:facet "len" ; sh:order 1 ] {checks} .
"""
LEN = """; sh:property [ atlas:check "v.len" ; rdfs:label "Long" ; atlas:compareTo "len" ; sh:minInclusive 740 ]"""
GAUGE = """; sh:property [ atlas:check "v.gauge" ; atlas:compareTo "gauge" ; sh:hasValue ex:std ;
  atlas:appliesWhen [ atlas:facet "level" ; sh:in ( ex:core ) ] ]"""


def load(tmp_path, checks: str, extra: str = ""):
    (tmp_path / "x.ttl").write_text(BASE + VIEW.format(checks=checks) + extra)
    return load_shapes(tmp_path)


def test_repo_views_compile():
    s = load_shapes(ROOT / "shapes")
    views = {v.key: v for v in s.views}
    assert set(views) >= {"corridors", "freight-corridors", "ertms", "electrification"}
    tent = views["corridors"]
    assert tent.summary_key == "tent.all" and tent.section_class == "section-of-line"
    checks = {c.key: c for c in tent.checks}
    assert checks["tent.electrification"].when_missing == "fails"
    assert checks["tent.electrification"].constraint == "notIn"
    assert checks["tent.ertms"].implied
    # the groups come from RINF: every checked class has the corridor facet
    for cls in ("running-track", "track", "section-of-line"):
        keys = {f.key for f in s.all_facets(next(c for c in s.classes if c.key == cls))}
        assert {"corridor", "partOfTENT.tentNetworkLevel"} <= keys, cls
    assert views["freight-corridors"].checks == []


def test_summary_only_with_two_checks(tmp_path):
    one = load(tmp_path, LEN).views[0]
    assert one.summary_key is None
    two = load(tmp_path, LEN + GAUGE).views[0]
    assert two.summary_key == "v.all"
    assert {c.key for c in two.checks} == {"v.len", "v.gauge"}


def test_digest_follows_the_checks_only(tmp_path):
    a = load(tmp_path, LEN).views_digest()
    (tmp_path / "x.ttl").write_text((tmp_path / "x.ttl").read_text().replace('rdfs:label "V"', 'rdfs:label "W"'))
    assert load_shapes(tmp_path).views_digest() == a
    assert load(tmp_path, LEN.replace("740", "750")).views_digest() != a


@pytest.mark.parametrize("checks, msg", [
    (LEN.replace('"len" ;', '"nope" ;'), "nope"),
    (LEN + LEN, "v.len"),
    (LEN.replace('"v.len"', '"gauge"'), "gauge"),
    (LEN.replace("sh:minInclusive 740", "sh:minInclusive 740 ; atlas:whenMissing atlas:Whatever"), "whenMissing"),
])
def test_bad_checks_are_refused(tmp_path, checks, msg):
    with pytest.raises(ShapeError, match=msg):
        load(tmp_path, checks)


def test_check_sql_has_one_parameter_per_placeholder(tmp_path):
    for v in load_shapes(ROOT / "shapes").views + load(tmp_path, LEN + GAUGE).views:
        for c in asdict(v)["checks"]:
            sql, params = status_sql(c)
            assert sql.count("%s") == len(params), c["key"]


def test_applies_when_wraps_the_check(tmp_path):
    g = next(c for c in asdict(load(tmp_path, LEN + GAUGE).views[0])["checks"] if c["key"] == "v.gauge")
    sql, params = status_sql(g)
    assert sql.startswith("CASE WHEN EXISTS") and sql.endswith("END END")
    assert params[:2] == ["level", '["http://ex/core"]']
