# Security policy

Please report vulnerabilities privately through GitHub's
[security advisories](../../security/advisories/new) for this repository
rather than in a public issue. You can expect a first answer within a week.

RINF Atlas is meant to run behind your own reverse proxy. It has no
authentication of its own: anyone who can reach the web port can search the
index and trigger live `describe` queries to ERA. Change `POSTGRES_PASSWORD`
in `.env` and do not expose the database port.
