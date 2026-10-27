#!/bin/sh
# Search engine settings of this instance, written when the container starts
# (the nginx image runs /docker-entrypoint.d/*.sh before nginx). See docs/seo.md.
#
#   ATLAS_PUBLIC_URL  public address, e.g. https://rinf-atlas.example.org.
#                     Set: canonical link, absolute og:url/og:image, sitemap, indexing allowed.
#                     Empty: the instance asks search engines not to index it.
#   ATLAS_INDEXING    "off" keeps an instance with a public address out of search engines
#                     (test, UAT); anything else lets ATLAS_PUBLIC_URL decide.
set -eu

ME=$(basename "$0")
HTML=/usr/share/nginx/html
SNIPPET_DIR=/etc/nginx/atlas

url=$(printf '%s' "${ATLAS_PUBLIC_URL:-}" | sed 's#/*$##')
# Only plain URL characters: the value is written into HTML, JSON and XML as is.
if [ -n "$url" ] && ! printf '%s' "$url" | grep -Eq '^https?://[A-Za-z0-9._~:/?#@!$()*+,;=%-]+$'; then
  echo "$ME: ATLAS_PUBLIC_URL is not a plain http(s) URL, ignoring it: $url" >&2
  url=
fi

index=no
if [ -n "$url" ] && [ "${ATLAS_INDEXING:-}" != off ]; then index=yes; fi

if ! [ -w "$HTML/index.html" ] || ! [ -w "$HTML" ]; then
  echo "$ME: $HTML is read-only, leaving the search engine settings as built" >&2
  exit 0
fi

# Tags between the atlas:seo markers in index.html (replaced on every start).
head=$(mktemp)
if [ -n "$url" ]; then
  cat > "$head" <<EOF
    <link rel="canonical" href="$url/" />
    <meta property="og:url" content="$url/" />
    <meta property="og:image" content="$url/og-image.jpg" />
    <meta name="twitter:image" content="$url/og-image.jpg" />
    <script type="application/ld+json">{"@context":"https://schema.org","@type":"WebApplication","name":"RINF Atlas","url":"$url/","applicationCategory":"ReferenceApplication","operatingSystem":"Any (web browser)","isAccessibleForFree":true,"offers":{"@type":"Offer","price":"0","priceCurrency":"EUR"},"description":"Search and map the EU Register of Railway Infrastructure (ERA RINF): operational points, sections of line, tracks, tunnels, bridges, signals and more.","image":"$url/og-image.jpg","license":"https://opensource.org/licenses/MIT","codeRepository":"https://github.com/Matdata-eu/rinf-atlas","isBasedOn":"https://www.era.europa.eu/domains/registers/rinf_en"}</script>
EOF
fi
if [ "$index" = no ]; then
  echo '    <meta name="robots" content="noindex, nofollow" />' >> "$head"
fi
out=$(mktemp)
awk -v f="$head" '
  /<!-- atlas:seo -->/ { print; while ((getline l < f) > 0) print l; skip = 1; next }
  /<!-- \/atlas:seo -->/ { skip = 0 }
  !skip { print }
' "$HTML/index.html" > "$out"
cat "$out" > "$HTML/index.html"
rm -f "$head" "$out"

if [ "$index" = yes ]; then
  cat > "$HTML/robots.txt" <<EOF
User-agent: *
Disallow: /api/

Sitemap: $url/sitemap.xml
EOF
  {
    echo '<?xml version="1.0" encoding="UTF-8"?>'
    echo '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    echo "  <url><loc>$url/</loc></url>"
    # Pages the deployer added in ./site (docs/privacy.md), not the shipped examples.
    for f in "$HTML"/site/*.html; do
      [ -f "$f" ] || continue
      case "$f" in *.example.html) continue ;; esac
      echo "  <url><loc>$url/site/$(basename "$f")</loc></url>"
    done
    echo '</urlset>'
  } > "$HTML/sitemap.xml"
  noindex=
  echo "$ME: search engines may index $url/"
else
  printf 'User-agent: *\nDisallow: /\n' > "$HTML/robots.txt"
  rm -f "$HTML/sitemap.xml"
  noindex='add_header X-Robots-Tag "noindex, nofollow" always;'
  if [ -n "$url" ]; then why="ATLAS_INDEXING=off"; else why="set ATLAS_PUBLIC_URL to allow it"; fi
  echo "$ME: asking search engines not to index this instance ($why)"
fi

# Included by every location of nginx.conf that sends its own headers.
if [ -w "$SNIPPET_DIR" ]; then
  printf '%s\n' "$noindex" > "$SNIPPET_DIR/robots.conf"
fi
