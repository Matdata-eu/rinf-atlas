#!/usr/bin/env bash
# End-to-end smoke test: index the bundled RINF sample and query the running stack.
#   docker compose up -d --build db api web
#   tests/smoke.sh            # BASE=http://localhost:8080 by default
set -euo pipefail
BASE="${BASE:-http://localhost:${ATLAS_PORT:-8080}}"
GRAPH="http://data.europa.eu/949/graph/0082"
here="$(cd "$(dirname "$0")" && pwd)"
compose=(docker compose ${COMPOSE_FILES:-})

fail() { echo "FAIL: $*" >&2; exit 1; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"; }

echo "waiting for $BASE/api/health"
for _ in $(seq 1 60); do curl -fsS "$BASE/api/health" >/dev/null 2>&1 && break; sleep 2; done
curl -fsS "$BASE/api/health" >/dev/null || fail "API not healthy"

echo "loading sample graph"
gunzip -c "$here/fixtures/lu-sample.nt.gz" > "$here/fixtures/lu-sample.nt"
"${compose[@]}" run --rm --no-deps -T -v "$here/fixtures:/fixtures:ro" worker \
  atlas load-file --env prod --graph "$GRAPH" /fixtures/lu-sample.nt
rm -f "$here/fixtures/lu-sample.nt"

post() { curl -fsS -X POST "$BASE/api/$1" -H 'content-type: application/json' -d "$2"; }

ops=$(curl -fsS "$BASE/api/meta?env=prod" | json "next(c['count'] for c in d['classes'] if c['class']=='operational-point')")
echo "operational points indexed: $ops"; [ "$ops" -ge 20 ] || fail "expected >= 20 operational points"

total=$(post facets '{"env":"prod","classes":[],"filters":{}}' | json "d['total']")
echo "features: $total"; [ "$total" -gt 40 ] || fail "facets total too small"

lux=$(post facets '{"env":"prod","classes":[],"filters":{"country":["http://publications.europa.eu/resource/authority/country/LUX"]}}' | json "d['total']")
[ "$lux" -gt 0 ] || fail "country facet filter returned nothing"

hits=$(post search '{"env":"prod","classes":["operational-point"],"filters":{},"limit":5}' | json "len(d['items'])")
[ "$hits" -eq 5 ] || fail "search returned $hits items"

bbox=$(post extent '{"env":"prod","classes":[],"filters":{}}' | json "d['bbox']")
echo "extent: $bbox"; [ "$bbox" != "None" ] || fail "no extent"

# The z10 tile at the centre of the data must contain features.
state=$(python3 -c "import base64,json;print(base64.urlsafe_b64encode(json.dumps({'env':'prod','classes':[],'filters':{}}).encode()).decode().rstrip('='))")
tile=$(python3 -c "
import math; w,s,e,n = $bbox; lon,lat = (w+e)/2, (s+n)/2; z = 10; k = 2**z
print(f'{z}/{int((lon+180)/360*k)}/{int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*k)}')")
size=$(curl -fsS "$BASE/api/tiles/$tile.pbf?state=$state" | wc -c)
echo "tile bytes: $size"; [ "$size" -gt 100 ] || fail "empty vector tile"

opstate=$(python3 -c "import base64,json;print(base64.urlsafe_b64encode(json.dumps({'env':'prod','classes':['operational-point'],'filters':{}}).encode()).decode().rstrip('='))")
rows=$(curl -fsS "$BASE/api/export?format=csv&state=$opstate" | python3 -c "import csv,sys; r=list(csv.reader(sys.stdin)); print(len(r)-1 if r[0][0].lstrip('\ufeff')=='IRI' else -1)")
echo "csv export rows: $rows"; [ "$rows" -eq "$ops" ] || fail "CSV export has $rows rows, expected $ops"
curl -fsS "$BASE/api/export?format=xlsx&state=$state" -o /tmp/atlas-export.xlsx
python3 -c "import zipfile,sys; z=zipfile.ZipFile('/tmp/atlas-export.xlsx'); n=sum(1 for f in z.namelist() if f.startswith('xl/worksheets/sheet')); sys.exit(0 if n >= 3 else 1)" \
  || fail "XLSX export is not a workbook with one sheet per class"
rm -f /tmp/atlas-export.xlsx

# Line profile: a national line, its sections in order with track parameters, and a route.
line=$(curl -fsS "$BASE/api/lines?env=prod&q=Luxembourg" | json "d[0]['line']")
echo "line: $line"
prof=$(post profile "$(python3 -c "import json,sys; print(json.dumps({'env':'prod','line':sys.argv[1]}))" "$line")")
echo "$prof" | python3 -c "
import json, sys
d = json.load(sys.stdin); s = d['sections']
assert s, 'no sections'
assert all(abs(a['end_km'] - b['start_km']) < 1e-6 for a, b in zip(s, s[1:])), 'km not cumulative'
assert any(x['tracks'] for x in s), 'no tracks found for the sections'
assert any(x['cells'].get('maximumPermittedSpeed', {}).get('min') for x in s), 'no speed from the tracks'
assert len(d['points']) >= len(s) + 1, 'missing operational points'
print(f\"profile: {len(s)} sections, {d['length_km']} km, {d['gaps']} gaps\")" || fail "line profile"
route=$(echo "$prof" | python3 -c "import json,sys; d=json.load(sys.stdin); s=[x for x in d['sections']]; print(json.dumps({'env':'prod','from':s[0]['from'],'to':s[0]['to']}))")
n=$(post profile "$route" | json "len(d['sections'])")
[ "$n" -ge 1 ] || fail "route profile returned no sections"
curl -fsS "$BASE/api/meta?env=prod" | python3 -c "import json,sys; d=json.load(sys.stdin); sys.exit(any(f['kind']=='link' for f in d['facets']))" \
  || fail "link facets must not be offered as filters"

ds=$(curl -fsS "$BASE/api/datasets?env=prod" | json "sum(1 for x in d if x['status']=='loaded')")
[ "$ds" -ge 1 ] || fail "dataset not listed as loaded"

curl -fsS "$BASE/" | grep -q "<title>RINF Atlas</title>" || fail "web UI not served"
# MapLibre's module worker must be served as JavaScript or the map stays blank.
worker=$(curl -fsS "$BASE/" | grep -o 'assets/index-[^"]*\.js' | head -1)
wpath=$(curl -fsS "$BASE/$worker" | grep -o 'assets/maplibre-gl-worker-[A-Za-z0-9_-]*\.mjs' | head -1)
[ -n "$wpath" ] || fail "MapLibre worker asset not referenced"
curl -fsSI "$BASE/$wpath" | grep -qi '^content-type: application/javascript' || fail "worker not served as JavaScript"
echo "smoke test passed"
