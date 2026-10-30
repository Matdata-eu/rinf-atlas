"""Complete trains (issue #57): the new constraints (requires, options, records, braking,
linked tunnels) and how a train's vehicles combine into one profile. No database."""
import sys
from pathlib import Path

import pytest
from atlas.shapes import ShapeError, load_shapes

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))
from app import compat as cx  # noqa: E402

ATLAS = "https://rinf-atlas.matdata.eu/ns#"
AC25 = "http://data.europa.eu/949/concepts/energy-supply-systems/rinf/AC25000"
BASE = """
PREFIX atlas: <https://rinf-atlas.matdata.eu/ns#>
PREFIX sh: <http://www.w3.org/ns/shacl#>
PREFIX ex: <http://ex/>
ex:t a sh:NodeShape ; atlas:key "track" ; sh:targetClass ex:Track ;
  sh:property [ sh:path ex:gauge ; atlas:facet "gauge" ] ;
  sh:property [ sh:path ex:flag ; atlas:facet "flag" ; atlas:kind "boolean" ] ;
  sh:property [ sh:path ex:len ; atlas:facet "len" ; atlas:kind "range" ] .
ex:s a sh:NodeShape ; atlas:key "sol" ; sh:targetClass ex:Sol .
"""


@pytest.fixture(scope="module")
def rules() -> list[dict]:
    return [vars(r) for r in load_shapes(ROOT / "shapes").compat.rules]


@pytest.fixture(scope="module")
def by_key(rules) -> dict[str, dict]:
    return {r["key"]: r for r in rules}


def _write(tmp_path, rules: str):
    (tmp_path / "x.ttl").write_text(BASE + """
ex:c a sh:NodeShape ; atlas:compatibility true ; atlas:trackClass "track" ; atlas:sectionClass "sol" ;
""" + rules)
    return load_shapes(tmp_path)


def _sound(checks: list[cx.Check]) -> None:
    for c in checks:
        assert c.sql.count("%s") == len(c.params), c.key
        if c.values_sql:
            assert c.values_sql.count("%s") == len(c.values_params), c.key


# --- the repo's rules ------------------------------------------------------------------

def test_repo_train_rules(by_key):
    assert by_key["maxSpeed"]["constraint"] == "input" and not by_key["maxSpeed"]["facets"]
    assert by_key["trainIntegrity"]["constraint"] == "requires"
    assert by_key["raisedPantographs"]["constraint"] == "record"
    assert by_key["brakingDeceleration"]["constraint"] == "brakingDistance"
    assert by_key["brakingDeceleration"]["combine_over"] == "train"
    assert by_key["fireCategory"]["linked_class"] == "tunnel"
    assert by_key["redTailLights"]["combine_over"] == "tail"
    assert by_key["etcsLevel"]["combine_over"] == "leading"
    assert by_key["maxTrainCurrent"]["combine_over"] == "pantograph"
    assert cx.combine_by(by_key["maxTrainCurrent"]) == "sum"
    assert cx.combine_by(by_key["axleLoad"]) == "max" and cx.combine_by(by_key["maxSpeed"]) == "min"
    assert cx.combine_by(by_key["magneticBrake"]) == "union"
    assert [o["label"] for o in by_key["magneticBrake"]["options"]][0] == "Not fitted"


def test_every_new_constraint_compiles(rules):
    profile = {
        "maxSpeed": 140, "raisedPantographs": 2, "pantographSpacing": 20, "brakingDeceleration": 0.7,
        "maxDeceleration": 1.1, "trainIntegrity": [False], "currentLimitation": [True], "redTailLights": [False],
        "quiet": [False], "axleLoad": 21,
        "magneticBrake": [ATLAS + "BrakeService"], "safeConsistLength": [ATLAS + "SafeConsistLengthNone"],
    }
    checks, ignored = cx.compile_profile(rules, profile)
    assert not ignored
    keys = {c.key for c in checks}
    # inputs are read by other rules, never checked themselves
    assert "maxSpeed" not in keys and "pantographSpacing" not in keys
    assert {"raisedPantographs", "brakingDeceleration", "trainIntegrity", "magneticBrake", "axleLoad"} <= keys
    _sound(checks)


def test_fire_category_compares_the_tracks_tunnels(rules, by_key):
    pick = by_key["fireCategory"]["options"][0]["value"]
    (c,), _ = cx.compile_profile(rules, {"fireCategory": [pick]})
    assert "feature_link" in c.sql and c.values_sql and "'from'" in c.values_sql
    _sound([c])


def test_requires(rules):
    (c,), _ = cx.compile_profile(rules, {"trainIntegrity": [True]})
    assert c.sql == str(cx.OK)  # the train has it: every track is compatible
    (c,), _ = cx.compile_profile(rules, {"trainIntegrity": [False]})
    assert f"@> 'true'::jsonb THEN {cx.FAIL} ELSE {cx.OK}" in c.sql
    with pytest.raises(cx.ProfileError):
        cx.compile_profile(rules, {"trainIntegrity": ["yes"]})


