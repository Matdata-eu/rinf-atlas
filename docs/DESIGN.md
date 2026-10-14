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
- Map colouring ("Colour by" a facet): the API writes one display value per
  feature into the unlogged `theme_value` cache the first time a facet is
  used (per environment and data version; the worker empties it after every
  update), so tiles at every zoom and the legend counts only join on the
  feature id. Sections of line are coloured from their running tracks through
  the `atlas:geometryFrom` links, which extraction now keeps for every feature
  (`EXTRACT_VERSION` 2, so an upgrade reindexes from the mirror once).
  Palette slots and range breaks are per environment, not per query, so a
  filter never repaints a value; colours are chosen in the browser from
  colour-blind-checked palettes with light and dark steps. Suggested facets
  come from `atlas:colorBy` in the shapes.
- The poll loop holds a Postgres advisory lock and owns the mirror; CLI
  `sync`/`reindex`/`generate-shapes` from another process are queued in the
  `job` table and run by the loop.

## Vehicle compatibility
A lightweight check of where a vehicle type can run, from a profile the user
fills in by hand (the "Vehicle" tab). Only PostGIS is read.

- Rules are declarative, in `shapes/compatibility.ttl`: one `sh:NodeShape`
  targeting `era:VehicleType` with `atlas:compatibility true`. Each
  `sh:property` is one vehicle characteristic: `sh:path` is its property in
  the ERA ontology (a sequence path for characteristics on
  `era:VehicleTypeConfigParameterSet`, e.g. the pantograph head),
  `atlas:compareTo` the indexed infrastructure facet(s), and
  `atlas:constraint` the SHACL constraint component the vehicle's value is
  plugged into and applied to the infrastructure values (`sh:hasValue`,
  `sh:in`, `sh:minInclusive`, `sh:maxInclusive`, `sh:minExclusive`,
  `sh:maxExclusive`). In effect a profile becomes a SHACL shape on the tracks:
  a 1435 mm vehicle is `sh:hasValue <.../nominal-track-gauges/30>` on
  `era:wheelSetGauge`, a 200 m train is `sh:minInclusive 200` on
  `era:maxPermittedTrainLength`.
- The worker validates the rules against the facet shapes (`atlas
  check-shapes` lists them) and publishes them as JSON in `compat_def`; rule
  changes need no reindex. The API compiles a profile to SQL over
  `feature.facets` (`?|`, `<@`, jsonpath comparisons), not pyshacl over the
  graph, so a Europe-wide check is one query (cached per profile and data
  version).
- Per track and characteristic: compatible, not compatible, or unknown when
  the track has no value for the facet. A track is red if any characteristic
  fails, else yellow if any is unknown, else green. A section of line takes
  the best of its running tracks (`running-track` and `track`, found through
  the `era:isPartOf` / `era:hasPart` links that also give them geometry; the
  worker now keeps those links even for tracks with their own geometry):
  green if one track is fully compatible, yellow if no track fails but some
  characteristics are unknown (or no track is indexed), red if every track
  fails. Rules with `atlas:appliesTo "section"` check the section itself and
  can only make it worse. Empty characteristics are not checked.
- Not every vehicle parameter is a compatibility constraint: maximum speed
  only limits the speed, so it has no rule. Ordered scales (gauging profiles,
  EN 15528 line categories, ETCS baselines) are compared by set membership:
  the user picks every value the vehicle is compatible with.
- The map draws a separate vector source (sections of line only, independent
  of the current filters) and colours it with MapLibre feature-state from the
  `{green|yellow|red: [feature id]}` response, so recolouring never refetches
  tiles; clicking a section shows the per-track explanation. Kept apart from
  generic thematic colouring on purpose, so the two can be merged later.
- Profiles live in `localStorage` (with four illustrative examples) and are
  exported / imported as JSON or as Turtle with `era:VehicleType` instances.
  When ERATV (the register of authorised vehicle types) is published as linked
  data, profiles could be imported from it instead of typed by hand.

## Open points
- KilometricPost geometry via LRS (`era:hasLRS` + `era:measuredDistance`).
- UAT v4.0.0 adds TAF/TAP message classes as InfrastructureElement
  subclasses; they have shapes (0 instances) and the UI hides empty classes.
- Shapes are generated from the union of both ontologies; v4 renames would
  need a shape per version if they diverge.
