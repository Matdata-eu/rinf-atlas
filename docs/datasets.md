# How datasets are updated

RINF Atlas never answers a search, facet count or map tile from ERA directly.
A background **worker** keeps a local copy of ERA's RINF data and an index
built from it, and refreshes both when ERA publishes something new.

## Where the data comes from

Each infrastructure manager (IM) uploads its RINF data to ERA. ERA publishes
it in a GraphDB repository per environment, with one named graph per IM:

| | PROD | UAT |
|---|---|---|
| SPARQL repository | `https://graph.data.era.europa.eu/repositories/rinf-plus` | `https://graph.uat.data.test-era.europa.eu/repositories/rinf-plus` |
| Dataset explorer API | `https://rinf.data.era.europa.eu/api/v1` | `https://rinf.uat.data.test-era.europa.eu/api/v1` |

- Provider graphs: `http://data.europa.eu/949/graph/{IM code}`, for example
  `…/graph/0088` for Infrabel. Which graphs count as provider data is set by
  `provider_graph_pattern` in `config/environments.yaml`.
- Vocabulary graphs: the ERA ontology, the SKOS concept schemes, the
  country list, the reference border points, the NUTS regions and the EU
  legislation (`…/graph/rinf/ontology`, `…/rinf/skos`, `…/rinf/countries`,
  `…/rinf/borders` or `…/borders`, `…/nuts`, `…/lex`). They provide labels,
  are used to generate the facet shapes, and are part of the data the SHACL
  shapes are checked against (as on ERA's side): an operational point's
  reference border point or NUTS region is only valid when it is found there.
  A change to them validates every provider graph again.

## The update cycle

Every `poll_interval_minutes` (default 30, `ATLAS_POLL_MINUTES` in `.env`)
the worker runs one cycle per enabled environment:

1. **Ask the dataset explorer** what is published:
   `GET {datasets_api}/dataset/published?latest=true&groupBy=im`, the call
   the explorer web page makes. It returns the latest dataset per IM: its id,
   file name, publication date and the IM's name and country. If the explorer
   is down, the cycle continues with the repository alone.
2. **List the repository's named graphs** (`GET {repo}/contexts`) and keep
   the provider graphs (optionally narrowed with `ATLAS_ONLY_GRAPHS`).
3. **Fingerprint each graph** as *dataset id | publication date | triple
   count* (the count comes from `GET {repo}/size?context=…`). A graph whose
   fingerprint matches the last successful load is skipped, so a cycle with
   no new uploads costs a few small requests. While the dataset explorer is
   down, the previous dataset id and date are kept and only the triple count
   is compared, so an outage does not re-download every graph.
4. **Download changed graphs** as one streaming N-Triples export
   (`GET {repo}/statements?context=…`), to a temporary file.
5. **Replace the graph in the local mirror**, an embedded Oxigraph store per
   environment on the `mirror` volume. The old graph is deleted in batches of
   small transactions: one transaction for a 1 GB graph needs several GB of
   memory and write-ahead log, enough to OOM the worker (issue #58). Blank nodes are rewritten to stable
   IRIs first, and triples above 1 MB (seen: schematic overviews embedded as
   huge WKT literals) are dropped if the parser cannot handle them.
6. **Re-index that graph**: for every class in `shapes/`, SPARQL on the mirror
   extracts each instance's label, geometry and facet values, which replace
   that graph's rows in PostGIS.
7. **Derive missing geometry**: tracks, sidings and platforms usually have no
   coordinates of their own and take them from the operational point or
   section of line they belong to; sections of line without a geometry get a
   line between their start and end points.
8. **Remove graphs that disappeared** from the repository, from both the
   mirror and the index.
9. **Validate the data quality** of each changed graph against the SHACL
   shapes ERA publishes (see below).
10. **Refresh the cached facet counts** used for the unfiltered view.

A graph that fails (network error, unparsable data) is marked *failed* with
the error and retried on the next cycle; the others carry on.

## Shape changes

Editing a file in `shapes/` changes what is extracted, not what is
downloaded. The worker notices the new shape set within a minute and
re-indexes every graph from the local mirror, without contacting ERA. For all
of PROD this takes about 20 minutes; the app stays usable meanwhile, with
each graph switching to the new facets as it is re-indexed.

## Manual runs

Inside the worker container (`docker compose exec worker atlas …`):

| Command | Effect |
|---|---|
| `atlas sync` | run a cycle now (`--env prod` for one environment) |
| `atlas sync --force` | download and re-index everything, ignoring fingerprints |
| `atlas reindex` | re-index from the mirror (what a shape change triggers) |
| `atlas validate` | validate the graphs not yet validated with the current SHACL shapes (`--graph G` for one, `--force` for all) |
| `atlas shapes-tags` | list the tags of ERA's ontology repository that `quality.shapes_tag` can pin the shapes to (GitLab API) |

While the poll loop runs it owns the mirror, so these commands are queued and
picked up by the loop within a minute.

## Seeing what was processed

The **Datasets** button at the top right of the web app (and
`GET /api/datasets?env=…`) lists every dataset per environment: IM, country,
file name, publication date, status (processed, processing, waiting, failed
with the error, removed), feature and triple counts, when it was processed,
and when the worker last checked for new datasets.

## Data quality

ERA publishes SHACL shapes for RINF data in the repository
(`http://data.europa.eu/949/graph/rinf/shacl`). The worker mirrors them with
the vocabularies and, after indexing a graph, validates it against them on
the local mirror. The results land in PostGIS: per dataset the number of
violations, warnings and info results (the **Data quality** column of the
Datasets dialog, which unfolds into the most frequent problems), per feature
its results (the feature's detail panel) and a `quality` filter plus a "Data
issues" map layer.

Each dataset's report can be downloaded as a standard `sh:ValidationReport`
(Turtle) and opened, together with the shapes, in
[shacl-report](https://github.com/Matdata-eu/shacl-report); when the worker
image includes the `shacl-report` binary (the default Dockerfile does), the
report is also rendered as shacl-report's HTML viewer, opened with
**Report**.

### Which shapes

By default each environment validates with the shapes graph ERA publishes in
its own repository (`http://data.europa.eu/949/graph/rinf/shacl`), so it
follows ERA's updates. To validate against a fixed release instead, set
`shapes_tag` to a tag of ERA's ontology repository on GitLab
(`gitlab.com/era-europa-eu/public/interoperable-data-programme/era-ontology/era-ontology`),
for every environment in the `quality:` block or for one environment in its
own `quality:` block, or with `ATLAS_QUALITY_SHAPES_TAG` (`v3.3.4`, or
`prod=v3.3.4,uat=v4.0.0`). `atlas shapes-tags` lists the tags, newest first.
The worker downloads the tag's `era-shacl/ERA-RINF-shapes.ttl` (from v3.1.8;
older tags fall back to `era-shapes.ttl`, which covers all of ERA's registers)
once into `ATLAS_SHAPES_CACHE` (the mirror volume, `/data/shapes-cache`), and
validates every dataset again from the local mirror: no data is downloaded
again. The Datasets dialog, each report and the shapes download name the
source and version used. If a tag cannot be fetched, the error shows in the
Datasets dialog and the previous results stay.

### Too large to validate

Datasets with more than `max_triples` triples (default 15 million, which
covers every PROD dataset of 2026-10; per environment, or
`ATLAS_QUALITY_MAX_TRIPLES`) are not validated. The Datasets dialog shows them
as "Not validated: too large (N triples > limit M)"; they have no counts, no
report, and their features have no value in the data quality filter (never
"No issues"). Raising the limit validates them on the next cycle; lowering it
turns datasets above it back into "too large".

### When it runs

A graph is validated again when its data or the shapes change. Settings
live in the `quality:` block of `config/environments.yaml`: the shapes
graph, the per-graph time budget (60 minutes; a run that hits it is shown as
a partial check), result caps, the maximum graph size (15M triples) and
rules to leave out. By default two very slow ERA warnings on running tracks
are left out (see [DESIGN.md](DESIGN.md#data-quality)); set
`exclude_shapes: []` to check them too. `ATLAS_QUALITY=0` turns validation off;
`ATLAS_QUALITY_MAX_TRIPLES` and `ATLAS_QUALITY_MINUTES` override the limits.

## Live queries

The only part of the app that queries ERA live is **Describe live** (and the
graph view built on it): the triples of one entity, in and out, fetched from
the environment's SPARQL endpoint and cached for 10 minutes.
