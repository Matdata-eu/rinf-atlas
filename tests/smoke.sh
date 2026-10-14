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

# ERA's SHACL shapes arrive like a vocabulary graph; loading them validates the sample.
echo "loading ERA SHACL shapes (validates the sample)"
gunzip -c "$here/fixtures/era-shacl.nt.gz" > "$here/fixtures/era-shacl.nt"
# Read-write: the shapes use blank nodes, which are rewritten into a file next to the input.
"${compose[@]}" run --rm --no-deps -T -v "$here/fixtures:/fixtures" worker \
  atlas load-file --env prod --vocab --graph http://data.europa.eu/949/graph/rinf/shacl /fixtures/era-shacl.nt
rm -f "$here/fixtures/era-shacl.nt"

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

curl -fsS "$BASE/" | grep -q "<title>RINF Atlas</title>" || fail "web UI not served"
# MapLibre's module worker must be served as JavaScript or the map stays blank.
worker=$(curl -fsS "$BASE/" | grep -o 'assets/index-[^"]*\.js' | head -1)
wpath=$(curl -fsS "$BASE/$worker" | grep -o 'assets/maplibre-gl-worker-[A-Za-z0-9_-]*\.mjs' | head -1)
[ -n "$wpath" ] || fail "MapLibre worker asset not referenced"
curl -fsSI "$BASE/$wpath" | grep -qi '^content-type: application/javascript' || fail "worker not served as JavaScript"
echo "smoke test passed"
