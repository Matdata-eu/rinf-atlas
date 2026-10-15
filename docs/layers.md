# Extra map layers

Whoever runs an instance can add their own layers to the map's **Map layers**
menu, next to OpenRailwayMap: aerial photos, a topographic map, the
infrastructure manager's track plans from a WMS, asset data as vector tiles.
Layers that need an API key or a login work too, without the key reaching
the people who open the map.

## Adding layers

Copy the example and edit it:

```sh
cp config/layers.example.yaml config/layers.yaml
```

`config/layers.yaml` is git-ignored, so `git pull` does not touch it. The
`api` container reads it from the mounted `config/` folder and reads it again
whenever it changes, so new layers appear on the next page load without a
restart. A layer with a mistake is left out with a warning in
`docker compose logs api`; the rest still load.

Three types are supported:

| `type` | `url` | Notes |
|---|---|---|
| `raster` (default) | XYZ tiles with `{z}`, `{x}`, `{y}`; `{-y}` for TMS; `{s}` with `subdomains` | PNG / JPEG / WebP |
| `wms` | the WMS service URL | `wms.layers` required; requested as 256 px tiles in EPSG:3857 (the WMS must offer it) |
| `vector` | Mapbox Vector Tiles with `{z}`, `{x}`, `{y}` | `style` lists MapLibre style layers, each with its `source-layer` |

Each layer has an `id` (lowercase, dashes; it also appears in shared links as
`lyr=`), a `label` and an optional one-line `description` for the menu, and an
`attribution` shown on the map while the layer is on. `opacity`,
`min_zoom` / `max_zoom`, `tile_size` and `position` (`below` the RINF data,
the default for images, or `above` it, the default for vector layers) are
optional. [config/layers.example.yaml](../config/layers.example.yaml)
documents every key with an example of each type.

Every layer starts switched off; users tick it in the menu.

## Keys and private layers

Write a key as `${ATLAS_LAYER_SOMETHING}` anywhere in `url`, `headers` or the
`wms` block, and put the value in `.env`:

```yaml
  - id: aerial
    label: Aerial photos
    url: https://tiles.example.com/ortho/{z}/{x}/{y}.jpg?apikey=${ATLAS_LAYER_ORTHO_KEY}
```

```sh
# .env
ATLAS_LAYER_ORTHO_KEY=abc123
```

then `docker compose up -d api` to pass the new variable in. Only variables
whose names start with `ATLAS_LAYER_` can be used, so a typo cannot put the
database password in a tile URL. A layer whose variable is not set is left
out.

For services that want a token in a header (`Authorization: Bearer …`) or
HTTP basic authentication, use `headers`:

```yaml
    headers:
      Authorization: Basic ${ATLAS_LAYER_WMS_BASIC}   # printf 'user:password' | base64
```

### How the key stays on the server

A layer that uses a key or headers is **proxied** by default: the browser
asks the Atlas API for `/api/layers/{id}/{z}/{x}/{y}`, and the API fetches the
tile from the provider with the key and passes the image on. `/api/layers`,
which tells the browser which layers exist, never contains the key or the
provider URL of a proxied layer. Browsers may cache proxied tiles for
`cache_seconds` (default one day).

Things to know:

- **Anyone who can open your instance can view the layer.** The proxy hides
  the key, not the tiles. If the layer must stay internal, run the instance
  behind your own login or network restrictions, or leave such layers out.
- **Every tile goes through the `api` container**, which costs bandwidth and
  counts against the provider's quota on behalf of all your users. A caching
  proxy in front of the instance can reduce that.
- The provider sees requests from your server, not from users. Providers that
  only accept keys locked to a website address (an HTTP Referer restriction)
  refuse these requests.

### When the provider needs the key in the browser

Some providers (MapTiler, Mapbox, many commercial basemaps) issue keys meant
to be public and restricted to your domain. For those, set `proxy: false`:

```yaml
    url: https://api.maptiler.com/tiles/satellite-v2/{z}/{x}/{y}.jpg?key=${ATLAS_LAYER_MAPTILER_KEY}
    proxy: false
```

The key is then part of the tile URLs every browser loads, so anyone can read
it. Only do this with a key the provider restricts to your instance's address,
and set a usage limit at the provider. The API logs a warning for each such
layer. Headers cannot be used with `proxy: false`.

Layers without any key load directly from the provider (no proxy), unless you
set `proxy: true`.

## Licences

You are responsible for the licence and terms of use of the layers you add:
many providers require an attribution (set `attribution`) and limit how many
tiles you may request.
