# RINF Atlas HTTP API

All endpoints are served by the `api` service under `/api` (nginx in `web`
proxies `/api/` to it). `env` is an environment key from
`config/environments.yaml` (`prod`, `uat`). Interactive docs: `/api/docs`.

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
| GET  | `/api/meta?env=` | | `{classes:[{class,class_iri,label,color,geometry,min_zoom,count}], facets:[{key,class,label,kind,datatype,unit,position}], datasets:{total,loaded,failed,pending,last_ingest,last_check}}` (`class` `*` = facet applies to every class; `text` facets are search-only and not listed) |
| POST | `/api/facets` | query state | `{total, by_class:{class:count}, facets:{key: {kind:"terms"\|"boolean", values:[{value,label,count}], more} \| {kind:"range", min, max, count}}}` |
| POST | `/api/search` | query state + `limit` (≤500), `offset` | `{total, items:[{id,iri,class,label,lon,lat}]}` |
| POST | `/api/extent` | query state | `{bbox:[w,s,e,n] \| null}` |
| GET  | `/api/tiles/{z}/{x}/{y}.pbf?state=` | `state` = base64url(JSON query state; bbox ignored) | Mapbox Vector Tile, layer `features` |
| GET  | `/api/feature?env=&iri=` or `?env=&id=` | | `{iri, label, classes:[...], class_iri, graph_iri, provider, dataset, geometry_source, facets:{key:[{value,label}]}, facet_labels:{key:label}, geometry: GeoJSON}` |
| GET  | `/api/describe?env=&iri=&direction=out\|in` | `limit` (default 1000) | Live from ERA. `{iri, direction, triples:[{s,p,o:{type:"uri"\|"literal"\|"bnode",value,datatype?,lang?}}], labels:{iri:label}, truncated}` |
| | | | Query states also accept `graphs: [graph IRI]` to keep only features from those datasets. A filter or `facets` key written `class/key` (e.g. `operational-point/opType`) applies to that class only; a plain key applies to every class that has the facet. The UI uses plain keys for the shared facets (country, IM) and `class/key` for the facets behind each class's filter icon. |
| GET  | `/api/datasets?env=` | | `[{graph_iri,title,provider,country,modified,status,triples,ingested_at,error,dataset_id,file_name,features}]`: every provider graph the worker knows; `status` is pending, loading, loaded, failed or removed. Shown in the UI's Datasets dialog |
| GET  | `/api/export?state=&format=csv\|xlsx\|ttl&values=label\|iri` | `state` = base64url(JSON query state, as for tiles; bbox is honoured) | Every matching feature as a download. Columns: IRI, label, class, dataset, provider, longitude/latitude (a point on the feature), WKT geometry, then one column per facet of the exported classes (shared facets first). Multiple values are joined with `; `. `values=label` (default) writes labels for IRI values, `iri` keeps the IRIs. **CSV**: one UTF-8 table (with BOM, for Excel), streamed. **XLSX**: one sheet per class plus an `Export` sheet describing the query; cells over Excel's 32 767 character limit (long WKT lines) are left empty. **TTL**: the outgoing triples (`?s ?p ?o`, like `/api/describe?direction=out`) of every distinct matching IRI as Turtle, fetched **live from ERA's SPARQL endpoint** in batches of 100 and streamed; capped at `ATLAS_TTL_EXPORT_MAX` entities (default 20000, `413` above it, also reported as `limits.ttl_export` in `/api/meta`). A batch that fails ends the file with a `# ERROR:` comment |
| GET  | `/api/health` | | `{ok:true}` |

## Notes

- `by_class` counts ignore the `classes` selection (so the class list can show
  what each class would add); `total` counts the selected classes only.
- A facet's own selection does not narrow its own value counts (standard
  multi-select facets). Terms facets return the 200 most frequent values;
  `more: true` means there are more.
- Tiles: feature id (`fid`) is set as the MVT feature id. From zoom 8 up the
  properties are `id, iri, class, label`. **Below zoom 8 only `id, class` are
  included** and features that collapse onto the same pixels are sent once,
  which keeps a Europe-wide tile small; resolve a click with
  `/api/feature?env=&id=`. Classes are left out of tiles below their
  `min_zoom` (from the shapes, see `/api/meta`). Lines are simplified below
  zoom 10. Tiles are sent with `Cache-Control: public, max-age=300`.
- Describe responses are cached in the API for 10 minutes.

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
types breaks there. When a class (in an environment) has features of more
than one geometry family (point = Point/MultiPoint, line =
LineString/MultiLineString, polygon = Polygon/MultiPolygon; anything else is
`other`), it is listed as one collection per family:
`{env}.{class}.point`, `.line`, `.polygon` (e.g. `prod.operational-point.point`,
titled "Operational Point (points)"), each with its own extent and only the
features of that family. Within one collection, single and multi geometries
of a family are both sent as the multi type (`ST_Multi`), so every feature has
the type in `geometryType`. Classes with a single family keep their
`{env}.{class}` id. For a mixed class, `{env}.{class}` still answers when asked
for directly (all its features, mixed types, also those without a geometry)
but is not listed: listing it would hand GIS clients a layer they mistype.
Features without a geometry appear only in a class's own `{env}.{class}`
collection. The families are read from the database, cached until the data
changes.

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
