# RINF Atlas HTTP API

All endpoints are served by the `api` service under `/api` (nginx in `web`
proxies `/api/` to it). `env` is an environment key from
`config/environments.yaml` (`prod`, `uat`). Interactive docs: `/api/docs`.
The web app's own links (the `#...` state of a view) are described in
[urls.md](urls.md).

Everything is answered from PostGIS. Only `/api/describe` goes live to ERA's
SPARQL endpoint.

A **query state** is shared by facets, search, extent and tiles:

```json
{
  "env": "prod",
  "classes": ["operational-point", "tunnel"],   // empty = all classes
  "filters": {                                  // per facet key
    "country": ["http://publications.europa.eu/resource/authority/country/BEL"],
    "lengthOfTunnel": {"min": 100, "max": 5000},
    "etcs.etcsLevelType": ["http://data.europa.eu/949/concepts/etcs-levels/30"],
    "localRulesOrRestrictions": [true]
  },
  "q": "brussel",                               // optional full text (prefix match), or a full IRI
  "bbox": [3.0, 50.5, 5.0, 51.5],               // optional [w,s,e,n], EPSG:4326
  "facets": ["country", "opType"]               // optional, /api/facets only: which facets to compute
}
```

Values inside one facet are OR-ed, different facets are AND-ed. A filter only
constrains the classes that have that facet: `opType = station` with classes
`[operational-point, tunnel]` returns stations plus all tunnels.

