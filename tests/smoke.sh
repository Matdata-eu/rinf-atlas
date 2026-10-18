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
# gc-op.nt adds an operational point with a point and a polygon (a GeometryCollection),
# kp-nogeom.nt a kilometric post without any geometry (like DB InfraGO's).
gunzip -c "$here/fixtures/lu-sample.nt.gz" | cat - "$here/fixtures/gc-op.nt" "$here/fixtures/kp-nogeom.nt" > "$here/fixtures/lu-sample.nt"
"${compose[@]}" run --rm --no-deps -T -v "$here/fixtures:/fixtures:ro" worker \
  atlas load-file --env prod --graph "$GRAPH" /fixtures/lu-sample.nt
rm -f "$here/fixtures/lu-sample.nt"

# ERA's SHACL shapes arrive like a vocabulary graph; loading them validates the sample.
echo "loading ERA SHACL shapes (validates the sample)"
gunzip -c "$here/fixtures/era-shacl.nt.gz" > "$here/fixtures/era-shacl.nt"
# Read-write: the shapes use blank nodes, which are rewritten into a file next to the input.
"${compose[@]}" run --rm --no-deps -T -v "$here/fixtures:/fixtures" worker \
  atlas load-file --env prod --vocab --graph http://data.europa.eu/949/graph/rinf/shacl /fixtures/era-shacl.nt
rm -f "$here/fixtures/era-shacl.nt"

# No config/layers.yaml in CI: no extra map layers, and the tile proxy knows none.
nlayers=$(curl -fsS "$BASE/api/layers" | json "len(d)")
[ "$nlayers" -eq 0 ] || fail "expected no extra map layers, got $nlayers"
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/layers/nope/1/0/0")
[ "$code" = 404 ] || fail "layer proxy for an unknown layer answered $code"

post() { curl -fsS -X POST "$BASE/api/$1" -H 'content-type: application/json' -d "$2"; }

ops=$(curl -fsS "$BASE/api/meta?env=prod" | json "next(c['count'] for c in d['classes'] if c['class']=='operational-point')")
echo "operational points indexed: $ops"; [ "$ops" -ge 20 ] || fail "expected >= 20 operational points"

total=$(post facets '{"env":"prod","classes":[],"filters":{}}' | json "d['total']")
echo "features: $total"; [ "$total" -gt 40 ] || fail "facets total too small"

lux=$(post facets '{"env":"prod","classes":[],"filters":{"country":["http://publications.europa.eu/resource/authority/country/LUX"]}}' | json "d['total']")
[ "$lux" -gt 0 ] || fail "country facet filter returned nothing"

hits=$(post search '{"env":"prod","classes":["operational-point"],"filters":{},"limit":5}' | json "len(d['items'])")
[ "$hits" -eq 5 ] || fail "search returned $hits items"

# Features without geometry are counted separately, so the UI can say they are not on the map.
unloc=$(post search '{"env":"prod","classes":["kilometric-post"],"filters":{},"limit":1}' | json "d['unlocated'], d['total'], d['items'][0]['lon']")
echo "kilometric posts (unlocated, total, lon): $unloc"; [ "$unloc" = "1 1 None" ] || fail "search unlocated count is wrong"
unloc=$(post facets '{"env":"prod","classes":[],"filters":{}}' | json "d['unlocated_by_class']")
[ "$unloc" = "{'kilometric-post': 1}" ] || fail "facets unlocated_by_class is $unloc"

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

# Full export: an async job the worker runs from the mirror (docs/export.md).
post_code() { curl -s -o /tmp/atlas-export.json -w '%{http_code}' -X POST "$BASE/api/exports" -H 'content-type: application/json' -d "$1"; }
nprof=$(curl -fsS "$BASE/api/exports/profiles?env=prod" | json "len(d['profiles'])")
[ "$nprof" -ge 1 ] || fail "no export profile published (shapes/export/full.ttl)"
curl -fsS "$BASE/api/exports/profiles?env=prod" | json "len(d['profiles'][0]['classes']['operational-point']['columns'])" | grep -qv '^0$' \
  || fail "the full profile has no columns for operational points"
