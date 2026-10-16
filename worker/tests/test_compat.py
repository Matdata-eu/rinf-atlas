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
    # no contact line: an electric vehicle is not compatible, unless it is bi-mode
    es = rules["energySupplySystem"]
    assert es.when_missing == "incompatible" and es.none_value == ERA + "concepts/contact-line-systems/40"
    for k in ("pantographHead", "maximumContactWireHeight", "minimumContactWireHeight", "maxTrainCurrent"):
        assert rules[k].when_missing == "incompatible" and rules[k].none_from == "energySupplySystem"
        assert rules[k].missing_reason == "No contact line"
    assert rules["wheelSetGauge"].when_missing == "unknown"
    # no ETCS on board: runs on N, level 0 and NTC tracks only
    et = rules["etcsLevel"]
    lv = ERA + "concepts/etcs-levels/"
    assert et.without_value == lv + "10" and et.without_label == "No ETCS installed"
    assert et.without_matches == [lv + "10", lv + "50", lv + "60"]


def test_repo_rule_facets_are_indexed_on_every_track_class():
    shapes = load_shapes(ROOT / "shapes")
    by_key = {c.key: c for c in shapes.classes}
    assert set(shapes.compat.track_classes) >= {"running-track", "track", "siding"}
    for r in shapes.compat.rules:
        if r.applies_to != "track":
            continue
        for c in shapes.compat.track_classes:
            keys = {f.key for f in shapes.all_facets(by_key[c])}
            assert set(r.facets) <= keys, f"{r.key}: {c} does not index {r.facets}"


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
    assert checks[2].params[-1] == "$[*] ? (@ < 400.0)"
    for one in (None, "http://sol/1"):
        sql, params = cx.status_sql(CDEF, checks, "prod", one)
        assert sql.count("%s") == len(params)
    sql, params = cx.track_detail_sql(CDEF, [c for c in checks if c.applies_to == "track"], "prod", "http://sol/1")
    assert sql.count("%s") == len(params)


@pytest.mark.parametrize("profile", [{"l": "abc"}, {"l": [1, 2]}, {"l": True}, {"g": [1]}, {"x": ["yes"]}])
def test_bad_profile_values(profile):
    with pytest.raises(cx.ProfileError):
        cx.compile_profile(RULES, profile)


def test_when_missing_must_be_known(tmp_path):
    with pytest.raises(ShapeError, match="whenMissing"):
        _write(tmp_path, 'sh:property [ sh:path ex:g ; atlas:characteristic "g" ; atlas:compareTo "gauge" ; '
                         'atlas:constraint sh:hasValue ; atlas:whenMissing atlas:Sometimes ] .')


def test_none_from_must_name_a_rule_with_a_none_value(tmp_path):
    with pytest.raises(ShapeError, match="noneFrom"):
        _write(tmp_path, 'sh:property [ sh:path ex:g ; atlas:characteristic "g" ; atlas:compareTo "gauge" ; '
                         'atlas:constraint sh:hasValue ; atlas:noneFrom "zzz" ] .')


def test_rule_facets_are_indexed_on_every_track_class(tmp_path):
    (tmp_path / "y.ttl").write_text("""
PREFIX atlas: <https://rinf-atlas.matdata.eu/ns#>
PREFIX sh: <http://www.w3.org/ns/shacl#>
PREFIX ex: <http://ex/>
ex:t2 a sh:NodeShape ; atlas:key "siding" ; sh:targetClass ex:Siding .
""")
    shapes = _write(tmp_path, 'atlas:trackClass "siding" ; sh:property [ sh:path ex:g ; atlas:characteristic "g" ; '
                              'atlas:compareTo "gauge" ; atlas:constraint sh:hasValue ] .')
    siding = next(c for c in shapes.classes if c.key == "siding")
    assert [f.key for f in siding.facets] == ["gauge"] and siding.facets[0].path == "<http://ex/gauge>"


NONE = "http://none"
ELEC = [
    {"key": "e", "constraint": "hasValue", "facets": ["es"], "applies_to": "track", "kind": "terms",
     "when_missing": "incompatible", "none_value": NONE, "missing_reason": "No contact line"},
    {"key": "h", "constraint": "maxInclusive", "facets": ["height"], "applies_to": "track", "kind": "number",
     "when_missing": "incompatible", "none_from": "e", "missing_reason": "No contact line"},
    {"key": "g", "constraint": "hasValue", "facets": ["gauge"], "applies_to": "track", "kind": "terms",
     "section_facets": ["gauge"]},
]


