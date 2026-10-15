## What changes

Before:

After:

## How

## Checks

- [ ] `ruff check worker api` and `pytest -q worker/tests`
- [ ] `npm run build` in `web/`
- [ ] Commit messages follow Conventional Commits (`scripts/check-commits.sh`)
- [ ] `docs/api.md` and `web/mock/server.mjs` updated if the API changed
