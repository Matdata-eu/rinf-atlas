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
- "Part of" links: the `atlas:geometryFrom` paths (`era:isPartOf |
  ^era:hasPart` for tracks and most located elements) are written to
  `feature_link` (rule `from`) for **every** feature, not only for those
  without a geometry of their own, and indexed by target. This is the one
  record of what a feature belongs to: map colouring rolls track values up to
  their section of line, the vehicle compatibility check and the line profile
  find a section's tracks through it. `INDEX_VERSION` in `shapes.py` is part
  of the shapes digest; it is 2 since this change, so an upgrade re-extracts
  every graph from the mirror once (no download).
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
  the "part of" links.
  Palette slots and range breaks are per environment, not per query, so a
  filter never repaints a value; colours are chosen in the browser from
  colour-blind-checked palettes with light and dark steps. Suggested facets
  come from `atlas:colorBy` in the shapes.
- Line profile (`/api/profile`): `link` facets (`atlas:kind "link"`) keep the
  IRIs of a section's `opStart`/`opEnd`; a section's tracks come from the
  "part of" links. Link facets are stored in `feature.facets` like other
  values but never offered as filters nor counted in `facet_cache`. The API
  chains sections through them (longest path per connected group, remaining
  runs attached to the nearest end, gaps flagged) or routes with Dijkstra over
  all sections of the environment (cached until the data changes), and
  summarises track parameters per section (min/max for numbers, every value
  with its track count otherwise). Real gradient profiles are not in RINF
  (only "gradient for stabling tracks"), so there is no gradient row by
  default; any facet can be chosen as a row.
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
  the "part of" links):
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
## Data quality

ERA publishes the SHACL shapes RINF data must conform to in the repository
itself (`http://data.europa.eu/949/graph/rinf/shacl`: PROD about 7 600
triples, 76 node shapes, 211 `sh:sparql` constraints, 7 SPARQL-based targets,
GeoSPARQL `geof:distance` in some of them). The worker validates every
provider graph against them and stores the results in PostGIS, so the UI and
API read them like any other index data.

**Where it runs.** In the worker, on the local Oxigraph mirror, after a graph
is (re)indexed. The shapes graph is mirrored with the vocabularies on every
sync; a graph is validated again when its data changed or when the shapes'
fingerprint (sha256 of the sorted triples) differs from the one it was last
validated with, so a new shapes release re-validates everything once, smallest
graph first. `atlas validate [--env E] [--graph G] [--force]` runs it by hand.

**Engine options considered.**

| Option | Verdict |
|---|---|
| pySHACL (rdflib) | Correct and complete, but rdflib holds the graph in Python objects: roughly 1 KB+ per triple and hours for the 12M-triple 0080 graph. Not feasible in the worker's memory. |
| [shacl-report](https://github.com/Matdata-eu/shacl-report)'s built-in engine (Rust, SHACL Core + SPARQL, passes the W3C suite) | Fast, but fails on ERA's shapes (`custom function geof:distance not supported`), has no SPARQL targets, and is in-memory (~400 MB per 100k triples here). Used for what it is best at instead: the report viewer (below). |
| A SHACL engine on a triple store (Jena, TopBraid, RDF4J) | Another JVM service and a second copy of the data. |
| **Own validator on the mirror** (`worker/atlas/shacl.py`) | **Chosen.** Targets and value lists come from SPARQL on the mirror; Core constraints are evaluated in Python; `sh:sparql` constraints and SPARQL targets run as SPARQL on Oxigraph (which supports `geof:distance`). No new service, the data is already there. |

**How the validator stays fast enough.**

- One SPARQL query per node shape fetches every (focus node, path, value)
  of its property shapes; Core constraints (`sh:class`, `sh:datatype`,
  `sh:min/maxCount`, `sh:in`, `sh:pattern`, ranges, `sh:node`, `sh:or`/
  `sh:and`/`sh:not`/`sh:xone`, `sh:closed`, …) are then checked in Python.
  `sh:class` looks types up in the whole mirror with the subclass closure of
  the ontology, so values that are concepts or countries resolve.
- The provider graph and the vocabularies are copied into one default graph
  of a scratch Oxigraph store on disk: queries over a union default graph were
  5 to 20 times slower.
- SPARQL constraints of class-targeted shapes get a seed pattern
  (`?this rdf:type ?t` for the target classes) injected at the start: many of
  ERA's constraints begin with `OPTIONAL { $this rdfs:label ?l }`, which made
  Oxigraph enumerate every labelled resource first. Constraints that only use
  `$this` inside `FILTER NOT EXISTS` are pre-bound with `VALUES` in batches.
- Scratch queries run in a child process (`python -m atlas.shacl`) so a
  runaway query can be killed (`query_timeout`, 15 min) without losing the
  rest; that constraint is reported in the run's note.
- A per-graph time budget (`quality.time_budget_minutes`, 60) and result caps
  (`max_results_per_constraint` 10 000, `max_results_per_graph` 200 000) make
  a run `partial` instead of endless; counts per shape/constraint stay exact
  even when results are capped. Graphs above `quality.max_triples` (15M) are
  `skipped`.

Measured on this container (PROD shapes): the LU sample in 5 s, 0082 (115k
triples) in 27 s / 190 MB, 0088 (676k triples) in 13 min / 630 MB with every
rule. Two `sh:sparql` warnings on `era:RunningTrack` took 290 s each of those
13 minutes: `LinesideDistanceIndicationRequiredForSoL` (tracks of a section of
line must give `era:linesideDistanceIndication`) and
`MaxPermittedTrainLengthApplicability` (tracks on freight corridors must give
`era:maxPermittedTrainLength`). Both walk from every track to its section of
line and on to other resources, which Oxigraph evaluates as a large join, so
their cost grows faster than the graph.

**Default: those two rules are left out** (`quality.exclude_shapes` in
`config/environments.yaml`; the same list is the built-in default when the key
is absent). With them out, 0088 validates in about 3 minutes (187 s, 630 MB) instead of 13; the 19 violations are unchanged. A dataset left
with fewer checks says so in its note ("2 rule(s) left out by
configuration: …"), as does the report's `rdfs:comment`. To check them, set
`exclude_shapes: []` (or list only what should stay out); the exclusion list
is part of the fingerprint a graph was validated with, so changing it
re-validates every graph once. `exclude_shapes` matches node shapes, property
shapes and named `sh:sparql` constraints by IRI or regex.

The largest graphs (0080: 12M, 0087: 6.6M triples) may still end `partial`
at the 60-minute budget; raise `time_budget_minutes` for complete runs.

**Shapes source and versions.** The default source is the shapes graph in
ERA's repository, which follows ERA's updates but has no stable version name
(its `owl:versionInfo`, when present, is shown). A deployer can pin a tag of
ERA's ontology repository on GitLab instead (`quality.shapes_tag`, per
environment). `era-shacl/ERA-RINF-shapes.ttl` at v3.3.4 holds exactly the
node shapes and SPARQL constraints of PROD's shapes graph (76 and 211), so the
two sources agree for a matching release. The tag's file is downloaded through
GitLab's raw-file URL (the files API returned 404 behind some proxies), checked
to be Turtle, cached on disk per tag, and loaded into its own mirror graph
(`urn:atlas:shapes:{tag}`). Tags are meant to be immutable, so neither the
cached file nor that graph is ever refreshed. The validation fingerprint is
the shapes' content hash plus the rules left out, so switching tags (or back to
the graph) re-validates every dataset from the mirror without touching the
data. The source (label, URL, tag, file, `owl:versionInfo`, `dct:modified`) is
stored per dataset (`dataset.qa_source`), written as triples on each report and
as comments at the top of the shapes download.

