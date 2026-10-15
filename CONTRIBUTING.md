# Contributing

Thanks for helping improve RINF Atlas. Issues and pull requests are welcome.

## Layout

| Path | |
|---|---|
| `worker/` | ingest worker (`atlas` CLI): ERA discovery, Oxigraph mirror, SHACL-driven extraction |
| `api/` | FastAPI app: facets, search, vector tiles, live describe |
| `web/` | TypeScript + MapLibre frontend, served by nginx |
| `shapes/` | SHACL facet shapes (most data-model changes start here) |
| `db/init.sql` | PostGIS schema |
| `docs/` | `api.md` (HTTP contract), `urls.md` (link scheme), `DESIGN.md` (design notes) |

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
docker compose up -d --build db api web
bash tests/smoke.sh
```

## Guidelines

- Search, facets and the map read only from PostGIS. Only `/api/describe`
  may query ERA live; keep it that way.
- An API change updates `docs/api.md` and the mock in `web/mock/server.mjs`.
- New UI state that changes what is shown goes in the link (`web/src/url.ts`) and `docs/urls.md`; old links keep working.
- Facet changes belong in `shapes/*.ttl` (see `shapes/atlas.ttl` for the
  annotations), not in code.
- Keep pull requests focused, and describe what a user would see before and
  after the change.
