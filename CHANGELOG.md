# Changelog

All notable changes to this project are documented here: https://github.com/Matdata-eu/rinf-atlas/releases

## Unreleased

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
