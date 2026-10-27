# Security policy

Please report vulnerabilities privately through GitHub's
[security advisories](https://github.com/Matdata-eu/rinf-atlas/security/advisories/new) for this repository
rather than in a public issue. You can expect a first answer within a week.

RINF Atlas is meant to run behind your own reverse proxy. It has no
authentication of its own: anyone who can reach the web port can search the
index and trigger live `describe` queries to ERA. Change `POSTGRES_PASSWORD`
in `.env` and do not expose the database port. Keys of extra map layers
(`ATLAS_LAYER_*`) stay on the server only for proxied layers; see
[docs/layers.md](docs/layers.md).
