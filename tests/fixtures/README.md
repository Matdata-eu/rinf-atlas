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
7,600 triples), unmodified apart from the N-Triples serialisation.
`tests/smoke.sh` loads it after the sample, which validates the sample (data
quality). Without the ontology and SKOS graphs, the `sh:class` checks on
concept values report more violations than on the full mirror.

Source: European Union Agency for Railways (ERA), RINF SHACL shapes
(`http://data.europa.eu/949/id/dataset/rinf/era-shapes`), version 3.3.4
(`owl:versionInfo`, last `dct:modified` 2026-05-18), from
https://data-interop.era.europa.eu/, extracted 2026-10-14. The same shapes
are maintained in ERA's ontology repository,
https://gitlab.com/era-europa-eu/public/interoperable-data-programme/era-ontology/era-ontology
(`era-shacl/ERA-RINF-shapes.ttl`, tag `v3.3.4`). © European Union Agency for
Railways; creator and publisher ERA. Licensed under the European Union Public
Licence v1.2 (EUPL-1.2): the shapes declare it themselves (`dct:license
<http://data.europa.eu/eli/dec_impl/2017/863/oj>`) and so does the repository.
A copy of the licence, taken from that repository at tag `v3.3.4`, is in
[`third_party/era-ontology/LICENSE`](../../third_party/era-ontology/LICENSE).

`kp-nogeom.nt`: a made-up kilometric post with a label and a km value but no
geometry, like the ones in DB InfraGO's dataset. `tests/smoke.sh` adds it to
the sample to check that features without a location are counted as such.

`era-classes.nt`: the class hierarchy below `era:InfrastructureElement` from
the ERA ontology (`rdfs:subClassOf` and English `rdfs:label` of the 19
infrastructure element classes, 38 triples), version 3.3.4, graph
`http://data.europa.eu/949/graph/ontology` on the mirror. Used by the worker's
export tests to generate the Full RINF export profile from `era-shacl.nt.gz`.
Same source and licence (EUPL-1.2) as the shapes above.
