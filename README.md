# RINF Atlas

[![CI](https://github.com/Matdata-eu/rinf-atlas/actions/workflows/ci.yml/badge.svg)](https://github.com/Matdata-eu/rinf-atlas/actions/workflows/ci.yml)
[![Images](https://github.com/Matdata-eu/rinf-atlas/actions/workflows/docker-publish.yml/badge.svg)](https://github.com/Matdata-eu/rinf-atlas/pkgs/container/rinf-atlas-web)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A fast map + faceted search over ERA's Register of Infrastructure (RINF), for
every subclass of `era:InfrastructureElement` (operational points, sections of
line, tracks, sidings, tunnels, platforms, kilometric posts, ...), with PROD
and UAT side by side.

**Demo:** https://rinf-atlas.matdata.eu. It runs on a home
server and is hosted on a best-effort basis, so expect occasional downtime.

Documentation: [how datasets are updated](docs/datasets.md),
[architecture](docs/architecture.md), [HTTP API](docs/api.md),
[design notes](docs/DESIGN.md).

![RINF Atlas: operational points and sections of line in Belgium](docs/screenshots/overview.png)

A **line profile** lays a national line, a series of sections of line picked on
the map, or the route between two operational points out as a strip along the
distance, with one row per parameter of their tracks (maximum speed,
electrification, gauge, ETCS level, gauging, line category...; rows are
configurable), linked to the map and downloadable as CSV.

Search and the map never query ERA live: a worker mirrors the RINF named
graphs and indexes them into PostGIS. Only "describe" (and the graph
traversal built on it) goes to ERA's SPARQL endpoint.

```
ERA dataset explorer API ──┐                      ┌── web (nginx + MapLibre UI) :8080
ERA GraphDB (rinf-plus) ───┼─> worker ─> PostGIS ─┤
                           │   (Oxigraph mirror)  └── api (FastAPI: facets, search, MVT tiles)
                           └──────────── live describe only ──────────┘
```

## Run it

With the published images (amd64 and arm64, from GitHub Container Registry):

```sh
cp .env.example .env        # optional: port, password, environments, graph subset
docker compose pull
docker compose up -d
docker compose logs -f worker
```

Or build from source with `docker compose up -d --build`. Set `ATLAS_VERSION`
in `.env` to pin a release (for example `0.1.0`) instead of `latest`.

Open `http://<host>:8080`. The first sync downloads every provider graph of
PROD and UAT (about 40 M and 27 M triples; the largest are DB InfraGO and
SNCF Réseau). Measured on a 4-core test box: all of PROD (61 graphs, 969k
features) took 34 minutes, then UAT follows; the map fills in as graphs land. To try it on a
few countries first, set for example `ATLAS_ONLY_GRAPHS=0088,0084,0082`
(Infrabel, ProRail, CFL) in `.env`.

Disk, measured for PROD: 7.6 GB mirror volume and 3.1 GB PostGIS; plan for
about 15 GB + 6 GB with UAT too, plus 2 GB of temporary space while the
largest graph downloads. RAM: 4 GB is comfortable.

Measured API timings on full PROD: unfiltered or repeated facet requests
50-200 ms, the first request for a new heavily filtered state 1-3.5 s, search
about 80 ms, vector tiles 80-600 ms.

## How new data is picked up

Users see what the backend has processed in the **Datasets** dialog (the
"54/54 datasets processed" button top right): every dataset per
environment with its infrastructure manager, country, file name, publication
date, status (processed, processing, waiting, failed with the error, removed),
feature and triple counts and when it was processed, plus when the worker last
checked for new datasets. "Show on map" limits the map and facets to that
dataset (shareable via the URL).

Every `poll_interval_minutes` (default 30) the worker, per environment:

1. reads the dataset explorer API
   (`{datasets_api}/dataset/published?latest=true&groupBy=im`, the same call
   the explorer page makes) for the latest dataset per infrastructure manager;
2. lists the repository's named graphs (`/contexts`); provider data lives in
   `http://data.europa.eu/949/graph/{IM code}`;
3. fingerprints each graph as *dataset id | publication date | triple count*
   and only re-downloads graphs whose fingerprint changed (one streaming
   N-Triples export per graph via the RDF4J `/statements` endpoint);
4. replaces the graph in the local mirror, re-extracts its features, and
   derives missing geometry; graphs that disappeared are removed.

## Facets are SHACL you own

`shapes/*.ttl` holds one SHACL node shape per class, plus `shared.ttl` for
facets every class has (country, infrastructure manager). They were generated
from the ERA ontology and the real data (properties used by fewer than 10
instances were left out) and are meant to be edited:

```turtle
shape:operational-point a sh:NodeShape ;
    atlas:key "operational-point" ;
    sh:targetClass era:OperationalPoint ;
    atlas:geometryPath [ sh:alternativePath ( ( gsp:hasGeometry gsp:asWKT )
                                              ( era:netReference gsp:hasGeometry gsp:asWKT ) ) ] ;
    sh:property [
        sh:path era:opType ;            # any SHACL path: sequence, inverse, alternative, *, +, ?
        atlas:facet "opType" ;
        rdfs:label "Type of operational point" ;
        atlas:kind "terms" ;            # terms | range | boolean | text (search only) | link
        sh:order 50 ;
    ] .
```

All annotations are documented in `shapes/atlas.ttl`. The worker notices a
changed shape set on its next loop (within a minute) and re-indexes from the
local mirror; nothing is downloaded again (full PROD: about 20 minutes). Invalid shapes are logged and the
current index is kept.

Geometry comes from `atlas:geometryPath`; features without their own
geometry get one from linked features: `atlas:lineBetween (era:opStart
era:opEnd)` for sections of line, `atlas:geometryFrom` (for tracks: the
operational point or section of line they are part of; platforms: their
track; and so on, resolved in chains).

## Commands

Run inside the worker container (`docker compose exec worker atlas ...`).
While the poll loop runs it owns the mirror, so these are queued and picked
up by the loop:

| Command | What it does |
|---|---|
| `atlas sync [--env prod] [--force]` | poll now; `--force` re-downloads everything |
| `atlas reindex [--env prod]` | re-extract from the mirror (also automatic on shape changes) |
| `atlas generate-shapes [--force] [--no-data]` | regenerate starter shapes into `shapes/` (keeps existing files unless `--force`) |
| `atlas check-shapes [-v]` | parse `shapes/` and print the compiled facets |
| `atlas load-file --env prod --graph IRI file.nt` | index a local file (testing; stop the worker first) |

## Configuration

`config/environments.yaml` lists the environments (SPARQL repository,
dataset explorer API, vocabulary graphs) and the provider-graph pattern.
`.env` sets the port, database password, `ATLAS_ENVS` (subset of
environments) and `ATLAS_ONLY_GRAPHS` (subset of graphs).

To publish a privacy statement for your instance, put a page at
`site/privacy.html` (start from `site/privacy.example.html`); the app then
links it as **Privacy**. [docs/privacy.md](docs/privacy.md) explains how and
lists what the app processes, as a basis for what to disclose.

## Layout

| Path | |
|---|---|
| `worker/` | ingest worker (`atlas` CLI): discovery, Oxigraph mirror, SHACL-driven extraction, shape generator |
| `api/` | FastAPI app: facets, search, extent, vector tiles (`ST_AsMVT`), feature, live describe |
| `web/` | TypeScript + MapLibre + vis-network frontend (built into the nginx image) |
| `shapes/` | facet shapes (edit these) |
| `site/` | optional pages for your instance, such as `privacy.html` |
| `db/init.sql` | PostGIS schema |
| `docs/api.md` | HTTP API contract |
| `docs/datasets.md` | how datasets are updated |
| `docs/architecture.md` | architecture overview |
| `docs/DESIGN.md` | design notes |
| `docs/privacy.md` | adding a privacy statement; what the app processes |

## Feedback

Bugs, feature requests and questions: [open an issue](https://github.com/Matdata-eu/rinf-atlas/issues/new/choose).
The web app links here from its About dialog (the ⓘ button at the top right).

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md): the frontend runs against a bundled
mock API, and `tests/smoke.sh` indexes a small Luxembourg sample
(`tests/fixtures/`) into a running stack and checks every endpoint. CI runs
lint, tests, the frontend build and that end-to-end test; pushes to `main` and
version tags publish the images.

## Licence and data

Code: [MIT](LICENSE). The MIT licence covers this repository's code only, not
the data the app shows or the services it loads at runtime:

- **RINF data** comes from the European Union Agency for Railways' Register of
  Infrastructure (RINF). ERA publishes it because EU law requires it
  (Directive (EU) 2016/797, Regulation (EU) 2019/777). The infrastructure
  managers who submit it remain responsible for it, and ERA's
  [RINF terms of use](https://www.era.europa.eu/system/files/2022-11/rinf_terms_of_use_en.pdf)
  apply. No data is stored in this repository except the small test extract in
  `tests/fixtures/`, which credits its source. This project is not affiliated
  with ERA.
- **ERA vocabulary**: the facet shapes in `shapes/` use class and property IRIs
  and labels from the [ERA ontology](https://data-interop.era.europa.eu/era-vocabulary/)
  (EUPL 1.2).
- **Basemap**: raster tiles from the [OpenStreetMap](https://www.openstreetmap.org/copyright)
  tile server (data © OpenStreetMap contributors, ODbL). The servers are
  donated, so follow the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/):
  a busy deployment should point at its own or a commercial tile server.
- **Rail overlay** (optional): [OpenRailwayMap](https://www.openrailwaymap.org/) tiles
  (CC-BY-SA 2.0, data ODbL), free only for small, public, non-commercial use
  ([conditions](https://wiki.openstreetmap.org/wiki/OpenRailwayMap/API)).
- **Dependencies** are under permissive licences (MIT, BSD, ISC, Apache-2.0)
  or weak copyleft used unmodified: psycopg (LGPL-3.0) and, at build time only,
  lightningcss (MPL-2.0). The PostGIS image (GPL-2.0) runs as an unmodified,
  separate container.
