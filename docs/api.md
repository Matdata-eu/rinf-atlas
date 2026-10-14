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
| GET  | `/api/meta?env=` | | `{classes:[{class,class_iri,label,color,geometry,min_zoom,count}], facets:[{key,class,label,kind,datatype,unit,position,color_by}], datasets:{total,loaded,failed,pending,last_ingest,last_check}}` (`class` `*` = facet applies to every class; `text` facets are search-only and not listed) |
| POST | `/api/facets` | query state | `{total, by_class:{class:count}, facets:{key: {kind:"terms"\|"boolean", values:[{value,label,count}], more} \| {kind:"range", min, max, count}}}` |
| POST | `/api/search` | query state + `limit` (≤500), `offset`, optional `color` (facet key) | `{total, items:[{id,iri,class,label,lon,lat}]}`; with `color` every item also has `v`, its display value for that facet (see *Map colouring*) |
| POST | `/api/theme` | query state + `key` (facet key, plain or `class/key`) | Legend for colouring the map by `key`. Common: `{key, kind, label, unit, classes, rollup, total, nodata}`. Terms/boolean: `values:[{value,label,count,slot}], more, other, mixed`. Range: `breaks:[number], bins:[{min,max,count}], min, max, discrete`. 400 for a text or unknown facet |
| POST | `/api/extent` | query state | `{bbox:[w,s,e,n] \| null}` |
| GET  | `/api/tiles/{z}/{x}/{y}.pbf?state=&color=` | `state` = base64url(JSON query state; bbox ignored); optional `color` = facet key | Mapbox Vector Tile, layer `features`; with `color` each feature that has a value carries property `v` at every zoom |
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

