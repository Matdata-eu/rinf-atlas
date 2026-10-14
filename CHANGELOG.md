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
