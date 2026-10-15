#!/usr/bin/env python3
"""Smoke check: a track without a contact line is not compatible with an electric
vehicle (reason "No contact line"), and is with a bi-mode one ("not electrified"),
which is also compatible with electrified tracks whose supply or pantograph it lacks.

    tests/compat_contact_line.py http://localhost:8080

Uses the section of tests/fixtures/no-contact-line.ntriples (load it first): one track whose
contact line system is of type "Not electrified" and one without a contact line system.
Standard library only.
"""
import json
import sys
import urllib.request

SOL = "http://example.org/rinf-atlas-test/sol/no-contact-line"


def call(base, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}/api/{path}", data, {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    base = sys.argv[1].rstrip("/")
    rules = {r["key"]: r for r in call(base, "compatibility/rules?env=prod")["rules"]}
    es = rules["energySupplySystem"]
    none = es["none_value"]
    assert none and es["when_missing"] == "incompatible", "energy supply rule has no 'not electrified' option"
    assert es["values"][0]["value"] == none and es["values"][0]["count"] >= 2, f"none option: {es['values'][:1]}"
    real = [v["value"] for v in es["values"] if v["value"] != none] or ["http://data.europa.eu/949/concepts/energy-supply-systems/AC10"]
    electric = {"energySupplySystem": real, "pantographHead": ["http://data.europa.eu/949/concepts/compliant-pantograph-heads/20"]}
    bimode = {**electric, "energySupplySystem": real + [none]}

    x = call(base, "compatibility/explain", {"env": "prod", "iri": SOL, "profile": electric})
    assert x["status"] == "red" and len(x["tracks"]) == 2, f"electric vehicle: {x['status']}, {len(x['tracks'])} tracks"
    for t in x["tracks"]:
        for r in t["results"]:
            assert r["result"] == "fail" and r.get("note") == "No contact line", f"electric vehicle on {t['iri']}: {r}"
    y = call(base, "compatibility/explain", {"env": "prod", "iri": SOL, "profile": bimode})
    assert y["status"] == "green", f"bi-mode vehicle: {y}"

    # a bi-mode vehicle whose supply matches no track still runs everywhere (not electrified)
    other = {"energySupplySystem": ["http://example.org/rinf-atlas-test/energy-supply-systems/none-matches", none],
             "pantographHead": ["http://example.org/rinf-atlas-test/pantograph-heads/none-matches"]}
    o = call(base, "compatibility", {"env": "prod", "profile": other})
    assert o["counts"]["red"] == 0, f"bi-mode vehicle with an unmatched supply: {o['counts']}"
    wired = [s for s in call(base, "compatibility", {"env": "prod", "profile": {
        "energySupplySystem": other["energySupplySystem"][:1]}})["red"] if s != x["id"]]
    assert wired, "no electrified section of line in the data"
    noted = 0
    for sid in wired[:20]:
        z = call(base, "compatibility/explain", {"env": "prod", "id": sid, "profile": other})
        assert z["status"] == "green", f"bi-mode vehicle on section {sid}: {z['status']}"
        # where the track has a supply the vehicle lacks: compatible in "not electrified" mode
        noted += any(r.get("note", "").startswith("Compatible in") for t in z["tracks"] for r in t["results"])
    assert noted, "no 'Compatible in not electrified mode' note on an electrified section"

    e = call(base, "compatibility", {"env": "prod", "profile": electric})
    b = call(base, "compatibility", {"env": "prod", "profile": bimode})
    sid = x["id"]
    assert sid in e["red"] and sid in b["green"], "section status in /api/compatibility"
    print(f"no contact line: electric red, bi-mode green (also where its supply matches no track); sections electric {e['counts']}, bi-mode {b['counts']}")


if __name__ == "__main__":
    main()