opq='{"env":"prod","classes":["operational-point"],"filters":{}}'
code=$(post_code "{\"state\":$opq,\"format\":\"ttl\"}")
[ "$code" = 202 ] || fail "POST /api/exports returned HTTP $code: $(cat /tmp/atlas-export.json)"
eid=$(json "d['id']" < /tmp/atlas-export.json)
code=$(post_code "{\"state\":$opq,\"format\":\"csv\",\"profile\":\"nope\"}")
[ "$code" = 400 ] || fail "an unknown export profile should be refused, got HTTP $code"
code=$(post_code "{\"state\":$opq,\"format\":\"xlsx\",\"include\":[\"opName\",\"uopid\"],\"headers\":\"rinf\"}")
[ "$code" = 202 ] || fail "POST /api/exports (xlsx, custom columns) returned HTTP $code"
xid=$(json "d['id']" < /tmp/atlas-export.json)
[ "$(curl -fsS "$BASE/api/exports/$eid" | json "d['status']")" = queued ] || fail "new export is not queued"
echo "running export jobs"
"${compose[@]}" run --rm --no-deps -T worker atlas export-jobs --once
st=$(curl -fsS "$BASE/api/exports/$eid" | json "d['status'] + ' ' + str(d['total']) + ' ' + str(d['error'])")
echo "ttl export: $st"; [ "${st%% *}" = done ] || fail "export job $eid: $st"
[ "$(curl -fsS "$BASE/api/exports/$eid" | json "d['total']")" -eq "$ops" ] || fail "export counted other than $ops elements"
curl -fsS "$BASE/api/exports/$eid/download" -o /tmp/atlas-export.ttl
grep -q '^@prefix era:' /tmp/atlas-export.ttl || fail "Turtle export has no era: prefix"
ntyped=$(grep -c 'era:OperationalPoint ;' /tmp/atlas-export.ttl || true)
[ "$ntyped" -eq "$ops" ] || fail "Turtle export types $ntyped operational points, expected $ops"
grep -q '^<http://data.europa.eu/949/netReference/' /tmp/atlas-export.ttl || fail "Turtle export did not resolve component nodes"
curl -fsS "$BASE/api/exports/$eid/recipe" | grep -q "SPARQL" || fail "export recipe does not describe the query"
[ "$(curl -fsS "$BASE/api/exports/$xid" | json "d['status']")" = done ] || fail "xlsx export job did not finish"
curl -fsS "$BASE/api/exports/$xid/download" -o /tmp/atlas-export.xlsx
python3 -c "import zipfile,sys; z=zipfile.ZipFile('/tmp/atlas-export.xlsx'); sys.exit(0 if any(f.startswith('xl/worksheets/sheet') for f in z.namelist()) else 1)" \
  || fail "XLSX export job did not produce a workbook"
rm -f /tmp/atlas-export.ttl /tmp/atlas-export.xlsx /tmp/atlas-export.json
code=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$BASE/api/exports/$eid")
[ "$code" = 204 ] || fail "DELETE /api/exports returned HTTP $code"
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/exports/$eid/download")
[ "$code" = 409 ] || fail "a removed export should not download, got HTTP $code"

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
# running tracks mix points and lines, so they are split per geometry family, and the
# GeometryCollection operational point is in the point and the polygon layer, each with its part.
python3 - "$ogc" <<'PY' || fail "OGC collections with mixed geometry types"
import json, sys, urllib.request
ogc = sys.argv[1]
get = lambda u: json.load(urllib.request.urlopen(u))
ids = [c["id"] for c in get(f"{ogc}/collections")["collections"]]
assert "prod.running-track.point" in ids and "prod.running-track.line" in ids and "prod.running-track" not in ids, ids
for cid in ids:
    c = get(f"{ogc}/collections/{cid}")
    types = {f["geometry"]["type"] for f in get(f"{ogc}/collections/{cid}/items?limit=10000")["features"] if f["geometry"]}
    if not types:  # a class without any geometry (the sample's kilometric post) has no type to declare
        assert "geometryType" not in c, (cid, c["geometryType"])
        continue
    assert types == {c["geometryType"]}, (cid, types, c.get("geometryType"))
whole = get(f"{ogc}/collections/prod.running-track/items?limit=1")["numberMatched"]  # still served, unlisted
parts = sum(get(f"{ogc}/collections/prod.running-track.{f}/items?limit=1")["numberMatched"] for f in ("point", "line"))
assert whole == parts, (whole, parts)
gc = "http://data.europa.eu/949/operationalPoint/atlas-test-gc"
assert "prod.operational-point.point" in ids and "prod.operational-point.polygon" in ids, ids
found = {}
for fam in ("point", "polygon"):
    fs = get(f"{ogc}/collections/prod.operational-point.{fam}/items?q={gc}")["features"]
    assert len(fs) == 1, (fam, fs)
    found[fam] = fs[0]
assert found["point"]["id"] == found["polygon"]["id"] and found["point"]["properties"] == found["polygon"]["properties"]
assert found["point"]["geometry"]["type"] == "MultiPoint" and found["polygon"]["geometry"]["type"] == "MultiPolygon"
assert get(f"{ogc}/collections/prod.operational-point/items/{found['point']['id']}")["geometry"]["type"] == "GeometryCollection"
print(f"OGC geometry types: {len(ids)} collections, running tracks {whole} = points + lines, "
      f"GeometryCollection OP in both layers")