Why the deployer picks the tag rather than the visitor: validation runs once
per dataset at ingest (minutes per large dataset) and its results drive the
facet, tiles and search. Letting a visitor choose among a few tags the deployer
enables would mean validating every dataset once per tag and keying
`violation`, `quality_count`, the reports and the `quality` facet by tag, about
doubling storage and worker time per extra tag. That is feasible but not built.

**Size limit.** `quality.max_triples` (default 15M, per environment,
`ATLAS_QUALITY_MAX_TRIPLES`) bounds the time and memory of one run. A dataset
above it is `skipped` with `qa_reason = 'too_large'` and the limit it exceeded:
its results and report are removed and its features get no `quality` value, so
it can never read as "no issues". A dataset is re-checked when it moves across
the limit (the limit is not part of the fingerprint, so changing it does not
re-validate datasets that were and stay below it).

**Storage.** `violation` (one row per kept result: focus, severity, shape,
component, path, value, message; indexed on `(env, focus)`),
`quality_count` (exact counts per shape/constraint/severity), the report
(gzipped Turtle, plus the shacl-report HTML) in `quality_report`, the shapes
Turtle in `quality_shapes`, and totals/status on `dataset`. Each feature gets
a builtin `quality` facet with the severities found on it (or `NoIssues`), so
filtering, counts, exports and the map need nothing new; tiles and search add
`qa`, the worst severity, computed from that facet. Only results whose focus
node is an indexed feature reach the map; the others (tracks without
geometry, contact line systems, …) are in the dataset counts and the report.

**shacl-report integration.** Every report is a standard
`sh:ValidationReport` (Turtle) and the shapes are downloadable, so both open
in shacl-report (or any SHACL tool). When the `shacl-report` binary is in the
worker image (copied from `ghcr.io/matdata-eu/shacl-report`), the worker also
renders its self-contained HTML viewer for reports up to
`quality.html_max_results` results, with the environment's SPARQL endpoint
preset; the API serves it inline in a CSP sandbox. shacl-report has no way to
open a remote report by URL (its public mode refuses URL loading), so the UI
links to a configurable instance (`ATLAS_SHACL_REPORT_URL`) with "drop both
files" instructions rather than deep links.

**Trade-offs and limits.** The validator implements the SHACL Core and SPARQL
features ERA's shapes use, not the whole spec: no
`sh:qualifiedValueShapesDisjoint`, no SHACL-AF, and `sh:or`/`sh:and`/`sh:not`/
`sh:xone`/`sh:node` only when the shapes they combine constrain the value
itself (true for ERA's). Anything else is counted in the run note as
unsupported rather than guessed. Results on blank-node focus nodes keep the
mirror's skolem IRIs (`urn:atlas:bnode:…`). A large provider takes minutes of worker
CPU per sync, after indexing, so a new dataset appears on the map before its
quality data. The results are as fresh as the mirror, not live.

## Open points
- KilometricPost geometry via LRS (`era:hasLRS` + `era:measuredDistance`).
- UAT v4.0.0 adds TAF/TAP message classes as InfrastructureElement
  subclasses; they have shapes (0 instances) and the UI hides empty classes.
- Shapes are generated from the union of both ontologies; v4 renames would
  need a shape per version if they diverge.
