"""Extra map layers chosen by whoever deploys this instance (docs/layers.md).

config/layers.yaml lists raster tiles (XYZ), vector tiles (MVT) and WMS layers.
Values may reference environment variables as ${ATLAS_LAYER_...}, for API keys
and tokens. A layer that uses one is proxied by default: the browser asks
/api/layers/{id}/{z}/{x}/{y} and only this service sees the key. A layer with
`proxy: false` is handed to the browser as is, keys included.

  GET /api/layers                       the layers, as the browser may see them
  GET /api/layers/{id}/{z}/{x}/{y}      one tile of a proxied layer

The file is read again when it changes, so editing it needs no restart.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
import yaml
from fastapi import APIRouter, HTTPException, Response

log = logging.getLogger("atlas.layers")
router = APIRouter()

LAYERS_FILE = Path(os.environ.get("ATLAS_LAYERS", "/app/config/layers.yaml"))
# Only these variables may be referenced, so a typo cannot put DATABASE_URL in a URL.
ENV_PREFIX = "ATLAS_LAYER_"
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
TYPES = ("raster", "vector", "wms")
POSITIONS = ("below", "above")
WMS_DEFAULTS = {"version": "1.3.0", "styles": "", "format": "image/png", "transparent": "true"}
# Web Mercator half extent, for the bounding box of a tile.
HALF = 20037508.342789244
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
# httpx has already decoded any content-encoding, so that header is not passed on.
PASS_HEADERS = ("content-type", "etag", "last-modified")

_cache: dict = {"mtime": None, "layers": []}
_client: httpx.AsyncClient | None = None


class LayerError(ValueError):
    pass


def _subst(value: str, env: dict[str, str]) -> tuple[str, bool]:
    """Fill ${ATLAS_LAYER_*} placeholders; also says whether there were any."""
    used = False

    def repl(m: re.Match) -> str:
        nonlocal used
        name = m.group(1)
        if not name.startswith(ENV_PREFIX):
            raise LayerError(f"${{{name}}}: only variables starting with {ENV_PREFIX} can be used")
        if not env.get(name):
            raise LayerError(f"${{{name}}} is not set")
        used = True
        return env[name]

    return PLACEHOLDER.sub(repl, value), used


def _wms_url(base: str, wms: dict, size: int, bbox: str) -> str:
    """A GetMap request in EPSG:3857 for one tile; `bbox` is literal or MapLibre's {bbox-epsg-3857}."""
    p = {k: str(v) for k, v in {**WMS_DEFAULTS, **wms}.items() if k not in ("layers", "params")}
    version = p.pop("version")
    params = {"service": "WMS", "request": "GetMap", "version": version, "layers": str(wms["layers"]),
              **p, ("srs" if version.startswith("1.1") else "crs"): "EPSG:3857", "width": size, "height": size}
    params.update({str(k): str(v) for k, v in (wms.get("params") or {}).items()})
    parts = urlsplit(base)
    query = "&".join(x for x in (parts.query, urlencode(params)) if x)
    # Keep the placeholder's braces readable for MapLibre (it substitutes them itself).
    return urlunsplit(parts._replace(query=f"{query}&bbox={bbox}"))


def parse(raw: dict | None, env: dict[str, str] | None = None) -> list[dict]:
    """The usable layers of a layers.yaml; a broken entry is logged and left out."""
    env = dict(os.environ) if env is None else env
    out, seen = [], set()
    for i, item in enumerate((raw or {}).get("layers") or []):
        try:
            out.append(_layer(item, env, seen))
        except (LayerError, KeyError, TypeError, ValueError) as e:
            ident = item.get("id") if isinstance(item, dict) else None
            log.warning("layers.yaml: layer %s left out: %s", ident or f"#{i + 1}", e)
    return out


def _layer(item: dict, env: dict[str, str], seen: set[str]) -> dict:
    if not isinstance(item, dict):
        raise LayerError("not a mapping")
    lid = str(item.get("id", ""))
    if not ID_RE.match(lid):
        raise LayerError("id must be lowercase letters, digits and dashes")
    if lid in seen:
        raise LayerError("duplicate id")
    kind = item.get("type", "raster")
    if kind not in TYPES:
        raise LayerError(f"type must be one of {', '.join(TYPES)}")
    if not item.get("url"):
        raise LayerError("url is missing")
    url, secret = _subst(str(item["url"]), env)
    if urlsplit(url).scheme not in ("http", "https"):
        raise LayerError("url must be http(s)")
    headers = {}
    for k, v in (item.get("headers") or {}).items():
        headers[str(k)], used = _subst(str(v), env)
        secret = secret or used
    wms = dict(item.get("wms") or {})
    if kind == "wms":
        if not wms.get("layers"):
            raise LayerError("wms.layers is missing")
        for k, v in list(wms.items()):
            if isinstance(v, str):
                wms[k], used = _subst(v, env)
                secret = secret or used
    # Proxied by default when a secret is involved; headers can only be sent by the proxy.
    proxy = bool(item.get("proxy", secret or bool(headers)))
    if headers and not proxy:
        raise LayerError("headers need proxy: true (a browser cannot send them for map tiles)")
    if secret and not proxy:
        log.warning("layers.yaml: layer %s sends its key to every browser (proxy: false)", lid)
    position = item.get("position", "below" if kind != "vector" else "above")
    if position not in POSITIONS:
        raise LayerError("position must be below or above")
    if kind == "vector" and not item.get("style"):
        raise LayerError("vector layers need `style`: a list of MapLibre layers")
    size = int(item.get("tile_size", 256))
    seen.add(lid)
    return {
        "id": lid, "label": str(item.get("label") or lid), "description": str(item.get("description") or ""),
        "type": kind, "url": url, "headers": headers, "wms": wms, "proxy": proxy, "tile_size": size,
        "subdomains": [str(s) for s in item.get("subdomains") or []],
        "min_zoom": int(item.get("min_zoom", 0)), "max_zoom": int(item.get("max_zoom", 22)),
        "opacity": float(item.get("opacity", 1)), "attribution": str(item.get("attribution") or ""),
        "position": position, "style": list(item.get("style") or []),
        "cache_seconds": int(item.get("cache_seconds", 86400)),
    }


