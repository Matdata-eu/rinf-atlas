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
| GET  | `/api/meta?env=` | | `{classes:[{class,class_iri,label,color,geometry,min_zoom,count}], facets:[{key,class,label,kind,datatype,unit,position}], datasets:{total,loaded,failed,pending,last_ingest,last_check}, quality:{enabled,validated,violations,warnings,infos,checked_at,shapes_graph,shapes_loaded_at,shacl_report_url}}` (`class` `*` = facet applies to every class; `text` facets are search-only and not listed). `quality` sums the data quality results of the loaded datasets; `enabled` is false when the worker does not validate (`quality.enabled: false`); `shacl_report_url` is the shacl-report instance the UI links to (`ATLAS_SHACL_REPORT_URL`, null = none) |
| POST | `/api/facets` | query state | `{total, by_class:{class:count}, facets:{key: {kind:"terms"\|"boolean", values:[{value,label,count}], more} \| {kind:"range", min, max, count}}}` |
| POST | `/api/search` | query state + `limit` (≤500), `offset` | `{total, items:[{id,iri,class,label,lon,lat,qa}]}` (`qa`: see Data quality below) |
| POST | `/api/extent` | query state | `{bbox:[w,s,e,n] \| null}` |
| GET  | `/api/tiles/{z}/{x}/{y}.pbf?state=` | `state` = base64url(JSON query state; bbox ignored) | Mapbox Vector Tile, layer `features` |
| GET  | `/api/feature?env=&iri=` or `?env=&id=` | | `{iri, label, classes:[...], class_iri, graph_iri, provider, dataset, geometry_source, facets:{key:[{value,label}]}, facet_labels:{key:label}, geometry: GeoJSON, quality}`. `quality` = `{status, checked_at, note, results:[{severity:"violation"\|"warning"\|"info", message, path, value, value_label, shape, component}], truncated, report, html}`: the SHACL results whose focus node is this IRI (at most 200, worst first; `truncated` when there are more), the validation `status` of its dataset (null = not validated yet, `ok`, `partial`, `skipped`, `failed`, with `note`), and whether the dataset report exists as Turtle (`report`) and HTML (`html`). `path` is a SPARQL property path, `value` an N-Triples term. The `quality` facet is not repeated in `facets` |
| GET  | `/api/describe?env=&iri=&direction=out\|in` | `limit` (default 1000) | Live from ERA. `{iri, direction, triples:[{s,p,o:{type:"uri"\|"literal"\|"bnode",value,datatype?,lang?}}], labels:{iri:label}, truncated}` |
| | | | Query states also accept `graphs: [graph IRI]` to keep only features from those datasets. A filter or `facets` key written `class/key` (e.g. `operational-point/opType`) applies to that class only; a plain key applies to every class that has the facet. The UI uses plain keys for the shared facets (country, IM) and `class/key` for the facets behind each class's filter icon. |
| GET  | `/api/datasets?env=` | | `[{graph_iri,title,provider,country,modified,status,triples,ingested_at,error,dataset_id,file_name,features,quality}]`: every provider graph the worker knows; `status` is pending, loading, loaded, failed or removed. `quality` = `{status, violations, warnings, infos, focus_nodes, kept, checked_at, seconds, note, report, html}`: the dataset's validation against ERA's SHACL shapes (`status` null = not validated yet, `ok`, `partial` = stopped at the time budget or a result cap, `skipped` = too large or no shapes, `failed`; `focus_nodes` = resources with results; `kept` = results stored, which can be fewer than the counts when a cap applied). Shown in the UI's Datasets dialog |
| GET  | `/api/quality/summary?env=&graph=&limit=` | `limit` ≤ 500 (default 50) | `[{shape, component, severity, count, message}]`: the dataset's results grouped by shape and constraint component, worst severity and most frequent first. Counts are exact even when results were capped |
| GET  | `/api/quality/report?env=&graph=&format=ttl\|html&download=` | | The dataset's validation report. `ttl` (default): a standard `sh:ValidationReport` in Turtle, as a download; open it in [shacl-report](https://github.com/Matdata-eu/shacl-report) together with `/api/quality/shapes`. `html`: the self-contained viewer shacl-report generated for it (only when the worker has the `shacl-report` binary and the report has at most `quality.html_max_results` results), served inline under `Content-Security-Policy: sandbox …`; `download=true` makes it an attachment. `404` when there is none |
| GET  | `/api/quality/shapes?env=` | | ERA's SHACL shapes graph the datasets were validated against, as Turtle (a download). `404` before the worker mirrored it |
| GET  | `/api/export?state=&format=csv\|xlsx\|ttl&values=label\|iri` | `state` = base64url(JSON query state, as for tiles; bbox is honoured) | Every matching feature as a download. Columns: IRI, label, class, dataset, provider, longitude/latitude (a point on the feature), WKT geometry, then one column per facet of the exported classes (shared facets first). Multiple values are joined with `; `. `values=label` (default) writes labels for IRI values, `iri` keeps the IRIs. **CSV**: one UTF-8 table (with BOM, for Excel), streamed. **XLSX**: one sheet per class plus an `Export` sheet describing the query; cells over Excel's 32 767 character limit (long WKT lines) are left empty. **TTL**: the outgoing triples (`?s ?p ?o`, like `/api/describe?direction=out`) of every distinct matching IRI as Turtle, fetched **live from ERA's SPARQL endpoint** in batches of 100 and streamed; capped at `ATLAS_TTL_EXPORT_MAX` entities (default 20000, `413` above it, also reported as `limits.ttl_export` in `/api/meta`). A batch that fails ends the file with a `# ERROR:` comment |
| GET  | `/api/health` | | `{ok:true}` |

## Notes

- `by_class` counts ignore the `classes` selection (so the class list can show
  what each class would add); `total` counts the selected classes only.
- A facet's own selection does not narrow its own value counts (standard
  multi-select facets). Terms facets return the 200 most frequent values;
  `more: true` means there are more.
- Tiles: feature id (`fid`) is set as the MVT feature id. From zoom 8 up the
  properties are `id, iri, class, label, qa`. **Below zoom 8 only `id, class, qa` are
  included** and features that collapse onto the same pixels are sent once,
  which keeps a Europe-wide tile small; resolve a click with
  `/api/feature?env=&id=`. Classes are left out of tiles below their
  `min_zoom` (from the shapes, see `/api/meta`). Lines are simplified below
  zoom 10. Tiles are sent with `Cache-Control: public, max-age=300`.
- Describe responses are cached in the API for 10 minutes.

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
- Only results whose focus node is an indexed feature can be shown on the
  map; results on other resources (a track, a contact line system) are in the
  dataset counts, the summary and the report.
