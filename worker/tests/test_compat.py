"""Vehicle compatibility: rule loading (worker) and profile compilation (API). No database."""
import sys
from pathlib import Path

import pytest
from atlas.shapes import ShapeError, load_shapes

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))
from app import compat as cx  # noqa: E402

ERA = "http://data.europa.eu/949/"
BASE = """
PREFIX atlas: <https://rinf-atlas.matdata.eu/ns#>
PREFIX sh: <http://www.w3.org/ns/shacl#>
PREFIX ex: <http://ex/>
ex:t a sh:NodeShape ; atlas:key "track" ; sh:targetClass ex:Track ;
  sh:property [ sh:path ex:gauge ; atlas:facet "gauge" ] ;
  sh:property [ sh:path ex:len ; atlas:facet "len" ; atlas:kind "range" ] .
ex:s a sh:NodeShape ; atlas:key "sol" ; sh:targetClass ex:Sol .
"""


def test_repo_rules_compile():
    cp = load_shapes(ROOT / "shapes").compat
    assert cp is not None and cp.section_class == "section-of-line"
    assert "running-track" in cp.track_classes
    rules = {r.key: r for r in cp.rules}
    assert rules["wheelSetGauge"].path == [ERA + "wheelSetGauge"]
    assert rules["pantographHead"].facets == ["otherPantographHead", "tsiPantographHead"]
    assert rules["pantographHead"].path == [ERA + "configDependentParameter", ERA + "vehiclePantographHead"]
    assert rules["trainLength"].kind == "number" and rules["trainLength"].constraint == "minInclusive"
    assert rules["trainDetectionSystemType"].constraint == "in"


def _write(tmp_path, rules: str):
    (tmp_path / "x.ttl").write_text(BASE + """
ex:c a sh:NodeShape ; atlas:compatibility true ; atlas:trackClass "track" ; atlas:sectionClass "sol" ;
""" + rules)
    return load_shapes(tmp_path)


def test_rule_on_unknown_facet_is_an_error(tmp_path):
    with pytest.raises(ShapeError, match="not defined"):
        _write(tmp_path, 'sh:property [ sh:path ex:g ; atlas:characteristic "g" ; atlas:compareTo "nope" ; '
                         'atlas:constraint sh:hasValue ] .')


def test_numeric_constraint_needs_range_facet(tmp_path):
    with pytest.raises(ShapeError, match="does not fit"):
        _write(tmp_path, 'sh:property [ sh:path ex:g ; atlas:characteristic "g" ; atlas:compareTo "gauge" ; '
                         'atlas:constraint sh:minInclusive ] .')


def test_rules_from_shapes(tmp_path):
    cp = _write(tmp_path, """
  sh:property [ sh:path ex:g ; atlas:characteristic "g" ; atlas:compareTo "gauge" ; atlas:constraint sh:in ; sh:order 2 ] ;
  sh:property [ sh:path ( ex:a ex:b ) ; atlas:characteristic "l" ; atlas:compareTo "len" ;
                atlas:constraint sh:maxInclusive ; sh:order 1 ] .""").compat
    assert [r.key for r in cp.rules] == ["l", "g"]
    assert cp.rules[0].path == ["http://ex/a", "http://ex/b"] and cp.rules[0].kind == "number"


RULES = [
    {"key": "g", "constraint": "hasValue", "facets": ["gauge"], "applies_to": "track", "kind": "terms"},
    {"key": "d", "constraint": "in", "facets": ["a", "b"], "applies_to": "track", "kind": "terms"},
    {"key": "l", "constraint": "minInclusive", "facets": ["len"], "applies_to": "section", "kind": "number"},
    {"key": "x", "constraint": "hasValue", "facets": ["flag"], "applies_to": "track", "kind": "boolean"},
]
CDEF = {"track_classes": ["track"], "section_class": "sol", "rules": RULES}


def test_empty_values_are_not_checked():
    checks, ignored = cx.compile_profile(RULES, {"g": [], "l": None, "d": "", "zzz": 1})
    assert checks == [] and ignored == ["zzz"]


def test_compiled_sql_has_one_param_per_placeholder():
    checks, _ = cx.compile_profile(RULES, {"g": ["http://g/1435"], "d": ["u", "v"], "l": 400, "x": [True]})
    assert [c.key for c in checks] == ["g", "d", "l", "x"]
    for c in checks:
        assert c.sql.count("%s") == len(c.params)
    assert checks[2].params[-1] == '$."len"[*] ? (@ < 400.0)'
    for one in (None, "http://sol/1"):
        sql, params = cx.status_sql(CDEF, checks, "prod", one)
        assert sql.count("%s") == len(params)
    sql, params = cx.track_detail_sql(CDEF, [c for c in checks if c.applies_to == "track"], "prod", "http://sol/1")
    assert sql.count("%s") == len(params)


@pytest.mark.parametrize("profile", [{"l": "abc"}, {"l": [1, 2]}, {"l": True}, {"g": [1]}, {"x": ["yes"]}])
def test_bad_profile_values(profile):
    with pytest.raises(cx.ProfileError):
        cx.compile_profile(RULES, profile)