def _missing_branch(check: cx.Check) -> str:
    return check.sql.split(" THEN ", 1)[1].split(" WHEN ", 1)[0].strip()


def test_missing_value_is_incompatible_for_an_electric_vehicle():
    (e, h), _ = cx.compile_profile(ELEC, {"e": ["http://ac25"], "h": 6.5})
    assert _missing_branch(e) == str(cx.FAIL)
    # the wire height fails only where there is no contact line, unknown elsewhere
    assert f"THEN {cx.FAIL} ELSE {cx.UNKNOWN} END" in h.sql and "es" in h.params
    for c in (e, h):
        assert c.sql.count("%s") == len(c.params)


def test_bi_mode_vehicle_runs_without_contact_line():
    (e, h), _ = cx.compile_profile(ELEC, {"e": ["http://ac25", NONE], "h": 6.5})
    assert _missing_branch(e) == str(cx.OK)
    assert NONE not in e.params[-1]  # the none value is not matched against the real values
    assert f"THEN {cx.OK} ELSE {cx.UNKNOWN} END" in h.sql


def test_bi_mode_vehicle_is_compatible_with_any_electrified_track():
    """With "not electrified" the vehicle can run without the contact line, so a supply,
    pantograph or wire height that does not match (or is missing) never makes it fail."""
    (e, h, g), _ = cx.compile_profile(ELEC, {"e": ["http://ac25", NONE], "h": 6.5, "g": ["http://g/1435"]})
    assert e.bypass and h.bypass and not g.bypass
    assert e.status == (str(cx.OK), []) and h.status == (str(cx.OK), [])
    assert cx.effective(e, cx.FAIL) == cx.OK and cx.effective(g, cx.FAIL) == cx.FAIL
    # the comparison itself is kept, for explaining why the track would not match
    assert e.sql.endswith(f"ELSE {cx.FAIL} END")
    # the section status only depends on the other characteristics
    cdef = {"section_class": "sol", "track_classes": ["track"]}
    sql, params = cx.status_sql(cdef, [e, h, g], "prod")
    assert sql.count("%s") == len(params) and "es" not in params and "height" not in params
    # only "not electrified": still compatible everywhere for these rules
    (e,), _ = cx.compile_profile(ELEC, {"e": [NONE]})
    assert e.bypass


def test_electric_vehicle_has_no_bypass():
    (e, h), _ = cx.compile_profile(ELEC, {"e": ["http://ac25"], "h": 6.5})
    assert not e.bypass and not h.bypass
    assert e.status[0] == e.sql


def test_pantograph_without_energy_supply_still_fails_without_contact_line():
    (h,), _ = cx.compile_profile(ELEC, {"h": 6.5})
    assert f"THEN {cx.FAIL} ELSE {cx.UNKNOWN} END" in h.sql


def test_default_missing_is_unknown_and_falls_back_to_the_section():
    (g,), _ = cx.compile_profile(ELEC, {"g": ["http://g/1435"]})
    assert _missing_branch(g) == str(cx.UNKNOWN)
    assert "s.facets -> %s" in g.sql and g.sql.count("%s") == len(g.params)


ETCS = [{"key": "l", "constraint": "hasValue", "facets": ["lvl"], "applies_to": "track", "kind": "terms",
         "without_value": "http://N", "without_matches": ["http://N", "http://L0", "http://NTC"]}]


def test_vehicle_without_etcs_matches_tracks_that_do_not_require_it():
    (c,), _ = cx.compile_profile(ETCS, {"l": ["http://N"]})
    assert sorted(c.params[-1]) == ["http://L0", "http://N", "http://NTC"]
    assert _missing_branch(c) == str(cx.UNKNOWN) and not c.bypass
    # with a level as well, both are matched
    (c,), _ = cx.compile_profile(ETCS, {"l": ["http://L1", "http://N"]})
    assert sorted(c.params[-1]) == ["http://L0", "http://L1", "http://N", "http://NTC"]
    # an ETCS vehicle is compared with its own levels only
    (c,), _ = cx.compile_profile(ETCS, {"l": ["http://L2"]})
    assert c.params[-1] == ["http://L2"]