PY
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

# The coloured layer's tiles carry the result's ids at every zoom (z3..z14), and the ids
# stay the same when the graph is indexed again (the map and browsers cache tiles).
profile="{\"wheelSetGauge\":[\"$gauge/30\"]}"
before=$(python3 "$here/compat_tiles.py" "$BASE" "$profile") || fail "compatibility tiles do not match the result"
gunzip -c "$here/fixtures/lu-sample.nt.gz" > "$here/fixtures/lu-sample.nt"
"${compose[@]}" run --rm --no-deps -T -v "$here/fixtures:/fixtures:ro" worker \
  atlas load-file --env prod --graph "$GRAPH" /fixtures/lu-sample.nt >/dev/null
rm -f "$here/fixtures/lu-sample.nt"
after=$(python3 "$here/compat_tiles.py" "$BASE" "$profile") || fail "compatibility tiles do not match the result after reloading"
[ "$before" = "$after" ] || fail "feature ids changed when the graph was loaded again"
# No contact line: red with the reason for an electric vehicle, compatible for a bi-mode one
# (a small extra graph: a section with a non-electrified track and a track without contact line).
"${compose[@]}" run --rm --no-deps -T -v "$here/fixtures:/fixtures:ro" worker \
  atlas load-file --env prod --graph "http://data.europa.eu/949/graph/test-no-contact-line" /fixtures/no-contact-line.ntriples >/dev/null
python3 "$here/compat_contact_line.py" "$BASE" || fail "non-electrified tracks are not handled"

ds=$(curl -fsS "$BASE/api/datasets?env=prod" | json "sum(1 for x in d if x['status']=='loaded')")
[ "$ds" -ge 1 ] || fail "dataset not listed as loaded"

# Data quality: validation results against ERA's SHACL shapes.
G=$(python3 -c "import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=''))" "$GRAPH")
dq() { curl -fsS "$BASE/api/datasets?env=prod" | json "next(x['quality'] for x in d if x['graph_iri']=='$GRAPH')$1"; }
echo "dataset quality: $(dq '')"
[ "$(dq "['status']")" = ok ] || fail "dataset quality status is not ok"
issues=$(dq "['violations'] + sum(x['quality']['warnings'] + x['quality']['infos'] for x in d if x['graph_iri']=='$GRAPH')")
[ "$issues" -gt 0 ] || fail "no SHACL results on the sample"
[ "$(curl -fsS "$BASE/api/meta?env=prod" | json "d['quality']['enabled'] and d['quality']['validated']")" -ge 1 ] \
  || fail "meta.quality does not report the validated dataset"
rules=$(curl -fsS "$BASE/api/quality/summary?env=prod&graph=$G&limit=500" | json "sum(r['count'] for r in d)")
echo "results per rule add up to: $rules"; [ "$rules" -eq "$issues" ] || fail "quality summary counts $rules results, dataset $issues"
curl -fsS "$BASE/api/quality/report?env=prod&graph=$G&format=ttl" -o /tmp/atlas-report.ttl
grep -q "a sh:ValidationReport" /tmp/atlas-report.ttl || fail "report is not a sh:ValidationReport"
results=$(grep -c "sh:result \[" /tmp/atlas-report.ttl || true)
echo "report results: $results"; [ "$results" -gt 0 ] || fail "report has no sh:result"
rm -f /tmp/atlas-report.ttl
curl -fsS "$BASE/api/quality/shapes?env=prod" -o /tmp/atlas-shapes.ttl
grep -q "sh:NodeShape" /tmp/atlas-shapes.ttl || fail "shapes download has no node shapes"
rm -f /tmp/atlas-shapes.ttl
if [ "$(dq "['html']")" = True ]; then
  curl -fsS -D /tmp/atlas-report.hdr -o /tmp/atlas-report.html "$BASE/api/quality/report?env=prod&graph=$G&format=html"
  grep -qi '^content-security-policy: sandbox' /tmp/atlas-report.hdr || fail "HTML report not served sandboxed"
  grep -qi '<html' /tmp/atlas-report.html || fail "HTML report is not a page"
  rm -f /tmp/atlas-report.hdr /tmp/atlas-report.html
  echo "shacl-report HTML viewer: present"
fi
flagged=$(post facets '{"env":"prod","classes":[],"filters":{},"facets":["quality"]}' \
  | json "sum(v['count'] for v in d['facets']['quality']['values'])")
