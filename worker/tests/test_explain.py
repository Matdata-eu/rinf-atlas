"""Explaining a compatibility rule in words (api/app/compat.explain_rule) and the rule's
Turtle and compared facets the worker publishes for it. No database."""
import sys
from pathlib import Path

import pyoxigraph as ox
import pytest
from atlas.shapes import load_shapes

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api"))
from app import compat as cx  # noqa: E402

NONE = "http://none"


def rule(**kw) -> dict:
    r = {"key": "x", "label": "Thing", "constraint": "hasValue", "facets": ["f"], "applies_to": "track",
         "kind": "terms", "path": ["http://data.europa.eu/949/thing"], "when_missing": "unknown",
         "infra": [{"key": "f", "label": "Track thing", "path": "era:thing", "classes": ["track"]}]}
    r.update(kw)
    return r


def explain(r: dict, *others: dict, **kw) -> dict[str, str]:
    by_key = {x["key"]: x for x in (r, *others)}
    out = cx.explain_rule(r, by_key, ["running-track", "siding"], "sol", {NONE: "Not electrified"},
                          {"running-track": "Running track", "siding": "Siding", "sol": "Section of line"}, **kw)
    assert [s["topic"] for s in out["steps"]][0] == "compare"
    return {s["topic"]: s["text"] for s in out["steps"]} | {"path": out["vehicle_path"]}


def test_has_value():
    t = explain(rule())
    assert "at least one of the vehicle's values" in t["compare"] and "“Track thing”" in t["compare"]
    assert "“Thing”" in t["compare"] and "sh:hasValue" in t["compare"]
    assert t["path"] == "era:thing"


def test_in():
    t = explain(rule(constraint="in"))
    assert "Every value on the track must be one of the vehicle's" in t["compare"] and "sh:in" in t["compare"]


@pytest.mark.parametrize("c, words, sym, bad", [
    ("minInclusive", "at least", "≥", "lower"), ("maxInclusive", "at most", "≤", "higher"),
    ("minExclusive", "more than", ">", "equal or lower"), ("maxExclusive", "less than", "<", "equal or higher"),
])
def test_numeric(c, words, sym, bad):
    t = explain(rule(constraint=c, kind="number", unit="MilliM"))
    assert f"must be {words} the vehicle's “Thing”" in t["compare"]
    assert f"track value {sym} vehicle value, in mm" in t["compare"] and f"is {bad}." in t["compare"]


def test_unknown_unit_is_shown_as_is():
    assert "in furlong" in explain(rule(constraint="maxInclusive", kind="number", unit="furlong"))["compare"]


def test_pooled_facets():
    r = rule(facets=["a", "b"], infra=[{"key": "a", "label": "A"}, {"key": "b", "label": "B"}])
    assert "values of “A” and “B” are taken together" in explain(r)["compare"]


def test_when_missing_unknown():
    t = explain(rule())
    assert t["missing"].endswith("is unknown: the data cannot tell.") and "none" not in t


def test_when_missing_incompatible_with_none_value():
    t = explain(rule(when_missing="incompatible", none_value=NONE, missing_reason="No contact line"))
    assert "is not compatible, with the reason “No contact line”." in t["missing"]
    assert "adds “Not electrified” to its values" in t["none"] and "Compatible in “Not electrified” mode" in t["none"]


def test_none_from():
    e = rule(key="e", label="Energy supply", when_missing="incompatible", none_value=NONE)
    h = rule(key="h", label="Height", constraint="maxInclusive", kind="number", when_missing="incompatible",
             none_from="e", missing_reason="No contact line")
    t = explain(h, e)
    assert "only when it has no “Energy supply” value either" in t["missing"] and "unknown" in t["missing"]
    assert "When the vehicle's “Energy supply” includes “Not electrified”" in t["none"]


def test_section_fallback_and_rollup():
    t = explain(rule(section_facets=["f"]))
    assert "value of its section of line" in t["fallback"] and "“Track thing”" in t["fallback"]
    assert "running track or siding" in t["rollup"] and "best track" in t["rollup"]
    assert "not checked" in t["empty"]


def test_section_rule():
    t = explain(rule(applies_to="section"))
    assert t["compare"].startswith("The section of line must offer") and "not per track" in t["rollup"]
    assert "fallback" not in t


def test_old_defs_without_infra_still_explain():
    r = rule()
    del r["infra"]
    assert "“f”" in explain(r)["compare"]


def test_every_repo_rule_is_explained_with_its_source_and_facets():
    cp = load_shapes(ROOT / "shapes").compat
    rules = [vars(r) for r in cp.rules]
    by_key = {r["key"]: r for r in rules}
    for r in rules:
        out = cx.explain_rule(r, by_key, cp.track_classes, cp.section_class)
        assert out["vehicle_path"].startswith(("era:", "atlas:"))  # atlas: where ERA has no property
        # an input (the maximum speed) is not compared itself, only read by other rules
        need = {"compare", "empty"} if r["constraint"] == "input" else {"compare", "missing", "rollup", "empty"}
        assert {s["topic"] for s in out["steps"]} >= need, r["key"]
        # the compared facets, then those that pick rows or the speed (record filter, braking)
        assert [f["key"] for f in r["infra"]][:len(r["facets"])] == r["facets"]
        assert all(f["path"] for f in r["infra"])
        # the Turtle is valid and holds the rule
        st = ox.Store()
        st.load(r["source"].encode(), ox.RdfFormat.TURTLE)
        keys = [q.object.value for q in st.quads_for_pattern(
            None, ox.NamedNode("https://rinf-atlas.matdata.eu/ns#characteristic"), None)]
        # the rule, and the characteristics it reads (the maximum speed of a record row)
        assert r["key"] in keys and set(keys) <= {r["key"]} | cx._reads(r), r["source"]
    es = by_key["energySupplySystem"]
    assert es["infra"][0]["path"] == "era:contactLineSystem/era:energySupplySystem"
    assert "atlas:noneValue <http://data.europa.eu/949/concepts/contact-line-systems/40>" in es["source"]
    assert "sh:path ( era:configDependentParameter era:vehiclePantographHead )" in by_key["pantographHead"]["source"]


def test_without_value():
    t = explain(rule(without_value="http://N", without_label="No ETCS installed",
                     without_matches=["http://N", "http://L0"]))
    assert "picks “No ETCS installed”" in t["without"] and "“N” or “L0”" in t["without"]
    assert "requires the system" in t["without"]


def test_term_values():
    t = explain(rule(key="axle", label="Axle load", constraint="minInclusive", kind="number", unit="TONNE",
                     term_values={"http://x/D4": 22.5, "http://x/C2": 20.0}))
    assert "C2 20 and D4 22.5" in t["compare"] and "in t" in t["compare"] and "at least one" in t["compare"]
