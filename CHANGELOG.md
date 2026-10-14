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

### Changed
- Extraction keeps the `atlas:geometryFrom` links of features that have their
  own geometry too. The worker re-extracts every graph from the mirror once
  after upgrading (no download).
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

### Changed
- The worker keeps the `era:isPartOf` / `era:hasPart` links of every feature
  (not only those without geometry of their own), so tracks can be related to
  their section of line. The first start after upgrading re-extracts the index
  from the mirror once (no download).
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
  features (a section's start and end operational point, the section a track
  is part of). They show in the detail panel as links and in exports, but are
  not filters. The worker re-indexes once after upgrading because the shapes
  changed.