Facet keys are the local names of the shape's `sh:path`, joined with `.` for
sequence paths (`etcs.etcsLevelType` = `era:etcs/era:etcsLevelType`). Terms
values are IRIs or plain strings, boolean values are JSON booleans, range
values are numbers.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET  | `/api/envs` | | `[{key,label,sparql,portal,default}]` |
| GET  | `/api/meta?env=` | | `{classes:[{class,class_iri,label,color,geometry,min_zoom,count}], facets:[{key,class,label,kind,datatype,unit,position,color_by}], datasets:{total,loaded,failed,pending,last_ingest,last_check}, quality:{enabled,validated,violations,warnings,infos,too_large,checked_at,shapes_graph,shapes_loaded_at,shapes,shapes_error,max_triples,shacl_report_url}}` (`class` `*` = facet applies to every class; `text` facets are search-only and, like `link` facets, not listed). `quality` sums the data quality results of the loaded datasets; `enabled` is false when the worker does not validate (`quality.enabled: false`); `shacl_report_url` is the shacl-report instance the UI links to (`ATLAS_SHACL_REPORT_URL`, null = none); `shapes` = `{kind:"graph"\|"tag", graph, label, url, tag, repo, file, version, modified}`, the SHACL shapes used (ERA's mirrored shapes graph, or a pinned tag of ERA's ontology repository), which the environment validates with now; `shapes_error` says why the configured shapes could not be loaded (an unknown tag, GitLab unreachable); `max_triples` is the size limit and `too_large` the number of datasets above it |
| POST | `/api/facets` | query state | `{total, by_class:{class:count}, unlocated_by_class:{class:count}, facets:{key: {kind:"terms"\|"boolean", values:[{value,label,count}], more} \| {kind:"range", min, max, count}}}` (`unlocated_by_class`: per class, how many of `by_class` have no geometry; classes without any are left out) |
| POST | `/api/search` | query state + `limit` (≤500), `offset`, optional `color` (facet key) | `{total, unlocated, items:[{id,iri,class,label,lon,lat,qa}]}` (`unlocated`: how many of `total` have no geometry, listed but never drawn on the map; their `lon`/`lat` are null; `qa`: see Data quality below); with `color` every item also has `v`, its display value for that facet (see *Map colouring*) |
| POST | `/api/theme` | query state + `key` (facet key, plain or `class/key`) | Legend for colouring the map by `key`. Common: `{key, kind, label, unit, classes, rollup, total, nodata}`. Terms/boolean: `values:[{value,label,count,slot}], more, other, mixed`. Range: `breaks:[number], bins:[{min,max,count}], min, max, discrete`. 400 for a text or unknown facet |
| POST | `/api/extent` | query state | `{bbox:[w,s,e,n] \| null}` |
| GET  | `/api/tiles/{z}/{x}/{y}.pbf?state=&color=` | `state` = base64url(JSON query state; bbox ignored); optional `color` = facet key | Mapbox Vector Tile, layer `features`; with `color` each feature that has a value carries property `v` at every zoom |
| GET  | `/api/feature?env=&iri=` or `?env=&id=` | | `{iri, label, classes:[...], class_iri, graph_iri, provider, dataset, geometry_source, facets:{key:[{value,label}]}, facet_labels:{key:label}, facet_kinds:{key:kind}, geometry: GeoJSON, quality}` (`link` facets hold IRIs of other indexed features, e.g. a section's `opStart` / `opEnd`). `quality` = `{status, checked_at, note, reason, shapes, results:[{severity:"violation"\|"warning"\|"info", message, path, value, value_label, shape, component}], truncated, report, html}`: the SHACL results whose focus node is this IRI (at most 200, worst first; `truncated` when there are more), the validation `status` of its dataset (null = not validated yet, `ok`, `partial`, `skipped`, `failed`, with `note`), `reason` (see datasets), the `shapes` used (as in `/api/meta`), and whether the dataset report exists as Turtle (`report`) and HTML (`html`). `path` is a SPARQL property path, `value` an N-Triples term. The `quality` facet is not repeated in `facets` |
| GET  | `/api/lines?env=&q=&limit=` | `limit` ≤ 100 (default 20) | National lines whose id (`nationalLine.lineId` of sections of line) contains `q`, exact and prefix matches first: `[{line, graph_iri, provider, sections, length_km}]`. Line ids are national, so the same id can come back once per dataset |
| POST | `/api/profile` | see [Line profile](#line-profile) | Sections in order along a line, a list of sections or a route, with track parameters per section |
| GET  | `/api/describe?env=&iri=&direction=out\|in` | `limit` (default 1000) | Live from ERA. `{iri, direction, triples:[{s,p,o:{type:"uri"\|"literal"\|"bnode",value,datatype?,lang?}}], labels:{iri:label}, truncated}` |
| | | | Query states also accept `graphs: [graph IRI]` to keep only features from those datasets. A filter or `facets` key written `class/key` (e.g. `operational-point/opType`) applies to that class only; a plain key applies to every class that has the facet. The UI uses plain keys for the shared facets (country, IM) and `class/key` for the facets behind each class's filter icon. |
| GET  | `/api/datasets?env=` | | `[{graph_iri,title,provider,country,modified,status,triples,ingested_at,error,dataset_id,file_name,features,quality}]`: every provider graph the worker knows; `status` is pending, loading, loaded, failed or removed. `quality` = `{status, violations, warnings, infos, focus_nodes, kept, checked_at, seconds, note, reason, limit, shapes, report, html}`: the dataset's validation against ERA's SHACL shapes (`status` null = not validated yet, `ok`, `partial` = stopped at the time budget or a result cap, `skipped`, `failed`; `reason` says why it was skipped: `too_large` (more triples than `limit`, the size limit then in force; it has no counts, no report and no `quality` facet values, so it never shows as "no issues") or `no_shapes`; `shapes` = the shapes it was validated with, as in `/api/meta`; `focus_nodes` = resources with results; `kept` = results stored, which can be fewer than the counts when a cap applied). Shown in the UI's Datasets dialog |
| GET  | `/api/quality/summary?env=&graph=&limit=` | `limit` ≤ 500 (default 50) | `[{shape, component, severity, count, message}]`: the dataset's results grouped by shape and constraint component, worst severity and most frequent first. Counts are exact even when results were capped |
| GET  | `/api/quality/report?env=&graph=&format=ttl\|html&download=` | | The dataset's validation report. `ttl` (default): a standard `sh:ValidationReport` in Turtle, as a download; open it in [shacl-report](https://github.com/Matdata-eu/shacl-report) together with `/api/quality/shapes`. `html`: the self-contained viewer shacl-report generated for it (only when the worker has the `shacl-report` binary and the report has at most `quality.html_max_results` results), served inline under `Content-Security-Policy: sandbox …`; `download=true` makes it an attachment. `404` when there is none |
| GET  | `/api/quality/shapes?env=` | | The SHACL shapes the environment validates with (ERA's shapes graph or the pinned tag's file), as Turtle (a download named after the tag or version). The file starts with comments naming the source URL, `owl:versionInfo` and `dct:modified`. `404` before the worker loaded them |
| GET  | `/api/export?state=&format=csv\|xlsx\|ttl&values=label\|iri` | `state` = base64url(JSON query state, as for tiles; bbox is honoured) | Every matching feature as a download. Columns: IRI, label, class, dataset, provider, longitude/latitude (a point on the feature), WKT geometry, then one column per facet of the exported classes (shared facets first). Multiple values are joined with `; `. `values=label` (default) writes labels for IRI values, `iri` keeps the IRIs. **CSV**: one UTF-8 table (with BOM, for Excel), streamed. **XLSX**: one sheet per class plus an `Export` sheet describing the query; cells over Excel's 32 767 character limit (long WKT lines) are left empty. **TTL**: the outgoing triples (`?s ?p ?o`, like `/api/describe?direction=out`) of every distinct matching IRI as Turtle, fetched **live from ERA's SPARQL endpoint** in batches of 100 and streamed; capped at `ATLAS_TTL_EXPORT_MAX` entities (default 20000, `413` above it, also reported as `limits.ttl_export` in `/api/meta`). A batch that fails ends the file with a `# ERROR:` comment |
| GET  | `/api/compatibility/rules?env=` | | Vehicle compatibility rules (from `shapes/compatibility.ttl`): `{track_classes, section_class, class_labels:{class:label}, rules:[{key,label,comment,path:[IRI],constraint,kind,datatype,unit,facets:[key],applies_to,when_missing,none_value,none_from,missing_reason,section_facets:[key], infra:[{key,label,path,classes,kind,unit}], source, explanation:{vehicle_path,steps:[{topic,text}]}, values:[{value,label,count,none?}] \| range:{min,max,count}}]}`. `kind` is `terms`, `boolean` or `number`; `values` / `range` describe the indexed infrastructure data for the compared facets (for value pickers). `when_missing` (`unknown` or `incompatible`) is what a track without a value gives; a rule with `none_value` (e.g. "not electrified") lists it first in `values` with `none: true` and `count` = the tracks without a value, and a vehicle that has it is compatible with them (also for the rules whose `none_from` names it). For explaining a rule ("How is this checked?"): `infra` lists each compared facet with its label, the RINF property path it is read from (prefixed, e.g. `era:contactLineSystem/era:energySupplySystem`) and the classes indexing it; `source` is the rule as Turtle (`null` until the worker has republished its definitions); `explanation` is generated from the rule's annotations, `vehicle_path` the prefixed vehicle property path and `steps` one plain-language sentence per topic `compare` (the constraint), `missing` (a track without a value), `none` (the vehicle's "none" option), `fallback` (the section of line's value), `rollup` (how tracks make the section's colour) and `empty`, in that order, each present only when it applies. 404 when no rules are configured |
| POST | `/api/compatibility` | `{env, profile:{key: [values] \| number}}` | Status of every section of line for a vehicle profile: `{env, version, checked:[key], ignored:[key], counts:{green,yellow,red}, green:[id], yellow:[id], red:[id]}`. Ids are feature ids (the MVT feature id of the tiles); a feature keeps its id when its graph is indexed again. `version` identifies the indexed data: add it to the tile URL (`&v=`) of the layer you colour, so cached tiles from an older index are not reused. 400 when no filled-in characteristic has a rule or a value has the wrong type |
| POST | `/api/compatibility/explain` | `{env, profile, iri}` (or `id`) of a section of line | Why it has its status: `{iri, id, status, checked, ignored, section:[check], tracks:[{id,iri,label,class,status,results:[check]}]}` with `check = {key, result: "ok"\|"unknown"\|"fail", values:[{value,label}], note?, bypass?}` (the infrastructure values compared; `note` says why a track without a value got its result, e.g. "No contact line", or that it is compatible only because the vehicle has the rule's `none_value` and can do without the system, e.g. "Compatible in “Not electrified” mode"; `bypass` is that none value IRI) |
| GET  | `/api/layers` | | The extra map layers of this instance (`config/layers.yaml`, see [layers.md](layers.md)): `[{id,label,description,type:"raster"\|"wms"\|"vector",tiles:[url],proxied,tile_size,min_zoom,max_zoom,opacity,attribution,position:"below"\|"above",style?}]`. `tiles` are MapLibre tile URL templates; for a `proxied` layer they point at the proxy below, and neither its provider URL nor its keys are listed. `style` (vector only) holds MapLibre style layers without `id` and `source`. `[]` when none are configured |
| GET  | `/api/layers/{id}/{z}/{x}/{y}` | | One tile of a proxied layer, fetched from the provider with the configured keys and headers (a WMS GetMap for the tile's EPSG:3857 box). `204` where the provider has no tile or outside `min_zoom`..`max_zoom`, `404` for an unknown or unproxied layer, `502` when the provider fails. `Cache-Control: public, max-age=cache_seconds` |
| GET  | `/api/health` | | `{ok:true}` |

## Line profile

`POST /api/profile` takes one of three inputs:

```json
{"env": "prod", "line": "50A", "graph": "http://data.europa.eu/949/graph/0088"}   // a national line (graph optional)
{"env": "prod", "sections": ["http://data.europa.eu/949/sectionOfLine/..."]}       // sections in any order
{"env": "prod", "from": "<operational point IRI>", "to": "<IRI>", "via": []}        // shortest chain of sections
```

plus optional `params` (facet keys for the rows; default: maximum speed,
contact line system type, energy supply system, nominal track gauge, ETCS
level, gauging, line category; unknown keys and `text`/`link` facets are
dropped) and `reverse: true`.

Sections are edges between their start and end operational point (`opStart` /
`opEnd` link facets). For a line or a list, each connected group contributes
its longest path; what is left (branches, parallel sections) and separate
groups are chained to the nearest end, and the section after each break gets
`gap_before: true` with `gap_km`, the straight-line distance between the two
operational points. The sequence runs the way most of its sections are
recorded (start to end, normally the line's kilometres); `reverse` flips it.
A route is the shortest path by length over every section of the
environment. Lengths are `lengthOfSectionOfLine`, else the geometry's length
(`length_estimated: true`).

Parameters are read from the section itself and from every track (running
track or track) that is part of it (`era:isPartOf` / `era:hasPart`, kept by
the worker as the track's `atlas:geometryFrom` link). With several tracks, a `range` row gives the
lowest and highest value, a `terms`/`boolean` row lists every value with the
number of tracks having it (`n`); `tracks` has the values per track with its
running direction for the detail.

```json
{
  "env": "prod", "mode": "line",              // line | sections | route
  "line": {"id": "50A", "graph_iri": "...", "provider": "Infrabel",
           "alternatives": [{"graph_iri": "...", "provider": "Infrabel", "sections": 31}]},
  "params": [{"key": "maximumPermittedSpeed", "label": "Maximum permitted speed", "kind": "range", "unit": "KiloM-PER-HR"}],
  "length_km": 117.9, "gaps": 0,
  "sections": [{
    "id": 123, "iri": "...", "label": "Brussel-Zuid – Aalst", "graph_iri": "...",
    "from": "<op IRI>", "to": "<op IRI>", "reversed": false,
    "start_km": 0, "end_km": 12.1, "length_km": 12.1, "length_estimated": false,
    "gap_before": false, "gap_km": null, "geometry": {"type": "LineString", "coordinates": [[4.33, 50.83], [4.04, 50.94]]},
    "cells": {"maximumPermittedSpeed": {"min": 120, "max": 160},
              "etcs.etcsLevelType": {"values": [{"value": "<IRI>", "label": "ETCS level 1", "n": 2}]}},
    "tracks": [{"iri": "...", "label": "...", "direction": "Normal", "values": {"maximumPermittedSpeed": [{"value": 160, "label": "160"}]}}]
  }],
  "points": [{"iri": "...", "id": 7, "label": "Brussel-Zuid", "km": 0, "lon": 4.33, "lat": 50.83}],
  "missing": [],          // requested section IRIs that are not indexed
  "truncated": false      // more than ATLAS_PROFILE_MAX_SECTIONS (3000) sections
}
```

`points` has the operational point at the start of the sequence, at both
sides of each gap, and at the end of every section. A route between
operational points that no chain of sections connects answers 404.

## Notes

- `by_class` counts ignore the `classes` selection (so the class list can show
  what each class would add); `total` counts the selected classes only.
- A facet's own selection does not narrow its own value counts (standard
  multi-select facets). Terms facets return the 200 most frequent values;
  `more: true` means there are more.
- Tiles: feature id (`fid`) is set as the MVT feature id. From zoom 8 up the
  properties are `id, iri, class, label, qa`. **Below zoom 8 only `id, class, qa` are
  included** and features that collapse onto the same pixels are sent once
  (across classes, the one first in the class list, which the map draws on
  top), which keeps a Europe-wide tile small; resolve a click with
  `/api/feature?env=&id=`. Classes are left out of tiles below their
  `min_zoom` (from the shapes, see `/api/meta`), except those listed in the
  state's `all_zoom` (the UI's "show at every zoom"). Lines are simplified below
  zoom 10. Tiles are sent with `Cache-Control: public, max-age=300`.
- Describe responses are cached in the API for 10 minutes.
- Vehicle compatibility: a profile maps characteristic keys (`/api/compatibility/rules`)
  to IRIs or booleans (terms, boolean) or one number; missing, `null` or empty
  values are not checked, unknown keys are reported in `ignored`. Each rule
  plugs the vehicle value(s) into a SHACL constraint applied to the indexed
  values of its facets: `hasValue` (the infrastructure has one of the
  vehicle's values), `in` (every infrastructure value is one of the
  vehicle's), `minInclusive` / `maxInclusive` / `minExclusive` /
  `maxExclusive` (every infrastructure value compared with the vehicle's
  number). Per track: `fail` if a check fails, else `unknown` if a facet has
  no value, else `ok`. A section of line is `green` when at least one of its
  tracks (found through `era:isPartOf` / `era:hasPart`) is ok, `yellow` when
  none fails but at least one is unknown (also when no track is indexed),
  `red` otherwise; checks on the section itself (`applies_to: "section"`)
  can only make it worse. Results are cached until the data changes.

## GIS access (OGC API – Features, vector tiles)

For desktop GIS (QGIS, ArcGIS Pro, GDAL/OGR, OWSLib); see "Use in QGIS" in the
README. Read-only, from PostGIS, CORS open. Errors are `{code, description}`:
400 for a bad or unknown query parameter, 404 for an unknown collection or
feature.

**OGC API – Features** (Part 1 Core + GeoJSON, Part 2 CRS by reference):

| Path | Returns |
|---|---|
| `/api/ogc` | landing page (links to conformance, collections and `/api/openapi.json`) |
| `/api/ogc/conformance` | `core` and `geojson` (Part 1), `crs` (Part 2) |
| `/api/ogc/collections` | one collection per environment and class with features: id `{env}.{class}` (e.g. `prod.operational-point`), title = the shape's class label (plus the environment when several are served), spatial extent, `geometryType` (a non-standard hint, e.g. `Point`, `MultiPolygon`), the facet parameters in `description`. A class whose features mix geometry types is listed once per type instead, see below |
| `/api/ogc/collections/{id}` | one collection |
| `/api/ogc/collections/{id}/items` | GeoJSON `FeatureCollection` with `numberMatched`, `numberReturned`, `timeStamp` and `self` / `next` / `prev` links |
| `/api/ogc/collections/{id}/items/{fid}` | one GeoJSON feature |
| `/api/ogc/filtered/{state}/...` | the same tree for a query state (base64url JSON, as for tiles): only that environment, only its classes (all when empty), every request narrowed by its filters, search and datasets. The state's `bbox` is ignored. The UI's **Open in GIS** button copies this URL |

Items parameters: `limit` (default 100; more than 10 000 is capped),
`offset`, `bbox` (`minx,miny,maxx,maxy`; 6 values accepted), `bbox-crs`,
`crs` (output CRS: CRS84, the default; `http://www.opengis.net/def/crs/EPSG/0/4326`
with lat/lon axis order; `.../EPSG/0/3857`; the response carries
`Content-Crs`), `q` (full text as in the UI, or a full IRI), `dataset`
(provider graph IRIs, comma separated), `values=label|iri` (IRI-valued
properties as labels, the default, or as IRIs), `datetime` and `f=json`
(accepted, ignored). Every facet key of the class is a parameter too, applied
like a UI filter: terms as comma separated values
(`?opType=http://data.europa.eu/949/concepts/op-types/10`), booleans
`true`/`false`, ranges `min..max` with either side optional
(`?lengthOfTunnel=1000..`). Any other parameter is a 400.

**One geometry type per collection.** QGIS and GDAL give a layer a single
geometry type, taken from the first features they read, so a class mixing
types breaks there. Geometries are sorted into three families: point
(Point/MultiPoint), line (LineString/MultiLineString) and polygon
(Polygon/MultiPolygon). A GeometryCollection (the worker collects a feature's
several WKT literals into one, e.g. an operational point with a point and a
polygon; nested collections included) is split into its parts with
`ST_CollectionExtract`: the feature belongs to every family it has a part of,
and in each it carries only that part. When a class (in an environment) has
more than one family, it is listed as one collection per family:
`{env}.{class}.point`, `.line`, `.polygon` (e.g.
`prod.operational-point.point`, titled "Operational Point (points)"). A feature
with a point and a polygon is then in both, with the same `id`, `iri` and
properties. Extents, `numberMatched`, paging, `bbox` (tested against the part)
and `items/{fid}` (404 when the feature has no part of that family) all use the
family's part. Within a collection, singles, multis and collection parts are
all sent as the multi type when they differ (`ST_Multi`), so every feature has
the type in `geometryType`. Classes with a single family keep their
`{env}.{class}` id (with their features without a geometry). For a mixed class,
`{env}.{class}` still answers when asked for directly (all its features as
stored, GeometryCollections included, and those without a geometry) but is not
listed: listing it would hand GIS clients a layer they cannot type. Geometry
types outside the three families (curves, TINs; empty geometries) are only in
that combined collection; the worker does not produce them. The families are
read from the database, cached until the data changes.

Features: `id` is the Atlas feature id (it can change on a re-index; `iri` is
stable). Properties are flat and the same on every feature of a collection:
`iri, label, class, dataset, provider`, then one per facet of the class
(shared facets first), `null` when absent. Several values are joined with
`; `; a single number stays a number, a single boolean a boolean. Features
without a geometry have `geometry: null`. Items are ordered by id.

Limits (environment variables of the `api` service): `ATLAS_OGC_DEFAULT_LIMIT`
(100), `ATLAS_OGC_MAX_LIMIT` (10000), `ATLAS_OGC_MAX_OFFSET` (5000000) and a
statement timeout per query, `ATLAS_OGC_TIMEOUT_MS` (30000). Links are
absolute and built from `ATLAS_PUBLIC_URL` when set (e.g.
`https://rinf-atlas.matdata.eu`), otherwise from `X-Forwarded-Proto` and
`X-Forwarded-Host` (nginx forwards those of a proxy in front of it, or its own
scheme and `Host`).

**Vector tiles for GIS:**

| Method | Path | Returns |
|---|---|---|
| GET | `/api/tiles.json?state=` or `?env=` | TileJSON 3.0.0 for `/api/tiles` with that state (or a whole environment; the default one when neither is given): `tiles`, `bounds`, `maxzoom` (`ATLAS_TILE_MAXZOOM`, 14), layer `features` with fields `id, iri, class, label` |
| GET | `/api/style.json?state=` or `?env=` | MapLibre style (v8) drawing those tiles in the class colours: circles for point classes, lines for line classes, both for `any`, each from the class `min_zoom` |
## Map colouring

`/api/theme`, `color` on tiles and `color` on search colour the map by one
facet. The API computes one **display value** per feature, once per
environment, facet key and data version (cached in PostGIS; the first request
after a data update takes a moment longer):

- A feature of a class that owns the facet gets its own value. Terms and
  boolean facets: the value, or `__mixed__` when the feature has several
  different values. Range facets: the highest number.
- **Roll-up**: a line class at the top of the "part of" chain that does not
  own the facet (a section of line, when colouring by a running-track facet)
  is coloured from its line parts (the `atlas:geometryFrom` links): the value
  all its parts agree on, `__mixed__` when they differ, the highest number for
  a range facet. Parts without a value are ignored. Such classes are listed in
  `rollup`.
- `classes` are the classes coloured by their own values (empty for a facet
  shared by every class); features of `classes` and `rollup` without a value
  count as `nodata`. Other classes are not coloured.

Colours are assigned by the client from data that does not change with the
query state, so filtering never repaints a value:

- Terms/boolean: `slot` 0-7 is the value's place among the 8 most frequent
  values of the whole environment (booleans: yes = 0, no = 1); values without
  a slot share one "other values" colour (`other` counts them). `values` lists
  the slotted values first, then the rest by count (at most 200, `more`).
- Range: `breaks` are class boundaries for the whole environment (rounded
  quantiles; with five or fewer distinct values, each value is its own class
  and `discrete` is true). A value `v` falls in class `i` = the number of
  breaks `<= v`; `bins[i]` counts the features of the query state in it.

The counts follow the query state (classes, filters, datasets, search text,
bbox) like `/api/facets`.

## Data quality

The worker validates each provider graph against the SHACL shapes ERA
publishes (`quality.shapes_graph`, by default
`http://data.europa.eu/949/graph/rinf/shacl`) and stores the results in
PostGIS; see [DESIGN.md](DESIGN.md#data-quality). The API reads them from
there like everything else:

- A builtin shared facet `quality` (class `*`, kind `terms`, label "Data
  quality (ERA SHACL)") holds the severities found on a feature:
  `http://www.w3.org/ns/shacl#Violation`, `…#Warning`, `…#Info`, or
  `https://rinf-atlas.matdata.eu/ns#NoIssues` when the feature was checked
  and had none. Features of datasets that were not validated (yet), or whose
  check was skipped or failed, have no value; after a `partial` check,
  `NoIssues` means none were found before the check stopped. It filters, counts and exports like any other facet, e.g.
  `"filters": {"quality": ["http://www.w3.org/ns/shacl#Violation"]}`.
- Search items and tiles (at every zoom) carry `qa`, the worst severity on
  the feature: `3` violation, `2` warning, `1` info, `0` none, `null` not
  validated. The map's "Data issues" layer colours features by it.
- Reports name the shapes they used as triples on the `sh:ValidationReport`
  node: `atlas:shapesSource` (label), `atlas:shapesKind`, `dct:source` (the
  file at the tag, or the shapes graph), `atlas:shapesTag`,
  `atlas:shapesRepository`, `atlas:shapesFile`, `atlas:shapesVersion`
  (`owl:versionInfo`) and `atlas:shapesModified`, with
  `atlas: <https://rinf-atlas.matdata.eu/ns#>`.
- Only results whose focus node is an indexed feature can be shown on the
  map; results on other resources (a track, a contact line system) are in the
  dataset counts, the summary and the report.
