# Changelog

All notable changes to this project are documented here: https://github.com/Matdata-eu/rinf-atlas/releases

## Unreleased

### Added
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