def test_without_value_needs_matches(tmp_path):
    with pytest.raises(ShapeError, match="withoutValue"):
        _write(tmp_path, 'sh:property [ sh:path ex:g ; atlas:characteristic "g" ; atlas:compareTo "gauge" ; '
                         'atlas:constraint sh:hasValue ; atlas:withoutValue ex:none ] .')


def test_repo_axle_load_and_mass_per_metre_rules():
    rules = {r.key: r for r in load_shapes(ROOT / "shapes").compat.rules}
    lc = ERA + "concepts/load-capability-line-categories/"
    al, mm = rules["axleLoad"], rules["massPerMetre"]
    assert al.kind == mm.kind == "number" and al.constraint == mm.constraint == "minInclusive"
    assert al.facets == mm.facets == ["trackLoadCapability.loadCapabilityLineCategory"]
    assert al.term_values[lc + "90"] == 22.5 and mm.term_values[lc + "90"] == 8.0  # D4
    assert al.term_values[lc + "120"] == 25.0 and mm.term_values[lc + "120"] == 8.8  # E5
    assert al.path_factor == 1000 and mm.path_factor is None


LOAD = [{"key": k, "constraint": "minInclusive", "facets": ["cat"], "applies_to": "track", "kind": "number",
         "term_values": tv} for k, tv in
        (("axle", {"http://C2": 20.0, "http://D2": 22.5, "http://C4": 20.0}),
         ("mass", {"http://C2": 6.4, "http://D2": 6.4, "http://C4": 8.0}))]


def test_axle_load_picks_the_categories_that_allow_it():
    (a,), _ = cx.compile_profile(LOAD, {"axle": 21})
    assert a.params[-3] == ["http://D2"] and sorted(a.params[-1]) == ["http://C2", "http://C4", "http://D2"]
    assert a.sql.count("%s") == len(a.params) and a.sql.endswith(f"ELSE {cx.UNKNOWN} END")
    assert _missing_branch(a) == str(cx.UNKNOWN)


def test_one_category_must_allow_both_axle_load_and_mass_per_metre():
    (a, m), _ = cx.compile_profile(LOAD, {"axle": 20, "mass": 7.0})
    assert a.params[-3] == m.params[-3] == ["http://C4"]
    # nothing allows 22.5 t and 8 t/m: every listed category fails
    (a, m), _ = cx.compile_profile(LOAD, {"axle": 22.5, "mass": 8.0})
    assert a.params[-3] == [] and m.params[-3] == []


def test_term_values_need_a_numeric_constraint(tmp_path):
    with pytest.raises(ShapeError, match="termValue"):
        _write(tmp_path, 'sh:property [ sh:path ex:g ; atlas:characteristic "g" ; atlas:compareTo "gauge" ; '
                         'atlas:constraint sh:hasValue ; atlas:termValue [ atlas:term ex:a ; atlas:value 1 ] ] .')


def test_repo_etcs_not_applicable_counts_as_no_etcs():
    shapes = load_shapes(ROOT / "shapes")
    et = next(r for r in shapes.compat.rules if r.key == "etcsLevel")
    assert et.implied == [{"facet": "notApplicable", "when": ERA + "etcs",
                           "value": ERA + "concepts/etcs-levels/10"}]
    # indexed on every track class, also where the class's own shape does not list it
    for c in shapes.classes:
        if c.key in shapes.compat.track_classes:
            assert any(f.key == "notApplicable" for f in shapes.all_facets(c)), c.key


def test_implied_value_is_pooled_with_the_track_values():
    rule = dict(ETCS[0], implied=[{"facet": "na", "when": "http://etcs", "value": "http://N"}])
    (c,), _ = cx.compile_profile([rule], {"l": ["http://N"]})
    assert "facets -> %s, '[]'::jsonb) ? %s THEN jsonb_build_array(%s::text)" in c.sql
    assert c.sql.count("%s") == len(c.params) and ["na", "http://etcs", "http://N"] == c.params[1:4]
    assert cx.implied_values(rule, {"na": ["http://etcs"]}) == ["http://N"]
    assert cx.implied_values(rule, {"na": ["http://other"]}) == []
