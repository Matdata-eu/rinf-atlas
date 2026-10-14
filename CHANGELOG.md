# Changelog

All notable changes to this project are documented here: https://github.com/Matdata-eu/rinf-atlas/releases

## Unreleased

### Added

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
