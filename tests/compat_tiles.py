#!/usr/bin/env python3
"""Smoke check: the vector tiles the vehicle compatibility layer colours carry the ids of
the /api/compatibility result, at every zoom the map draws sections at.

    tests/compat_tiles.py http://localhost:8080 '{"wheelSetGauge": [...]}'

For a few sections of line it fetches the tile under the section at each zoom in ZOOMS
and checks that the tile holds sections, all with ids the result knows, the section
itself among them. Prints the result's ids (sorted, one line) for comparison by the caller.
Standard library only: the tiles are decoded with a minimal protobuf reader.
"""
import base64
import json
import math
import sys
import urllib.request

ZOOMS = (3, 5, 8, 11, 14)


def post(base, path, body):
    req = urllib.request.Request(f"{base}/api/{path}", json.dumps(body).encode(),
                                 {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def get(base, path):
    with urllib.request.urlopen(f"{base}/api/{path}", timeout=60) as r:
        return r.read()


def varint(b, i):
    n = shift = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << shift
        shift += 7
        if c < 0x80:
            return n, i


def fields(b):
    """(field number, wire type, value) of one protobuf message; value is an int or bytes."""
    i = 0
    while i < len(b):
        key, i = varint(b, i)
        f, wt = key >> 3, key & 7
        if wt == 0:
            v, i = varint(b, i)
        elif wt == 2:
            n, i = varint(b, i)
            v, i = b[i:i + n], i + n
        elif wt == 1:
            v, i = b[i:i + 8], i + 8
        elif wt == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError(f"wire type {wt}")
        yield f, wt, v


def tile_ids(data):
    """Feature ids per layer name of a Mapbox vector tile."""
    out = {}
    for f, _, layer in fields(data):
        if f != 3:  # Tile.layers
            continue
        name, ids = None, []
        for lf, _, v in fields(layer):
            if lf == 1:
                name = v.decode()
            elif lf == 2:  # Layer.features
                fid = next((x for ff, wt, x in fields(v) if ff == 1 and wt == 0), None)
                ids.append(fid)
        out[name] = ids
    return out


def tile_of(lon, lat, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    lat_r = math.radians(max(min(lat, 85.0511), -85.0511))
    y = int((1 - math.asinh(math.tan(lat_r)) / math.pi) / 2 * n)
    return x, y


def main():
    base, profile = sys.argv[1].rstrip("/"), json.loads(sys.argv[2])
    rules = json.loads(get(base, "compatibility/rules?env=prod"))
    res = post(base, "compatibility", {"env": "prod", "profile": profile})
    known = {i for c in ("green", "yellow", "red") for i in res[c]}
    assert known, "the compatibility result is empty"
    state = {"env": "prod", "classes": [rules["section_class"]], "filters": {}}
    st = base64.urlsafe_b64encode(json.dumps(state).encode()).decode().rstrip("=")
    secs = post(base, "search", {"env": "prod", "classes": [rules["section_class"]], "filters": {}, "limit": 5})["items"]
    secs = [s for s in secs if s.get("lon") is not None]
    assert secs, "no section of line with a geometry"
    for z in ZOOMS:
        seen = set()
        for s in secs:
            x, y = tile_of(s["lon"], s["lat"], z)
            ids = tile_ids(get(base, f"tiles/{z}/{x}/{y}.pbf?state={st}&v={res.get('version', '')}")).get("features", [])
            assert ids, f"z{z}: no section in tile {z}/{x}/{y}"
            assert None not in ids, f"z{z}: a feature without id in tile {z}/{x}/{y}"
            unknown = set(ids) - known
            assert not unknown, f"z{z}: tile {z}/{x}/{y} has ids the result does not know: {sorted(unknown)[:5]}"
            seen |= set(ids)
        # Below z10 lines are simplified and pixel-duplicates sent once, so a short
        # section can be left out; from z10 every section is in its tile.
        if z >= 10:
            missing = [s["id"] for s in secs if s["id"] not in seen]
            assert not missing, f"z{z}: sections {missing} are not in their tiles"
        print(f"z{z}: {len(seen)} section ids, all in the result", file=sys.stderr)
    print(" ".join(str(i) for i in sorted(known)))


if __name__ == "__main__":
    main()
