#!/usr/bin/env python3
"""Smoke check: a track without a contact line is not compatible with an electric
vehicle (reason "No contact line"), and is with a bi-mode one ("not electrified").

    tests/compat_contact_line.py http://localhost:8080

Needs at least one section of line with a non-electrified track (the Luxembourg sample
has some). Standard library only.
"""
import json
import sys
import urllib.request


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
    assert es["values"][0]["value"] == none and es["values"][0]["count"] > 0, "no track without a contact line"
    real = [v["value"] for v in es["values"] if v["value"] != none]
    electric = {"energySupplySystem": real}
    bimode = {"energySupplySystem": real + [none]}
    sections = call(base, "search", {"env": "prod", "classes": ["section-of-line"], "filters": {}, "limit": 200})["items"]
    for s in sections:
        x = call(base, "compatibility/explain", {"env": "prod", "id": s["id"], "profile": electric})
        bare = [t for t in x["tracks"] if not t["results"][0]["values"]]
        if not bare:
            continue
        r = bare[0]["results"][0]
        assert r["result"] == "fail" and r.get("note") == "No contact line", f"electric vehicle on {bare[0]['iri']}: {r}"
        y = call(base, "compatibility/explain", {"env": "prod", "id": s["id"], "profile": bimode})
        r2 = next(t for t in y["tracks"] if t["iri"] == bare[0]["iri"])["results"][0]
        assert r2["result"] == "ok", f"bi-mode vehicle on {bare[0]['iri']}: {r2}"
        e = call(base, "compatibility", {"env": "prod", "profile": electric})["counts"]
        b = call(base, "compatibility", {"env": "prod", "profile": bimode})["counts"]
        assert b["red"] <= e["red"], f"electric {e}, bi-mode {b}"
        print(f"no contact line on {bare[0]['iri']}: electric fails, bi-mode ok; sections electric {e}, bi-mode {b}")
        return
    raise AssertionError("no section of line with a track without a contact line")


if __name__ == "__main__":
    main()