def load() -> list[dict]:
    """The configured layers, re-read when the file changes (none when there is no file)."""
    try:
        mtime = LAYERS_FILE.stat().st_mtime
    except OSError:
        mtime = None
    if mtime != _cache["mtime"]:
        layers = []
        if mtime is not None:
            try:
                layers = parse(yaml.safe_load(LAYERS_FILE.read_text(encoding="utf-8")))
            except (OSError, yaml.YAMLError) as e:
                log.warning("%s could not be read: %s", LAYERS_FILE, e)
        _cache.update(mtime=mtime, layers=layers)
    return _cache["layers"]


def _direct_tiles(lay: dict) -> list[str]:
    if lay["type"] == "wms":
        return [_wms_url(lay["url"], lay["wms"], lay["tile_size"], "{bbox-epsg-3857}")]
    if lay["subdomains"] and "{s}" in lay["url"]:
        return [lay["url"].replace("{s}", s) for s in lay["subdomains"]]
    return [lay["url"]]


def public(lay: dict) -> dict:
    """What the browser gets: never the headers, and the URL only when not proxied."""
    tiles = [f"/api/layers/{lay['id']}/{{z}}/{{x}}/{{y}}"] if lay["proxy"] else _direct_tiles(lay)
    out = {k: lay[k] for k in ("id", "label", "description", "type", "tile_size", "min_zoom", "max_zoom",
                               "opacity", "attribution", "position")}
    out.update(tiles=tiles, proxied=lay["proxy"])
    if lay["type"] == "vector":
        out["style"] = lay["style"]
    return out


def upstream_url(lay: dict, z: int, x: int, y: int) -> str:
    """The provider URL of one tile (keys filled in)."""
    if lay["type"] == "wms":
        size = HALF * 2 / (1 << z)
        minx, maxy = -HALF + x * size, HALF - y * size
        bbox = ",".join(f"{v:.6f}" for v in (minx, maxy - size, minx + size, maxy))
        return _wms_url(lay["url"], lay["wms"], lay["tile_size"], bbox)
    url = lay["url"].replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
    url = url.replace("{-y}", str((1 << z) - 1 - y))
    if lay["subdomains"]:
        url = url.replace("{s}", lay["subdomains"][(x + y) % len(lay["subdomains"])])
    return url


@router.get("/api/layers")
def layers():
    return [public(lay) for lay in load()]


@router.get("/api/layers/{lid}/{z}/{x}/{y}")
async def tile(lid: str, z: int, x: int, y: int):
    lay = next((lay for lay in load() if lay["id"] == lid and lay["proxy"]), None)
    if lay is None:
        raise HTTPException(404, "no such proxied layer")
    if not (0 <= z <= 30 and 0 <= x < (1 << z) and 0 <= y < (1 << z)):
        raise HTTPException(400, "tile out of range")
    if z < lay["min_zoom"] or z > lay["max_zoom"]:
        return Response(status_code=204)
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                    headers={"user-agent": "rinf-atlas (+https://github.com/Matdata-eu/rinf-atlas)"})
    try:
        r = await _client.get(upstream_url(lay, z, x, y), headers=lay["headers"])
    except httpx.HTTPError as e:
        log.warning("layer %s: %s", lid, type(e).__name__)
        raise HTTPException(502, "the layer's server did not answer") from None
    if r.status_code in (204, 404):
        return Response(status_code=204)  # no tile here: an empty tile, not a map error
    if r.status_code >= 400:
        # The provider's message may echo the key; keep it in the log only, without the URL.
        log.warning("layer %s: upstream answered %s", lid, r.status_code)
        raise HTTPException(502, f"the layer's server answered {r.status_code}")
    ctype = r.headers.get("content-type", "")
    if lay["type"] == "wms" and "xml" in ctype:
        log.warning("layer %s: WMS exception %s", lid, r.text[:300])
        raise HTTPException(502, "the WMS answered with an error")
    hdrs = {k: r.headers[k] for k in PASS_HEADERS if k in r.headers}
    hdrs["cache-control"] = f"public, max-age={lay['cache_seconds']}"
    return Response(r.content, status_code=200, headers=hdrs)

