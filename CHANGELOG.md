# Changelog

All notable changes to this project are documented here: https://github.com/Matdata-eu/rinf-atlas/releases

## Unreleased

### Added
- Vehicle compatibility check (the "Vehicle check" map tool): describe a vehicle type by
  the technical characteristics you know (track gauge, gauging, energy supply,
  pantograph head, contact wire heights, ETCS level, class B protection, train
  detection, line category, train length, wheel diameter) and the map colours
  every section of line green (compatible), yellow (compatible where data
  exists, some characteristics unknown) or red (not compatible), with counts
  and a per-track explanation in the detail panel. Profiles stay in the
  browser, start from eight illustrative examples, and can be exported and
  imported as JSON or as Turtle (`era:VehicleType`). The rules are SHACL-style
  Turtle in `shapes/compatibility.ttl`; new endpoints `GET
  /api/compatibility/rules`, `POST /api/compatibility` and `POST
  /api/compatibility/explain`.

- Vehicle compatibility: an electric vehicle is not compatible with a track
  without a contact line (red, "No contact line" in the explanation) instead
  of "no data"; this covers the energy supply, pantograph head, contact wire
  heights and the new maximum train current characteristic. Add "Not
  electrified" to a profile's energy supply systems for a bi-mode vehicle: it
  is then compatible with every track for these characteristics, also
  electrified ones whose supply or pantograph it does not match ("Compatible
  in “Not electrified” mode" in the explanation).
  Rules declare it with `atlas:whenMissing`, `atlas:noneValue`,
  `atlas:noneFrom` and `atlas:missingReason`.

- Vehicle compatibility: "How is this checked?" for every characteristic, in
  the profile editor and in the per-track detail. It explains in plain
  language, generated from the rule in `shapes/compatibility.ttl`, which
  vehicle and RINF properties are compared and how, what a track without a
  value gives, the "not electrified" exception, the fallback to the section
  of line and how tracks make the section's colour, and shows the rule as
  Turtle. `GET /api/compatibility/rules` has the new `infra`, `source`,
  `explanation` and `section_facets` fields.

### Changed
- Vehicle compatibility looks parameters up on every track class (sidings
  too, and tracks typed `era:Track` get the parameters of running tracks)
  and falls back to the section of line's value when a track has none.
- The worker keeps the `era:isPartOf` / `era:hasPart` links of every feature
  (not only those without geometry of their own), so tracks can be related to
  their section of line. The first start after upgrading re-extracts the index
  from the mirror once (no download).

### Fixed
- Features keep their id when their graph is indexed again (they were deleted
  and inserted with new ids on every sync). Map tiles cached before a sync no
  longer refer to other ids than the API, which left the vehicle compatibility
  colours out at the zoom levels whose tiles were already loaded. The
  compatibility result also carries a data `version` that the map puts in the
  tile URL of the coloured layer.
- Below zoom 10, lines shorter than a pixel are kept in the tiles as a dot
  instead of being dropped, so networks of short sections of line (and their
  compatibility colours) no longer break up at low zoom.
