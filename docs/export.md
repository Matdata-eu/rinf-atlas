# Exporting data

RINF Atlas exports what the map shows: every element matching the current
selection (classes, facet filters, search, datasets, map view). Two kinds of
export exist, both behind **Export…** in the Results panel.

| | Quick export | Full export |
|---|---|---|
| What | The facet columns, straight from the index | Every RINF property of each element, resolved from the local copy of ERA's data |
| Formats | CSV, Excel | Turtle, N-Triples, JSON-LD; CSV, Excel; GeoJSON, GeoPackage |
| How | Streamed at once (`GET /api/export`) | A job the worker runs; the link appears when it is ready |
| Limit | none | `ATLAS_EXPORT_MAX_ENTITIES` (none by default); `ATLAS_EXPORT_QUEUE` jobs at a time |

The full export answers the request for "more advanced exporting": the former
Turtle export fetched one hop live from ERA and stopped at 20,000 elements,
and the tables only held the facets.

## What a full export contains

For each selected element the worker reads its **resolved description** from
the mirror of the element's provider graph, hop by hop:

- the element's own triples;
- every **component** it points to that has triples in the same graph (net
  references and their LRS / topological coordinates, geometries, validity
  periods, contact line systems, ETCS, train detection, load capabilities,
  documents…), recursively, up to 8 hops;
- **other infrastructure elements** (the tracks of an operational point, the
  operational points of a section of line…) stay **references**: their IRI
  plus the label RINF Atlas indexed. Tick *Include parts* to also resolve the
  elements linked by `era:hasPart` one level deep (tracks of an operational
  point, platforms of a track);
- **concepts, countries, organisations** and other IRIs without triples in
  the graph are references with their `skos:prefLabel` / `rdfs:label` from the
  vocabulary graphs.

Blank nodes are written as blank nodes again. Nothing is queried live: an
export is reproducible from the datasets it names, and its **recipe**
(`/api/exports/{id}/recipe`, linked from the job card) spells out the
selection, the datasets with their dataset explorer fingerprint, and the
SPARQL patterns used, so anyone can check an element against ERA's endpoint.
If a dataset changed while the export ran, the job carries a warning.

## Formats

- **Turtle / N-Triples**: the resolved triples, one block per element in
  Turtle with a comment naming its class and graph. Loads into any triple
  store.
- **JSON-LD**: one nested object per element (components embedded, references
  as `{"@id", "rdfs:label"}`), with an `atlas:export` header describing the
  query.
- **CSV / Excel**, *one row per element* ("wide"): the base columns (IRI,
  label, class, dataset, provider, longitude/latitude of a point on the
  feature, WKT geometry) followed by the columns of an **export profile** (see
  below). Several values in a cell are joined with `; `. Excel gets one sheet
  per class plus an `Export` sheet describing the query.
- **CSV / Excel**, *one row per value* ("long"): `IRI, Class, Dataset,
  Property path, Value, Value IRI, Datatype, Language`; every property in the
  data, nested components as dotted paths (`netReference.startsAt.…`). No
  profile needed.
- **GeoJSON / GeoPackage**: the indexed geometry with the profile's columns as
  properties; a GeoPackage layer per class, for QGIS or ArcGIS.

Options: **Headers** as names, RINF parameter numbers (`1.2.0.0.0.1`) or
property paths; **Values** as labels, IRIs or both; **gzip** for the text
formats.

## Export profiles: which columns

Tabular and GIS exports take their columns from a profile in
`shapes/export/*.ttl`: SHACL node shapes, one per class, each `sh:property`
one column. The bundled **Full RINF** profile (`shapes/export/full.ttl`) is
generated from ERA's own RINF shapes, so it lists every parameter RINF allows
per class, with its `era:rinfIndex` number. A property whose value is a
component with its own shape (`sh:node`) flattens into sub-columns
(`Contact line system / Energy supply system`). In the dialog, *Choose
columns…* picks a subset per class.

```turtle
xp:full a atlas:ExportProfile ; atlas:key "full" ; rdfs:label "Full RINF" ; atlas:order 20 .

xp:full-OperationalPoint a sh:NodeShape ;
    atlas:profile xp:full ;
    sh:targetClass era:OperationalPoint ;
    rdfs:label "Operational point" ;
    sh:property [ sh:path era:opName ; rdfs:label "Name" ; era:rinfIndex "1.2.0.0.0.1" ; sh:order 10 ] ;
    sh:property [ sh:path ( era:primaryLocation era:latitude ) ; rdfs:label "Primary location / Latitude" ; sh:order 20 ] ;
    sh:property [ sh:path era:contactLineSystem ; rdfs:label "Contact line system" ; sh:node xp:full-ContactLineSystem ; sh:order 30 ] .
```

Edit the file, or add your own profile (another `atlas:ExportProfile` with its
shapes); the worker republishes profiles when the shapes folder changes, like
facets. Regenerate the full profile after an ontology update with
`atlas generate-export-profile --force` (it reads the ERA shapes graph of the
mirror). Annotations are documented in `shapes/atlas.ttl`.

## Running

Export jobs are run by the worker (`atlas run` starts the job runner next to
the poll loop; `atlas export-jobs [--once]` runs them on their own). Files go
to the `exports` volume (`ATLAS_EXPORTS`, `/data/exports`), which the api
mounts read-only to serve downloads. Settings in `.env`:

| Variable | Default | |
|---|---|---|
| `ATLAS_EXPORT_WORKERS` | `2` | parallel export jobs (pyoxigraph reads concurrently) |
| `ATLAS_EXPORT_RETENTION_HOURS` | `24` | finished files are deleted after this; the job then shows as expired |
| `ATLAS_EXPORT_MAX_ENTITIES` | empty | refuse exports of more elements (`413`); empty = no cap |
| `ATLAS_EXPORT_QUEUE` | `20` | queued + running jobs before `429` |

The browser remembers its jobs (local storage) and polls them; a job can be
cancelled or removed from the dialog. `atlas export --env prod --format ttl
--class operational-point [--graph IRI] [--profile full] [--shape long]
[--parts] [--gzip] [--out file]` writes the same export from the command line,
for whole datasets or scripted use.

API: `POST /api/exports`, `GET /api/exports/{id}`, `/download`, `/recipe`,
`DELETE /api/exports/{id}`, `GET /api/exports/profiles` in [api.md](api.md#exports).
