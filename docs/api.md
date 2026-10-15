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
| GET  | `/api/compatibility/rules?env=` | | Vehicle compatibility rules (from `shapes/compatibility.ttl`): `{track_classes, section_class, class_labels:{class:label}, rules:[{key,label,comment,path:[IRI],constraint,kind,datatype,unit,facets:[key],applies_to,when_missing,none_value,none_from,missing_reason,section_facets:[key], infra:[{key,label,path,classes,kind,unit}], source, explanation:{vehicle_path,steps:[{topic,text}]}, values:[{value,label,count,none?}] \| range:{min,max,count}}]}`. `kind` is `terms`, `boolean` or `number`; `values` / `range` describe the indexed infrastructure data for the compared facets (for value pickers). `when_missing` (`unknown` or `incompatible`) is what a track without a value gives; a rule with `none_value` (e.g. "not electrified") lists it first in `values` with `none: true` and `count` = the tracks without a value, and a vehicle that has it is compatible with them (also for the rules whose `none_from` names it). For explaining a rule ("How is this checked?"): `infra` lists each compared facet with its label, the RINF property path it is read from (prefixed, e.g. `era:contactLineSystem/era:energySupplySystem`) and the classes indexing it; `source` is the rule as Turtle (`null` until the worker has republished its definitions); `explanation` is generated from the rule's annotations, `vehicle_path` the prefixed vehicle property path and `steps` one plain-language sentence per topic `compare` (the constraint), `missing` (a track without a value), `none` (the vehicle's "none" option), `fallback` (the section of line's value), `rollup` (how tracks make the section's colour) and `empty`, in that order, each present only when it applies. 404 when no rules are configured |
| POST | `/api/compatibility` | `{env, profile:{key: [values] \| number}}` | Status of every section of line for a vehicle profile: `{env, version, checked:[key], ignored:[key], counts:{green,yellow,red}, green:[id], yellow:[id], red:[id]}`. Ids are feature ids (the MVT feature id of the tiles); a feature keeps its id when its graph is indexed again. `version` identifies the indexed data: add it to the tile URL (`&v=`) of the layer you colour, so cached tiles from an older index are not reused. 400 when no filled-in characteristic has a rule or a value has the wrong type |
| POST | `/api/compatibility/explain` | `{env, profile, iri}` (or `id`) of a section of line | Why it has its status: `{iri, id, status, checked, ignored, section:[check], tracks:[{id,iri,label,class,status,results:[check]}]}` with `check = {key, result: "ok"\|"unknown"\|"fail", values:[{value,label}], note?, bypass?}` (the infrastructure values compared; `note` says why a track without a value got its result, e.g. "No contact line", or that it is compatible only because the vehicle has the rule's `none_value` and can do without the system, e.g. "Compatible in “Not electrified” mode"; `bypass` is that none value IRI) |
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
