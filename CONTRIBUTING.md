# Contributing

Thanks for helping improve RINF Atlas. Issues and pull requests are welcome.

## Layout

| Path | |
|---|---|
| `worker/` | ingest worker (`atlas` CLI): ERA discovery, Oxigraph mirror, SHACL-driven extraction, data quality validation |
| `api/` | FastAPI app: facets, search, vector tiles, export, line profile, vehicle check, OGC API – Features, live describe |
| `web/` | TypeScript + MapLibre frontend, served by nginx; mock API in `web/mock/`, browser tests in `web/e2e/` |
| `shapes/` | SHACL facet shapes (most data-model changes start here) and the vehicle check rules (`compatibility.ttl`) |
| `config/` | environments, data quality settings, example extra map layers |
| `db/init.sql` | PostGIS schema |
| `tests/` | `smoke.sh` and its sample data |
| `docs/` | `api.md` (HTTP contract), `urls.md` (link scheme), `datasets.md` (update cycle, data quality), `architecture.md`, `DESIGN.md` (design notes), `layers.md`, `privacy.md` |

## Running locally

```sh
cp .env.example .env
docker compose up -d --build
```

For a quick start, set `ATLAS_ENVS=prod` and `ATLAS_ONLY_GRAPHS=0082` (CFL,
Luxembourg) in `.env`.

Frontend only, against the bundled mock API (no Docker, no ERA access):

```sh
cd web
npm ci
npm run mock          # mock API on :8000
npm run dev:mock      # UI on http://localhost:5173/?mock
```

## Checks

CI runs the same checks; please run them before opening a pull request.

```sh
pip install ./worker -r api/requirements.txt pytest ruff
ruff check worker api
pytest -q worker/tests api/tests
(cd web && npm ci && npm run build)

# end to end: index the bundled Luxembourg sample and query the stack
# (ATLAS_IMAGE keeps the local build from taking the published images' tags, as in CI)
export ATLAS_ENVS=prod ATLAS_IMAGE=local/rinf-atlas
docker compose up -d --build --wait db api web
bash tests/smoke.sh

# browser tests (Playwright) against that stack, desktop and phone sizes
(cd web && npx playwright install chromium && npm run test:e2e)
```

The browser tests live in `web/e2e/`. A change to what the UI shows or does
should keep them passing, and a new feature gets a test there. They read the
expected counts from the API, so they hold when the sample changes;
`ATLAS_URL` points them at another instance, and `PW_CHANNEL=chrome` runs
them in an installed Google Chrome instead of Playwright's Chromium (as CI
does).

## Guidelines

- Search, facets and the map read only from PostGIS. Only `/api/describe`
  may query ERA live; keep it that way.
- An API change updates `docs/api.md` and the mock in `web/mock/server.mjs`.
- New UI state that changes what is shown goes in the link (`web/src/url.ts`) and `docs/urls.md`; old links keep working.
- Facet changes belong in `shapes/*.ttl` (see `shapes/atlas.ttl` for the
  annotations), not in code.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org):
  `<type>(<optional scope>): <description>`, with type `feat`, `fix`, `docs`,
  `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore` or `revert`
  (e.g. `fix(web): keep the legend open on zoom`). CI checks every commit in a
  pull request; run `scripts/check-commits.sh` to check yours locally.
- Keep pull requests focused, and describe what a user would see before and
  after the change.
- There is no changelog file: release notes live on
  [GitHub Releases](https://github.com/Matdata-eu/rinf-atlas/releases), generated
  from the merged pull requests and edited there, so give your pull request a
  Conventional Commits title that reads well as a line in them.
