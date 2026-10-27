# Search engines and link previews

RINF Atlas does the technical part of search engine optimisation for every
instance. The parts tied to your domain and your organisation are yours.

## What the app does

The `web` container writes these when it starts
(`web/docker-entrypoint.d/40-atlas-seo.sh`), from two settings in `.env`:

| `.env` | Effect |
| --- | --- |
| `ATLAS_PUBLIC_URL` empty (default) | Search engines are asked not to index the instance: `robots.txt` disallows everything, pages carry `<meta name="robots" content="noindex">` and an `X-Robots-Tag: noindex` header. Right for private, internal and test instances. |
| `ATLAS_PUBLIC_URL=https://rinf-atlas.example.org` | Indexing allowed. `index.html` gets a canonical link, absolute `og:url` and `og:image` and JSON-LD (`schema.org/WebApplication`); `robots.txt` allows everything except `/api/` and points to `sitemap.xml`, which lists the home page and the pages in `site/`. |
| `ATLAS_PUBLIC_URL=…` and `ATLAS_INDEXING=off` | Link previews work (canonical and Open Graph tags), but search engines are asked not to index it. For a public test or UAT instance. |

Every instance also has a description, Open Graph and Twitter card tags, a
social preview image (`/og-image.jpg`, 1200 × 630), a heading and a
`<noscript>` text describing the app, so crawlers that do not run JavaScript
still see what it is. `/api/` always answers with `X-Robots-Tag: noindex`.

The settings apply on every start: change `.env` and run
`docker compose up -d web`. Check the result with:

```sh
curl -s https://rinf-atlas.example.org/robots.txt
curl -s https://rinf-atlas.example.org/ | grep -E 'canonical|robots'
docker compose logs web | grep atlas-seo
```

The whole app is one page: map position, filters and selection live in the
URL fragment (`#…`, [urls.md](urls.md)), which search engines ignore. So an
instance is one search result, not one per element.

## What you do for a public instance

1. **Serve it on its own HTTPS hostname**, for example
   `https://rinf-atlas.example.org`, not under a path of another site. Put a
   TLS proxy (Traefik, Caddy, nginx) in front of the `web` container.
2. **Set the public address** in `.env`: `ATLAS_PUBLIC_URL=https://rinf-atlas.example.org`
   (the same value the OGC API links use), then `docker compose up -d`.
3. **Redirect the alternatives** to that one address with a 301 on your
   proxy: `http://` to `https://`, `www.` to the bare name (or the other way
   round), and any old hostname. Search engines then count one address.
4. **Register the site** in [Google Search Console](https://search.google.com/search-console)
   and [Bing Webmaster Tools](https://www.bing.com/webmasters). Verify
   ownership with a DNS TXT record (nothing to change in the app), then
   submit `https://<your host>/sitemap.xml`. After a week or two, check that
   the home page is indexed.
5. **Check the link preview** by pasting your address into the
   [LinkedIn Post Inspector](https://www.linkedin.com/post-inspector/). Ask it
   to fetch again after an upgrade that changes the image or texts.
6. **Add pages of your own if you want**, such as a page about your
   organisation or the [privacy statement](privacy.md), in `site/` next to
   `docker-compose.yml`. They are served at `/site/…` and listed in the
   sitemap on the next start (`*.example.html` files are not).
7. **Analytics are your choice.** The app ships none. If you add some through
   your proxy, prefer a cookieless tool and mention it in `site/privacy.html`.
8. **Get linked.** For a specialised tool, ranking comes from links: your
   organisation's website, rail and open data community pages. A public
   instance can also be listed in the RINF Atlas README: open an issue.
9. **Keep `/api/` out of crawlers' reach.** If your proxy serves its own
   `robots.txt`, keep `Disallow: /api/`: crawlers following tile, search and
   export links load the server for nothing.

## Not covered

Prerendered pages per infrastructure element (one indexable page per
operational point or line) are out of scope: they would make the app a
catalogue site next to ERA's own pages. If you run a read-only root
filesystem (`read_only: true`), the start script cannot write and the
instance stays at the built-in noindex.
