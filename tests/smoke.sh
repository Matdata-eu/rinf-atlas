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

# Map colouring: sections of line take the speed of their running tracks.
speed="running-track/maximumPermittedSpeed"
theme=$(post theme "{\"env\":\"prod\",\"classes\":[],\"filters\":{},\"key\":\"$speed\"}")
echo "theme: $(echo "$theme" | json "(d['kind'], d['rollup'], len(d['bins']), d['total'])")"
echo "$theme" | json "d['kind']=='range' and 'section-of-line' in d['rollup'] and sum(b['count'] for b in d['bins']) > 0" | grep -q True \
  || fail "colouring by $speed gave no classes or no roll-up to sections of line"
energy=$(post theme '{"env":"prod","classes":[],"filters":{},"key":"running-track/contactLineSystem.energySupplySystem"}' \
  | json "sum(v['count'] for v in d['values'])")
[ "$energy" -gt 0 ] || fail "colouring by energy supply system found no values"
ctile=$(curl -fsS "$BASE/api/tiles/$tile.pbf?state=$state&color=$speed" | wc -c)
echo "coloured tile bytes: $ctile"; [ "$ctile" -gt "$size" ] || fail "coloured tile carries no values"
solv=$(post search "{\"env\":\"prod\",\"classes\":[\"section-of-line\"],\"filters\":{},\"limit\":50,\"color\":\"$speed\"}" \
  | json "sum(1 for i in d['items'] if i.get('v') is not None)")
echo "sections of line with a rolled-up speed: $solv"; [ "$solv" -gt 0 ] || fail "no section of line got its tracks' speed"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$BASE/api/theme" -H 'content-type: application/json' \
  -d '{"env":"prod","classes":[],"filters":{},"key":"running-track/trackId"}')
[ "$code" = 400 ] || fail "colouring by a text facet should be refused, got HTTP $code"

opstate=$(python3 -c "import base64,json;print(base64.urlsafe_b64encode(json.dumps({'env':'prod','classes':['operational-point'],'filters':{}}).encode()).decode().rstrip('='))")
rows=$(curl -fsS "$BASE/api/export?format=csv&state=$opstate" | python3 -c "import csv,sys; r=list(csv.reader(sys.stdin)); print(len(r)-1 if r[0][0].lstrip('\ufeff')=='IRI' else -1)")
echo "csv export rows: $rows"; [ "$rows" -eq "$ops" ] || fail "CSV export has $rows rows, expected $ops"
curl -fsS "$BASE/api/export?format=xlsx&state=$state" -o /tmp/atlas-export.xlsx
python3 -c "import zipfile,sys; z=zipfile.ZipFile('/tmp/atlas-export.xlsx'); n=sum(1 for f in z.namelist() if f.startswith('xl/worksheets/sheet')); sys.exit(0 if n >= 3 else 1)" \
  || fail "XLSX export is not a workbook with one sheet per class"
rm -f /tmp/atlas-export.xlsx

# GIS access: OGC API - Features (unfiltered and filtered) and TileJSON / style.
ogc="$BASE/api/ogc"
curl -fsS "$ogc" | json "' '.join(l['rel'] for l in d['links'])" | grep -qw conformance || fail "OGC landing page lacks links"
curl -fsS "$ogc/conformance" | grep -q 'ogcapi-features-1/1.0/conf/geojson' || fail "OGC conformance incomplete"
curl -fsS "$ogc/collections" | json "' '.join(c['id'] for c in d['collections'])" | grep -qw prod.operational-point \
  || fail "no prod.operational-point collection"
