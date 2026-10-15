# Privacy statement

RINF Atlas does not ship a privacy statement of its own: whoever runs an
instance is the one who processes visitors' data, so they write it. The app
gives that statement a place.

## Adding one

Put a page at `site/privacy.html` next to `docker-compose.yml`. The `web`
container serves the `site/` folder read-only at `/site/`, and when
`site/privacy.html` exists the app shows a **Privacy** link in the map
attribution (bottom right) and in the About dialog. Without the file there is
no link. No rebuild or restart is needed: the app checks for the page on every
load.

```sh
cp site/privacy.example.html site/privacy.html   # then edit it
```

The page is served as is, so it can be a full HTML page with your own styling
(it opens in a new tab). If the statement already lives elsewhere, for example
on your organisation's website, make `site/privacy.html` a redirect:

```html
<!doctype html>
<meta http-equiv="refresh" content="0; url=https://example.org/privacy">
<a href="https://example.org/privacy">Privacy statement</a>
```

`site/privacy.html` is git-ignored, so `git pull` does not touch it. If you
run the published images without a checkout, create the `site/` folder next
to your `docker-compose.yml`.

## What the app processes

A starting point for what to disclose, for an instance run with the default
configuration. Check it against your own setup (reverse proxy, log shipping,
backups, hosting provider).

**In the visitor's browser**

- No cookies, analytics, trackers or third-party fonts and scripts.
- `localStorage` key `atlas-theme`, only when the visitor picks a light or
  dark theme. It never leaves the browser.
- The map position, filters and selection live in the URL fragment (`#…`) for
  shareable links. Browsers do not send the fragment to any server.

**Requests the browser makes to third parties**

- Basemap tiles from `tile.openstreetmap.org` (OpenStreetMap Foundation) on
  every map view. The visitor's IP address, user agent and the page as
  referrer reach the OSMF, whose
  [privacy policy](https://osmfoundation.org/wiki/Privacy_Policy) applies.
- Railway overlay tiles from `{a,b,c}.tiles.openrailwaymap.org`, only while
  the visitor has the OpenRailwayMap overlay switched on.
- Tiles of the extra layers you configured in `config/layers.yaml`
  ([layers.md](layers.md)) that are not proxied, from their providers, only
  while the visitor has that layer switched on. Proxied layers are fetched by
  your server instead, so the provider sees your server's address.

**On your server**

- nginx (`web`) logs every request: client IP address, time, URL and user
  agent. Tile and search URLs contain the active filters. uvicorn (`api`)
  logs requests as well, with the IP address of the nginx container. Both go
  to the Docker log driver; retention is whatever you configure there
  (`docker compose logs` keeps them until the containers are removed).
- Searches, filters and exports are answered from the local PostGIS database
  and are not stored apart from those logs.

**Requests your server makes to ERA**

- The `worker` polls ERA's dataset explorer and SPARQL endpoint to keep the
  index current.
- "Describe live" and the graph view ask the `api` to query ERA's SPARQL
  endpoint for the selected element. The request comes from your server and
  carries only the element's IRI, not the visitor's IP address.

The RINF data itself describes railway infrastructure published by the
infrastructure managers through ERA.
