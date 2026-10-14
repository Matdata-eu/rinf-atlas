# RINF Atlas: design notes

## Goals and constraints
1. Ingest per environment (UAT and PROD): only new or changed named graphs are
   downloaded, and every `era:InfrastructureElement` subclass is indexed into
   PostGIS.
2. Facets come from user-editable SHACL in `shapes/` (starter shapes generated
   from the ontology and the real data). Changing a shape re-indexes
   automatically.
3. FastAPI serves facets, search and MVT tiles from PostGIS; a TypeScript and
   MapLibre frontend in `web/`.
4. One `docker-compose.yml` with a scheduled poll; no Kubernetes.

Search, facets and the map never query ERA's SPARQL endpoint live (speed).
Only describing an entity and the graph traversal built on it go live, in the
style of the yasgui graph and geo plugins.

## ERA endpoints (verified 2026-10-14)
| | PROD | UAT |
|---|---|---|
| SPARQL (GraphDB repo) | `https://graph.data.era.europa.eu/repositories/rinf-plus` | `https://graph.uat.data.test-era.europa.eu/repositories/rinf-plus` |
| Dataset explorer API | `https://rinf.data.era.europa.eu/api/v1/dataset/published?latest=true&groupBy=im` | `https://rinf.uat.data.test-era.europa.eu/api/v1/...` (same path) |
| Ontology in the repo | v3.3.4 | v4.0.0 |

Found by reading the portal bundle (`/main.js`, lazy chunk `7235.js`). The
explorer API returns the latest dataset per IM: `id`, `fileName`,
`account.code/name/country`, `type` (RDF_FULL, XML_FULL, RDF_PARTIAL_MERGE),
`status`, `datePublished` (epoch ms), pipeline states.

Named graphs (RDF4J `GET {repo}/contexts`): provider data in
`http://data.europa.eu/949/graph/{IM code}` (e.g. `/graph/0088` Infrabel);
vocabularies in `/graph/rinf/ontology`, `/graph/rinf/skos`,
`/graph/rinf/countries`; also `/graph/nuts`, `/graph/borders`, `/graph/lex`,
`/graph/rinf/shacl`. PROD has ~61 graphs / 39 M triples, UAT ~58 / 27 M.
`GET {repo}/statements?context=<g>` with `Accept: application/n-triples`
streams one graph fast (100 MB in ~10 s); `GET {repo}/size?context=<g>` gives
its triple count.

## Architecture as built
Services: `db` (postgis/postgis:16-3.4), `worker` (Python, `atlas` CLI),
`api` (FastAPI, MVT via `ST_AsMVT`, so no Martin container: tiles depend on
the full query state, which Martin's table sources can't express), `web`
(nginx serving the Vite build, proxying `/api/` to `api:8000`).

- Change detection: fingerprint = explorer dataset id | datePublished |
  triple count. Graphs without an explorer entry still sync by triple count.
- Worker keeps a local Oxigraph mirror per env (`/data/mirror/{env}`, about
  270 bytes per triple on disk). Facet extraction runs SPARQL on the mirror
  with the provider graph as default graph. A shape change only re-extracts.
- Oxigraph's parallel `bulk_load` loses blank-node identity across chunks,
  which broke `owl:unionOf` domains in the ontology; files with blank nodes
  are skolemized to `urn:atlas:bnode:...` IRIs before loading.
- One query per class per complex facet path; plain predicates are batched
  with `VALUES ?p`. Facet values in `feature.facets jsonb {key: [values]}`;
  IRI labels in `term` (skos:prefLabel / rdfs:label from the mirror; IM role
  IRIs get the explorer's organisation name since the data only has a
  generic role label).
- Instances are indexed under the most specific shaped class they are
  asserted with.
- Geometry in real data: OP and SoL via `era:netReference/gsp:hasGeometry/
  gsp:asWKT`; tracks, sidings and platforms have none and get it from
  `era:isPartOf | ^era:hasPart` (OP or SoL); HABDs from `^era:tracksideHabd`;
  PrimaryLocation partly direct, else from the OP. KPs mostly lack geometry
  (would need LRS interpolation; not done). No provider used a WKT CRS prefix;
  axis order is still checked against Europe's extent.
- Labels: some providers append "(from YYYY-MM-DD until YYYY-MM-DD)" to
  rdfs:label; it is stripped for display.
- A filter on a plain facet key constrains every class having that facet; a
  `class/key` filter (used by the per-class facets in the UI) only that class.
- Tiles below z8 carry only `id, class` and dedupe identical pixels; classes
  have `atlas:minZoom` (tracks 10, sidings 11, KPs 12).
- The poll loop holds a Postgres advisory lock and owns the mirror; CLI
  `sync`/`reindex`/`generate-shapes` from another process are queued in the
  `job` table and run by the loop.

## Open points
- KilometricPost geometry via LRS (`era:hasLRS` + `era:measuredDistance`).
- UAT v4.0.0 adds TAF/TAP message classes as InfrastructureElement
  subclasses; they have shapes (0 instances) and the UI hides empty classes.
- Shapes are generated from the union of both ontologies; v4 renames would
  need a shape per version if they diverge.