def test_options(by_key):
    r = by_key["magneticBrake"]
    service, emergency, none = (ATLAS + "BrakeService", ATLAS + "BrakeEmergencyOnly", ATLAS + "BrakeNotFitted")
    ok, maybe = cx.option_sets(r, [service])
    assert ok and maybe and not set(ok) & set(maybe)
    # a train with both: a track value must suit each vehicle
    ok2, _ = cx.option_sets(r, [service, emergency])
    assert set(ok2) <= set(ok)
    # not fitted adds no restriction
    assert cx.option_sets(r, [service, none])[0] == ok
    (c,), _ = cx.compile_profile([r], {"magneticBrake": [none]})
    assert c.sql == str(cx.OK)
    with pytest.raises(cx.ProfileError, match="not one of the options"):
        cx.option_sets(r, ["http://nope"])


def test_record_picks_the_row_at_the_vehicle_speed(rules):
    (c,), _ = cx.compile_profile(rules, {"raisedPantographs": 2, "maxSpeed": 140})
    assert "ORDER BY" in c.sql and "LIMIT 1" in c.sql and ">= 140.0" in c.sql
    # without a speed every row must allow it; some rows only: unknown
    (c,), _ = cx.compile_profile(rules, {"raisedPantographs": 2})
    assert "bool_and" in c.sql and "bool_or" in c.sql
    _sound([c])


def test_record_note(by_key):
    r = by_key["raisedPantographs"]
    rows = [{"speed": 120, "number": 4}, {"speed": 160, "number": 2}, {"number": 1}]
    note = cx.check_note(r, {"facets": {}}, {"raisedPantographs": 2, "maxSpeed": 100}, rows, cx.OK, by_key)
    assert note == "The row for a speed up to 120 applies (the vehicle's is 100)"
    note = cx.check_note(r, {"facets": {}}, {"raisedPantographs": 2, "maxSpeed": 200}, rows, cx.OK, by_key)
    assert note == "The row without a speed applies"


def test_braking_distance(rules, by_key):
    (c,), _ = cx.compile_profile(rules, {"brakingDeceleration": 0.7, "maxSpeed": 160})
    assert "power" in c.sql and "LEAST(160.0" in c.sql
    _sound([c])
    with pytest.raises(cx.ProfileError, match="more than 0"):
        cx.compile_profile(rules, {"brakingDeceleration": 0})
    b = by_key["brakingDeceleration"]["braking"]
    row = {"facets": {b["caveat_facets"][0]: [True]}}
    assert "more braking requirements" in cx.check_note(by_key["brakingDeceleration"], row,
                                                        {"brakingDeceleration": 0.7}, [1200], cx.UNKNOWN, by_key)


# --- combining a train -------------------------------------------------------------------

def _train(*vehicles, values=None) -> cx.Train:
    return cx.Train(vehicles=[cx.TrainVehicle(**v) for v in vehicles], values=values or {})


LOCO = {"name": "Loco", "roles": ["leading", "pantograph"],
        "values": {"trainLength": 19, "maxSpeed": 140, "axleLoad": 22, "maxTrainCurrent": 800,
                   "energySupplySystem": [AC25], "redTailLights": [True],
                   "magneticBrake": [ATLAS + "BrakeNotFitted"]}}
WAGON = {"name": "Wagon", "count": 20,
         "values": {"trainLength": 15, "maxSpeed": 100, "axleLoad": 20, "redTailLights": [False],
                    "magneticBrake": [ATLAS + "BrakeEmergencyOnly"]}}


def test_combine_a_freight_train(rules):
    p, src = cx.combine(rules, _train(LOCO, WAGON, values={"brakingDeceleration": 0.6}))
    assert p["trainLength"] == 19 + 20 * 15 and src["trainLength"]["from"] == [0, 1]
    assert p["maxSpeed"] == 100 and src["maxSpeed"]["from"] == [1]
    assert p["axleLoad"] == 22 and src["axleLoad"]["from"] == [0]
    assert p["maxTrainCurrent"] == 800  # only the unit with a raised pantograph
    assert p["energySupplySystem"] == [AC25]
    assert p["redTailLights"] == [False] and src["redTailLights"]["from"] == [1]  # the tail
    assert p["magneticBrake"] == sorted([ATLAS + "BrakeEmergencyOnly", ATLAS + "BrakeNotFitted"])
    assert p["brakingDeceleration"] == 0.6 and src["brakingDeceleration"]["entered"]
    # a single pantograph unit left empty: the number of raised pantographs is not checked
    assert "raisedPantographs" not in p