ogcn=$(python3 - "$ogc/collections/prod.operational-point/items?limit=7" <<'PY'
import json, sys, urllib.request
url, ids, matched = sys.argv[1], [], None
while url:  # follow the next links like a GIS client does
    d = json.load(urllib.request.urlopen(url))
    assert all(f["type"] == "Feature" and "opType" in f["properties"] for f in d["features"])
    ids += [f["id"] for f in d["features"]]
    matched = d["numberMatched"]
    url = next((link["href"] for link in d["links"] if link["rel"] == "next"), None)
assert len(ids) == len(set(ids)) == matched, (len(ids), len(set(ids)), matched)
print(len(ids))
PY
) || fail "OGC items paging broken"
echo "OGC items (paged): $ogcn"; [ "$ogcn" -eq "$ops" ] || fail "OGC items has $ogcn operational points, expected $ops"
inbox=$(curl -fsS "$ogc/collections/prod.operational-point/items?bbox=$(echo "$bbox" | tr -d '[] ')&limit=1" | json "d['numberMatched']")
[ "$inbox" -eq "$ops" ] || fail "OGC bbox of the whole extent returned $inbox"
crs=$(curl -fsS -D - -o /dev/null "$ogc/collections/prod.tunnel/items?crs=http://www.opengis.net/def/crs/EPSG/0/3857" | tr -d '\r' | sed -n 's/^content-crs: //Ip')
[ "$crs" = "<http://www.opengis.net/def/crs/EPSG/0/3857>" ] || fail "OGC crs parameter ignored ($crs)"
long=$(curl -fsS "$ogc/collections/prod.tunnel/items?lengthOfTunnel=420..&limit=100" | json "len(d['features'])")
all=$(curl -fsS "$ogc/collections/prod.tunnel/items?limit=100" | json "len(d['features'])")
echo "tunnels >= 420 m: $long of $all"; [ "$long" -ge 1 ] && [ "$long" -lt "$all" ] || fail "OGC range facet parameter"
code=$(curl -s -o /dev/null -w '%{http_code}' "$ogc/collections/prod.tunnel/items?noSuchFacet=1")
[ "$code" = 400 ] || fail "unknown OGC parameter gave $code, expected 400"
fid=$(curl -fsS "$ogc/collections/prod.tunnel/items?limit=1" | json "d['features'][0]['id']")
curl -fsS "$ogc/collections/prod.tunnel/items/$fid" | json "d['properties']['iri']" | grep -q '^http' || fail "OGC single item"
station=$(python3 -c "import base64,json;print(base64.urlsafe_b64encode(json.dumps({'env':'prod','classes':['operational-point'],'filters':{'operational-point/opType':['http://data.europa.eu/949/concepts/op-types/10']}}).encode()).decode().rstrip('='))")
fcols=$(curl -fsS "$ogc/filtered/$station/collections" | json "' '.join(c['id'] for c in d['collections'])")
[ "$fcols" = prod.operational-point ] || fail "filtered OGC view lists $fcols"
fn=$(curl -fsS "$ogc/filtered/$station/collections/prod.operational-point/items" | json "d['numberMatched']")
sn=$(post facets '{"env":"prod","classes":["operational-point"],"filters":{"operational-point/opType":["http://data.europa.eu/949/concepts/op-types/10"]}}' | json "d['total']")
echo "filtered OGC stations: $fn (facets: $sn)"; [ "$fn" -eq "$sn" ] && [ "$fn" -lt "$ops" ] || fail "filtered OGC view does not match the facets"
curl -fsS "$BASE/api/tiles.json?state=$state" | json "d['tiles'][0]" | grep -q '/api/tiles/{z}/{x}/{y}.pbf?state=' || fail "TileJSON"
curl -fsS "$BASE/api/style.json?env=prod" | json "len(d['layers'])" | grep -qv '^0$' || fail "style.json has no layers"
# Vehicle compatibility: standard gauge fits every section of the sample, Iberian gauge none.
nrules=$(curl -fsS "$BASE/api/compatibility/rules?env=prod" | json "len(d['rules'])")
echo "compatibility rules: $nrules"; [ "$nrules" -ge 5 ] || fail "compatibility rules missing"
sols=$(curl -fsS "$BASE/api/meta?env=prod" | json "next(c['count'] for c in d['classes'] if c['class']=='section-of-line')")
gauge="http://data.europa.eu/949/concepts/nominal-track-gauges"
green=$(post compatibility "{\"env\":\"prod\",\"profile\":{\"wheelSetGauge\":[\"$gauge/30\"]}}" | json "d['counts']['green']")
red=$(post compatibility "{\"env\":\"prod\",\"profile\":{\"wheelSetGauge\":[\"$gauge/70\"]}}" | json "d['counts']['red']")
echo "sections of line: $sols, 1435 mm green: $green, 1668 mm red: $red"
[ "$green" -ge 1 ] || fail "no section compatible with standard gauge"
[ "$red" -eq "$sols" ] || fail "Iberian gauge should be incompatible with every section ($red of $sols)"
first=$(post compatibility "{\"env\":\"prod\",\"profile\":{\"wheelSetGauge\":[\"$gauge/30\"]}}" | json "d['green'][0]")
why=$(curl -fsS "$BASE/api/feature?env=prod&id=$first" | json "d['iri']")
tracks=$(post compatibility/explain "{\"env\":\"prod\",\"iri\":\"$why\",\"profile\":{\"wheelSetGauge\":[\"$gauge/30\"],\"trainLength\":5000}}" \
  | json "str(d['status']) + ' ' + str(len(d['tracks']))")
echo "explain $why: $tracks"; [ "${tracks%% *}" != "green" ] || fail "a 5 km train should not fit ($tracks)"
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
