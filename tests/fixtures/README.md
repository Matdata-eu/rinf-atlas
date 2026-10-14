# Test fixtures

`lu-sample.nt.gz`: a small extract (about 9,800 triples) of the CFL (Luxembourg)
provider graph `http://data.europa.eu/949/graph/0082` from ERA's RINF PROD
repository: 40 operational points, the sections of line between them, their
parts (tracks, platforms...) and the nodes they reference (net references,
geometries). Used by `tests/smoke.sh` and the CI end-to-end job.

Source: European Union Agency for Railways (ERA), Register of Infrastructure
(RINF), https://data-interop.era.europa.eu/. Extracted 2026-10-14.

`era-shacl.nt.gz`: the SHACL shapes ERA publishes for RINF data, graph
`http://data.europa.eu/949/graph/rinf/shacl` of the PROD repository (about
7,600 triples). `tests/smoke.sh` loads it after the sample, which validates
the sample (data quality). Without the ontology and SKOS graphs, the
`sh:class` checks on concept values report more violations than on the full
mirror. Same source, extracted 2026-10-14.