def test_combine_two_units(rules):
    p, src = cx.combine(rules, _train(LOCO, {**LOCO, "roles": ["pantograph"]}))
    assert p["maxTrainCurrent"] == 1600 and p["raisedPantographs"] == 2  # one each by default
    assert p["pantographSpacing"] == 19  # centres of two 19 m units
    assert src["axleLoad"]["from"] == [0, 1]  # tied: both set it


def test_a_vehicle_without_a_value_makes_the_train_unknown(rules):
    wagon = {**WAGON, "values": {k: v for k, v in WAGON["values"].items() if k != "axleLoad"}}
    p, src = cx.combine(rules, _train(LOCO, wagon))
    assert "axleLoad" not in p and src["axleLoad"]["missing"] == [1]


def test_an_entered_train_value_wins(rules):
    p, src = cx.combine(rules, _train(LOCO, WAGON, values={"trainLength": 400}))
    assert p["trainLength"] == 400 and src["trainLength"]["entered"]


def test_intersection_without_common_value(rules):
    other = {**LOCO, "roles": ["pantograph"], "values": {**LOCO["values"], "energySupplySystem": ["http://dc3"]}}
    p, _ = cx.combine(rules, _train(LOCO, other))
    assert p["energySupplySystem"] == [cx.NO_COMMON]


def test_combine_phrases(by_key):
    assert cx.combine_phrase(by_key["maxTrainCurrent"]) == "the sum over the units with a raised pantograph"
    assert cx.combine_phrase(by_key["redTailLights"]) == "the value of the last vehicle"
    assert cx.combine_phrase(by_key["brakingDeceleration"]) == "entered for the whole train"
    assert cx.combine_phrase(by_key["axleLoad"]) == "the highest value among all vehicles"


# --- shape errors ----------------------------------------------------------------------

def test_input_has_no_compare_to(tmp_path):
    with pytest.raises(ShapeError, match="not compared"):
        _write(tmp_path, 'sh:property [ sh:path ex:v ; atlas:characteristic "v" ; atlas:input true ; '
                         'atlas:compareTo "len" ] .')


def test_combine_by_must_fit_the_kind(tmp_path):
    with pytest.raises(ShapeError, match="does not fit"):
        _write(tmp_path, 'sh:property [ sh:path ex:g ; atlas:characteristic "g" ; atlas:compareTo "gauge" ; '
                         'atlas:constraint sh:hasValue ; atlas:combineBy atlas:Sum ] .')


def test_combine_over_must_be_known(tmp_path):
    with pytest.raises(ShapeError, match="combineOver"):
        _write(tmp_path, 'sh:property [ sh:path ex:g ; atlas:characteristic "g" ; atlas:compareTo "gauge" ; '
                         'atlas:constraint sh:hasValue ; atlas:combineOver ex:Nope ] .')


def test_option_needs_value_and_label(tmp_path):
    with pytest.raises(ShapeError, match="atlas:option"):
        _write(tmp_path, 'sh:property [ sh:path ex:g ; atlas:characteristic "g" ; atlas:compareTo "gauge" ; '
                         'atlas:constraint sh:in ; atlas:option [ atlas:matches ex:x ] ] .')


def test_requires_rule(tmp_path):
    cp = _write(tmp_path, 'sh:property [ sh:path ex:r ; atlas:characteristic "r" ; atlas:compareTo "flag" ; '
                          'atlas:constraint atlas:requires ] .').compat
    assert cp.rules[0].constraint == "requires" and cp.rules[0].kind == "boolean"


# --- the code lists the vehicle check offers -------------------------------------------

def test_code_lists_hold_every_concept_of_the_scheme():
    import pyoxigraph as ox
    from atlas.extract import compat_code_lists

    shapes = load_shapes(ROOT / "shapes")
    era, skos = "http://data.europa.eu/949/", "http://www.w3.org/2004/02/skos/core#"
    es = era + "concepts/energy-supply-systems/"
    st = ox.Store()
    st.load(f"""
        <{era}energySupplySystem> <{era}inSkosConceptScheme> <{es}EnergySupplySystems> .
        <{es}AC10> <{skos}inScheme> <{es}EnergySupplySystems> .
        <{es}AC20> <{skos}inScheme> <{es}EnergySupplySystems> .
        <{es}OLD> <{skos}inScheme> <{es}EnergySupplySystems> .
        <{es}OLD> <http://www.w3.org/2002/07/owl#deprecated> true .
    """.encode(), ox.RdfFormat.TURTLE, to_graph=ox.NamedNode("http://g/vocab"))
    out = compat_code_lists(st, shapes)
    assert out["energySupplySystem"] == [es + "AC10", es + "AC20"]
    # option rules and numbers pick from their own choices, not a code list
    assert "magneticBrake" not in out and "trainLength" not in out