echo "features with a quality value: $flagged"; [ "$flagged" -gt 0 ] || fail "quality facet has no values"
bad=$(post search "$(python3 -c "import json; sh='http://www.w3.org/ns/shacl#'; print(json.dumps({'env':'prod','classes':[],'filters':{'quality':[sh+'Violation',sh+'Warning']},'limit':1}))")")
echo "$bad" | json "d['items'][0]['qa']" | grep -qE '^[23]$' || fail "search item of the quality filter lacks qa >= 2"
fid=$(echo "$bad" | json "d['items'][0]['id']")
nres=$(curl -fsS "$BASE/api/feature?env=prod&id=$fid" | json "len(d['quality']['results'])")
echo "feature $fid quality results: $nres"; [ "$nres" -gt 0 ] || fail "feature detail has no quality results"

# Shapes pinned to a GitLab tag: the cache is pre-filled with the fixture, so no network is needed.
echo "validating against a pinned shapes tag (cached)"
cache="$here/fixtures/shapes-cache/era-europa-eu_public_interoperable-data-programme_era-ontology_era-ontology/v3.3.4"
mkdir -p "$cache"
gunzip -c "$here/fixtures/era-shacl.nt.gz" > "$cache/shapes.ttl"   # N-Triples is Turtle
echo '{"tag": "v3.3.4", "file": "era-shacl/ERA-RINF-shapes.ttl"}' > "$cache/meta.json"
"${compose[@]}" run --rm --no-deps -T -v "$here/fixtures:/fixtures:ro" \
  -e ATLAS_QUALITY_SHAPES_TAG=prod=v3.3.4 -e ATLAS_SHAPES_CACHE=/fixtures/shapes-cache worker \
  atlas validate --env prod --now
rm -rf "$here/fixtures/shapes-cache"
[ "$(dq "['shapes']['tag']")" = v3.3.4 ] || fail "dataset not validated with the pinned tag"
[ "$(curl -fsS "$BASE/api/meta?env=prod" | json "d['quality']['shapes']['kind']")" = tag ] || fail "meta does not report the tag source"
curl -fsS "$BASE/api/quality/report?env=prod&graph=$G&format=ttl" -o /tmp/atlas-report.ttl
grep -q 'atlas:shapesTag "v3.3.4"' /tmp/atlas-report.ttl || fail "report does not name the shapes tag"
rm -f /tmp/atlas-report.ttl
curl -fsS -D /tmp/atlas-shapes.hdr "$BASE/api/quality/shapes?env=prod" -o /tmp/atlas-shapes.ttl
grep -q "^# SHACL shapes used by RINF Atlas for data quality: era-ontology v3.3.4" /tmp/atlas-shapes.ttl \
  || fail "shapes download does not name its source"
grep -qi 'filename="era-shacl-shapes-prod-v3.3.4.ttl"' /tmp/atlas-shapes.hdr || fail "shapes download not named after the tag"
rm -f /tmp/atlas-shapes.ttl /tmp/atlas-shapes.hdr

# Above the size limit: "not validated: too large", never "no issues".
echo "validating with a size limit below the sample"
"${compose[@]}" run --rm --no-deps -T -e ATLAS_QUALITY_MAX_TRIPLES=1000 worker atlas validate --env prod --now
echo "dataset quality: $(dq '')"
[ "$(dq "['reason']")" = too_large ] || fail "dataset above the limit not marked too_large"
dq "['note']" | grep -q "too large ([0-9,]* triples > limit 1,000)" || fail "too-large note lacks the sizes"
[ "$(dq "['violations']")" = None ] || fail "a too-large dataset still has counts"
[ "$(post facets '{"env":"prod","classes":[],"filters":{},"facets":["quality"],"graphs":["'"$GRAPH"'"]}' | json "len(d['facets']['quality']['values'])")" = 0 ] \
  || fail "features of a too-large dataset still have quality values"
[ "$(curl -fsS "$BASE/api/meta?env=prod" | json "d['quality']['too_large']")" = 1 ] || fail "meta does not count the too-large dataset"
"${compose[@]}" run --rm --no-deps -T worker atlas validate --env prod --now
[ "$(dq "['status']")" = ok ] || fail "dataset not validated again under the default limit"

curl -fsS "$BASE/" | grep -q "<title>RINF Atlas</title>" || fail "web UI not served"
# MapLibre's module worker must be served as JavaScript or the map stays blank.
worker=$(curl -fsS "$BASE/" | grep -o 'assets/index-[^"]*\.js' | head -1)
wpath=$(curl -fsS "$BASE/$worker" | grep -o 'assets/maplibre-gl-worker-[A-Za-z0-9_-]*\.mjs' | head -1)
[ -n "$wpath" ] || fail "MapLibre worker asset not referenced"
curl -fsSI "$BASE/$wpath" | grep -qi '^content-type: application/javascript' || fail "worker not served as JavaScript"
echo "smoke test passed"
