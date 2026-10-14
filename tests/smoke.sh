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
[ -n "$fcols" ] && [ -z "$(echo "$fcols" | tr ' ' '\n' | grep -v '^prod\.operational-point\(\.\(point\|line\|polygon\|other\)\)\?$')" ] \
  || fail "filtered OGC view lists $fcols"
fn=$(curl -fsS "$ogc/filtered/$station/collections/prod.operational-point/items" | json "d['numberMatched']")
sn=$(post facets '{"env":"prod","classes":["operational-point"],"filters":{"operational-point/opType":["http://data.europa.eu/949/concepts/op-types/10"]}}' | json "d['total']")
echo "filtered OGC stations: $fn (facets: $sn)"; [ "$fn" -eq "$sn" ] && [ "$fn" -lt "$ops" ] || fail "filtered OGC view does not match the facets"
# Every listed collection has one geometry type (GIS clients need that); the sample's
# running tracks mix points and lines, so they are split per geometry family.
python3 - "$ogc" <<'PY' || fail "OGC collections with mixed geometry types"
import json, sys, urllib.request
ogc = sys.argv[1]
get = lambda u: json.load(urllib.request.urlopen(u))
ids = [c["id"] for c in get(f"{ogc}/collections")["collections"]]
assert "prod.running-track.point" in ids and "prod.running-track.line" in ids and "prod.running-track" not in ids, ids
for cid in ids:
    c = get(f"{ogc}/collections/{cid}")
    types = {f["geometry"]["type"] for f in get(f"{ogc}/collections/{cid}/items?limit=10000")["features"] if f["geometry"]}
    assert types == {c["geometryType"]}, (cid, types, c.get("geometryType"))
whole = get(f"{ogc}/collections/prod.running-track/items?limit=1")["numberMatched"]  # still served, unlisted
parts = sum(get(f"{ogc}/collections/prod.running-track.{f}/items?limit=1")["numberMatched"] for f in ("point", "line"))
assert whole == parts, (whole, parts)
print(f"OGC geometry types: {len(ids)} collections, running tracks {whole} = points + lines")
PY
curl -fsS "$BASE/api/tiles.json?state=$state" | json "d['tiles'][0]" | grep -q '/api/tiles/{z}/{x}/{y}.pbf?state=' || fail "TileJSON"
curl -fsS "$BASE/api/style.json?env=prod" | json "len(d['layers'])" | grep -qv '^0$' || fail "style.json has no layers"

ds=$(curl -fsS "$BASE/api/datasets?env=prod" | json "sum(1 for x in d if x['status']=='loaded')")
[ "$ds" -ge 1 ] || fail "dataset not listed as loaded"

curl -fsS "$BASE/" | grep -q "<title>RINF Atlas</title>" || fail "web UI not served"
# MapLibre's module worker must be served as JavaScript or the map stays blank.
worker=$(curl -fsS "$BASE/" | grep -o 'assets/index-[^"]*\.js' | head -1)
wpath=$(curl -fsS "$BASE/$worker" | grep -o 'assets/maplibre-gl-worker-[A-Za-z0-9_-]*\.mjs' | head -1)
[ -n "$wpath" ] || fail "MapLibre worker asset not referenced"
curl -fsSI "$BASE/$wpath" | grep -qi '^content-type: application/javascript' || fail "worker not served as JavaScript"
echo "smoke test passed"
