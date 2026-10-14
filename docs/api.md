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
| GET  | `/api/feature?env=&iri=` or `?env=&id=` | | `{iri, label, classes:[...], class_iri, graph_iri, provider, dataset, geometry_source, facets:{key:[{value,label}]}, facet_labels:{key:label}, facet_kinds:{key:kind}, geometry: GeoJSON}` (`link` facets hold IRIs of other indexed features, e.g. a section's `opStart` / `opEnd`, a track's `partOf`) |
| GET  | `/api/lines?env=&q=&limit=` | `limit` ≤ 100 (default 20) | National lines whose id (`nationalLine.lineId` of sections of line) contains `q`, exact and prefix matches first: `[{line, graph_iri, provider, sections, length_km}]`. Line ids are national, so the same id can come back once per dataset |
| POST | `/api/profile` | see [Line profile](#line-profile) | Sections in order along a line, a list of sections or a route, with track parameters per section |
| GET  | `/api/describe?env=&iri=&direction=out\|in` | `limit` (default 1000) | Live from ERA. `{iri, direction, triples:[{s,p,o:{type:"uri"\|"literal"\|"bnode",value,datatype?,lang?}}], labels:{iri:label}, truncated}` |
| | | | Query states also accept `graphs: [graph IRI]` to keep only features from those datasets. A filter or `facets` key written `class/key` (e.g. `operational-point/opType`) applies to that class only; a plain key applies to every class that has the facet. The UI uses plain keys for the shared facets (country, IM) and `class/key` for the facets behind each class's filter icon. |
| GET  | `/api/datasets?env=` | | `[{graph_iri,title,provider,country,modified,status,triples,ingested_at,error,dataset_id,file_name,features}]`: every provider graph the worker knows; `status` is pending, loading, loaded, failed or removed. Shown in the UI's Datasets dialog |
| GET  | `/api/export?state=&format=csv\|xlsx&values=label\|iri` | `state` = base64url(JSON query state, as for tiles; bbox is honoured) | Every matching feature as a download. Columns: IRI, label, class, dataset, provider, longitude/latitude (a point on the feature), WKT geometry, then one column per facet of the exported classes (shared facets first). Multiple values are joined with `; `. `values=label` (default) writes labels for IRI values, `iri` keeps the IRIs. **CSV**: one UTF-8 table (with BOM, for Excel), streamed. **XLSX**: one sheet per class plus an `Export` sheet describing the query; cells over Excel's 32 767 character limit (long WKT lines) are left empty |
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

Parameters are read from the section itself and from every track whose
`partOf` link points at it. With several tracks, a `range` row gives the
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
  properties are `id, iri, class, label`. **Below zoom 8 only `id, class` are
  included** and features that collapse onto the same pixels are sent once,
  which keeps a Europe-wide tile small; resolve a click with
  `/api/feature?env=&id=`. Classes are left out of tiles below their
  `min_zoom` (from the shapes, see `/api/meta`). Lines are simplified below
  zoom 10. Tiles are sent with `Cache-Control: public, max-age=300`.
- Describe responses are cached in the API for 10 minutes.
