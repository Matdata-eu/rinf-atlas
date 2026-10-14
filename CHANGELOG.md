# Changelog

All notable changes to this project are documented here: https://github.com/Matdata-eu/rinf-atlas/releases

## Unreleased

### Added
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
