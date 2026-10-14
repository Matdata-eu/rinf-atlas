# Changelog

All notable changes to this project are documented here: https://github.com/Matdata-eu/rinf-atlas/releases

## Unreleased

### Added

- GIS access: an OGC API – Features service at `/api/ogc` (one collection per
  environment and class, bbox, paging, facet filters as query parameters,
  CRS84 / EPSG:4326 / EPSG:3857 output) and `/api/ogc/filtered/{state}` for the
  current filters, plus TileJSON (`/api/tiles.json`) and a MapLibre style
  (`/api/style.json`) for the vector tiles, so QGIS, ArcGIS Pro and GDAL can
  load Atlas layers directly. An **Open in GIS** button next to the exports
  copies the URL for the current classes and filters; see "Use in QGIS" in the
  README.
- **Colour the map by a property** ("Map colours" above the class list, or the
  palette icon on any facet): sections of line, tracks and any other class are
  coloured by the chosen facet, with a categorical palette for values and
  yes/no facets and sequential classes for numbers (for example maximum
  speed). Sections of line take the values of their running tracks (the
  highest number, or "Mixed" when the tracks differ). A collapsible legend
  shows each value or class with its count; click to hide one, Shift+click to
  show only it, hover to highlight it, × to go back to class colours. The
  choice is kept in the URL. Colours are checked for colour-blind safety in
  the light and dark themes.
- API: `POST /api/theme`, `color` on `/api/tiles` and `/api/search`
  (docs/api.md, "Map colouring"); shape annotation `atlas:colorBy` lists a
  facet among the suggestions.
- Vehicle compatibility check (the "Vehicle" tab): describe a vehicle type by
  the technical characteristics you know (track gauge, gauging, energy supply,
  pantograph head, contact wire heights, ETCS level, class B protection, train
  detection, line category, train length, wheel diameter) and the map colours
  every section of line green (compatible), yellow (compatible where data
  exists, some characteristics unknown) or red (not compatible), with counts
  and a per-track explanation in the detail panel. Profiles stay in the
  browser, start from four illustrative examples, and can be exported and
  imported as JSON or as Turtle (`era:VehicleType`). The rules are SHACL-style
  Turtle in `shapes/compatibility.ttl`; new endpoints `GET
  /api/compatibility/rules`, `POST /api/compatibility` and `POST
  /api/compatibility/explain`.
- Line profile: lay out a national line, a series of sections of line picked
  on the map, or the route between two operational points as a strip along
  the cumulative distance, with one row per parameter of the sections'
  tracks (maximum speed, electrification, gauge, ETCS level, gauging, line
  category by default; any facet can be a row). Sections are put in order and
  gaps are flagged; hovering the strip highlights the section on the map and
  the other way round, clicking opens its details, and the strip downloads as
  CSV. Works as a bottom sheet with a sideways scrolling strip on phones.
  New endpoints `GET /api/lines` and `POST /api/profile`.
- Shapes: new `atlas:kind "link"` for facets that hold IRIs of other indexed
  features (a section's start and end operational point). They show in the
  detail panel as links and in exports, but are not filters.
- Data quality: the worker validates every provider dataset against the SHACL
  shapes ERA publishes (`graph/rinf/shacl`) on its local mirror and stores the
  results in PostGIS. The Datasets dialog shows violations, warnings and info
  per infrastructure manager with the most frequent problems; the map gets a
  "Data issues" layer coloured by severity and a "Data quality (ERA SHACL)"
  filter; a feature's detail panel lists its results. Reports download as
  standard `sh:ValidationReport` Turtle and open in
  [shacl-report](https://github.com/Matdata-eu/shacl-report), whose HTML
  viewer is also built per dataset. New `atlas validate` command, `quality:`
  settings and `/api/quality/*` endpoints.
- Data quality leaves out two ERA SHACL warnings by default
  (`LinesideDistanceIndicationRequiredForSoL`,
  `MaxPermittedTrainLengthApplicability`), whose SPARQL took about 5 minutes
  each on a mid-sized dataset; `quality.exclude_shapes: []` turns them back
  on. `exclude_shapes` now also matches named `sh:sparql` constraints, a
  dataset's note lists the rules left out, and changing the list re-validates.
- Data quality shapes can be pinned to a tag of ERA's ontology repository on
  GitLab (`quality.shapes_tag`, per environment, or
  `ATLAS_QUALITY_SHAPES_TAG`); the tag's shapes are downloaded once and cached,
  and changing the tag re-validates from the mirror. `atlas shapes-tags` lists
  the tags. The Datasets dialog, each report (as triples) and the shapes
  download name the shapes' source and version.
- The data quality size limit (`quality.max_triples`, default 15 million
  triples) can be set per environment as well as with
  `ATLAS_QUALITY_MAX_TRIPLES`. Datasets above it show as "Not validated: too
  large (N triples > limit)" and are never counted as having no issues.

### Changed

- The worker keeps the "part of" links (`atlas:geometryFrom`, i.e.
  `era:isPartOf` / `era:hasPart`) of every feature, not only of those without
  a geometry of their own, and indexes them by target. Map colouring, the
  vehicle compatibility check and the line profile all find a section's
  tracks through them. The first start after upgrading re-extracts every
  graph from the mirror once (no download).
