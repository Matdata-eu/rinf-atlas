"""Deployer map layers, no database or network: `python -m pytest api/tests`."""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.layers import parse, public, upstream_url  # noqa: E402

ENV = {"ATLAS_LAYER_KEY": "s3cret", "DATABASE_URL": "postgresql://x"}
EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "layers.example.yaml"


def one(item, env=ENV):
    layers = parse({"layers": [item]}, env)
    return layers[0] if layers else None


def test_key_in_url_is_proxied_and_hidden():
    lay = one({"id": "ortho", "url": "https://t.example/{z}/{x}/{y}.png?key=${ATLAS_LAYER_KEY}"})
    pub = public(lay)
    assert pub["proxied"] and pub["tiles"] == ["/api/layers/ortho/{z}/{x}/{y}"]
    assert "s3cret" not in repr(pub)
    assert upstream_url(lay, 3, 4, 2) == "https://t.example/3/4/2.png?key=s3cret"


def test_public_layer_goes_direct_with_subdomains():
    lay = one({"id": "topo", "url": "https://{s}.t.example/{z}/{x}/{y}.png", "subdomains": ["a", "b"]})
    assert public(lay)["tiles"] == ["https://a.t.example/{z}/{x}/{y}.png", "https://b.t.example/{z}/{x}/{y}.png"]


def test_header_token_needs_and_implies_proxy():
    lay = one({"id": "h", "url": "https://t.example/{z}/{x}/{y}", "headers": {"Authorization": "Bearer ${ATLAS_LAYER_KEY}"}})
    assert lay["proxy"] and lay["headers"] == {"Authorization": "Bearer s3cret"}
    assert "s3cret" not in repr(public(lay))
    assert one({"id": "h", "url": "https://t.example/{z}/{x}/{y}", "proxy": False, "headers": {"X": "y"}}) is None


def test_only_prefixed_and_set_variables():
    assert one({"id": "a", "url": "https://t.example/{z}/{x}/{y}?k=${DATABASE_URL}"}) is None
    assert one({"id": "a", "url": "https://t.example/{z}/{x}/{y}?k=${ATLAS_LAYER_MISSING}"}) is None


def test_client_key_when_proxy_off():
    lay = one({"id": "c", "url": "https://t.example/{z}/{x}/{y}?k=${ATLAS_LAYER_KEY}", "proxy": False})
    assert public(lay)["tiles"] == ["https://t.example/{z}/{x}/{y}?k=s3cret"]


def test_wms_bbox_and_tms():
    lay = one({"id": "w", "type": "wms", "url": "https://w.example/wms?map=x&token=${ATLAS_LAYER_KEY}",
               "wms": {"layers": "rail"}})
    u = upstream_url(lay, 0, 0, 0)
    assert "token=s3cret" in u and "crs=EPSG%3A3857" in u and "layers=rail" in u and "request=GetMap" in u
    assert u.endswith("bbox=-20037508.342789,-20037508.342789,20037508.342789,20037508.342789")
    pub = one({"id": "w", "type": "wms", "url": "https://w.example/wms", "wms": {"layers": "rail", "version": "1.1.1"}})
    tiles = public(pub)["tiles"][0]
    assert "srs=EPSG%3A3857" in tiles and tiles.endswith("bbox={bbox-epsg-3857}")
    tms = one({"id": "t", "url": "https://t.example/{z}/{x}/{-y}.png"})
    assert upstream_url(tms, 2, 1, 0) == "https://t.example/2/1/3.png"


def test_bad_entries_are_left_out_not_fatal():
    raw = {"layers": [{"id": "Bad id", "url": "https://x"}, {"id": "v", "type": "vector", "url": "https://x/{z}/{x}/{y}.pbf"},
                      {"id": "ok", "url": "https://x/{z}/{x}/{y}.png"}, {"id": "ok", "url": "https://y/{z}/{x}/{y}.png"},
                      {"id": "f", "url": "file:///etc/passwd"}]}
    assert [lay["id"] for lay in parse(raw, ENV)] == ["ok"]


def test_example_file_parses():
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    env = {f"ATLAS_LAYER_{k}": "x" for k in ("BASEMAP_KEY", "WMS_BASIC", "VECTOR_TOKEN", "MAPTILER_KEY")}
    assert len(parse(raw, env)) == len(raw["layers"])
