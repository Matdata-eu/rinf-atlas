# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-10-14

First public release.

### Added
- Ingest worker that discovers RINF datasets through ERA's dataset explorer,
  mirrors every provider graph of PROD and UAT in Oxigraph and indexes all
  `era:InfrastructureElement` subclasses into PostGIS.
- Editable SHACL facet shapes, generated from the ERA ontology and the data;
  shape changes re-index automatically from the local mirror.
- FastAPI service: facets, full-text search, extent, vector tiles, feature
  details and live describe for graph traversal.
- Web UI: map with per-class colours, shared and per-class facets, results
  list, describe and graph views, Datasets dialog, light/dark theme, About
  dialog with credits and a link to this repository for feedback.
- Export of the current results (selected classes, facets, datasets, search
  and map view) to CSV or Excel from the Results tab (`/api/export`).
- Documentation: how datasets are updated, architecture overview, HTTP API.
- docker-compose deployment, CI and container images on GHCR (amd64 and arm64).
- Licence notes in the README for the RINF data, ERA vocabulary, basemap
  tiles and dependencies; full OpenRailwayMap credit in the map attribution.

[Unreleased]: https://github.com/Matdata-eu/rinf-atlas/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Matdata-eu/rinf-atlas/releases/tag/v0.1.0
