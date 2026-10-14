"""Shapes source for data quality: a pinned GitLab tag (downloads mocked) and per-environment settings."""
from types import SimpleNamespace

import httpx
import pyoxigraph as ox
import pytest
from atlas import config, era_shapes, quality, shacl

SHAPES = """
@prefix sh: <http://www.w3.org/ns/shacl#> . @prefix ex: <http://ex/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> . @prefix dct: <http://purl.org/dc/terms/> .
ex:shapes owl:versionInfo "9.9.9" ; dct:modified "2026-01-01", "2026-05-18" .
ex:PointShape a sh:NodeShape ; sh:targetClass ex:Point ;
  sh:property [ sh:path ex:name ; sh:minCount 1 ; sh:message "A point needs a name." ] .
"""
DATA = "@prefix ex: <http://ex/> . ex:a a ex:Point . ex:b a ex:Point ; ex:name \"B\" ."
G = "http://ex/graph/data"


def _q(**kw):
    return config.Quality(shapes_tag=kw.pop("tag", "v9.9.9"), **kw)


@pytest.fixture
def web(monkeypatch):
    """A fake gitlab.com: {url suffix: (status, body)}; records the URLs asked for."""
    routes: dict[str, tuple[int, object]] = {}
    calls: list[str] = []

    def get(url):
        calls.append(url)
        for suffix, (status, body) in routes.items():
            if url.endswith(suffix):
                if isinstance(body, (list, dict)):
                    return httpx.Response(status, json=body, headers=body_headers.get(suffix, {}),
                                          request=httpx.Request("GET", url))
                return httpx.Response(status, content=body, request=httpx.Request("GET", url))
        return httpx.Response(404, content=b"not found", request=httpx.Request("GET", url))

    body_headers: dict[str, dict] = {}
    monkeypatch.setattr(era_shapes, "_get", get)
    return SimpleNamespace(routes=routes, calls=calls, headers=body_headers)


def _cfg(tmp_path):
    return SimpleNamespace(shapes_cache=tmp_path / "cache", tmp_dir=tmp_path / "tmp")


def test_tag_download_falls_back_and_is_cached(tmp_path, web):
    q = _q()
    web.routes["/-/raw/v9.9.9/era-shapes.ttl"] = (200, SHAPES.encode())  # the RINF file is missing at this tag
    path, meta = era_shapes.fetch_tag(_cfg(tmp_path), q, "v9.9.9")
    assert meta["file"] == "era-shapes.ttl" and path.read_text().strip().startswith("@prefix")
    assert [c.rsplit("/-/raw/", 1)[1] for c in web.calls] == ["v9.9.9/era-shacl/ERA-RINF-shapes.ttl",
                                                              "v9.9.9/era-shapes.ttl"]
    web.calls.clear()
    again, meta2 = era_shapes.fetch_tag(_cfg(tmp_path), q, "v9.9.9")
    assert again == path and meta2 == meta and web.calls == []  # served from the cache


def test_unknown_tag_and_non_turtle_are_errors(tmp_path, web):
    with pytest.raises(era_shapes.ShapesError, match="has none of"):
        era_shapes.fetch_tag(_cfg(tmp_path), _q(), "nope")
    web.routes["/-/raw/v1/era-shacl/ERA-RINF-shapes.ttl"] = (200, b"<html>sign in</html>")
    with pytest.raises(era_shapes.ShapesError, match="not Turtle"):
        era_shapes.fetch_tag(_cfg(tmp_path), _q(), "v1")
    assert not list((tmp_path / "cache").rglob("shapes.ttl"))


def test_resolve_tag_loads_its_own_graph_and_validates(tmp_path, web):
    web.routes["/-/raw/v9.9.9/era-shacl/ERA-RINF-shapes.ttl"] = (200, SHAPES.encode())
    st = ox.Store()
    st.load(DATA.encode(), format=ox.RdfFormat.TURTLE, to_graph=ox.NamedNode(G))
    src = era_shapes.resolve(_cfg(tmp_path), _q(), st)
    assert src.kind == "tag" and src.graph == "urn:atlas:shapes:v9.9.9" and src.tag == "v9.9.9"
    assert src.version == "9.9.9" and src.modified == "2026-05-18"
    assert src.label == "era-ontology v9.9.9 (era-shacl/ERA-RINF-shapes.ttl)"
    run = shacl.validate_graph(st, G, src.graph, [])
    assert [r.focus for r in run.results] == ["<http://ex/a>"]
    # the report says which shapes it used, as triples
    ttl = shacl.report_turtle(run, report_iri="urn:r", created="2026-10-14T00:00:00Z", meta=quality.report_meta(src))
    rep = ox.Store()
    rep.load(ttl.encode(), format=ox.RdfFormat.TURTLE)
    tag = list(rep.quads_for_pattern(None, ox.NamedNode(quality.ATLAS + "shapesTag"), None))
    assert [q.object.value for q in tag] == ["v9.9.9"]
    assert quality.shapes_turtle(st, src.graph, src).startswith(b"# SHACL shapes used by RINF Atlas")


def test_tag_and_graph_fingerprints_differ(tmp_path, web):
    web.routes["/-/raw/v9.9.9/era-shacl/ERA-RINF-shapes.ttl"] = (200, SHAPES.encode())
    web.routes["/-/raw/v9.9.8/era-shacl/ERA-RINF-shapes.ttl"] = (200, SHAPES.replace("9.9.9", "9.9.8").encode())
    st = ox.Store()
    a = era_shapes.resolve(_cfg(tmp_path), _q(tag="v9.9.9"), st)
    b = era_shapes.resolve(_cfg(tmp_path), _q(tag="v9.9.8"), st)
    assert quality.shapes_fingerprint(st, a.graph) != quality.shapes_fingerprint(st, b.graph)
    assert era_shapes.resolve(_cfg(tmp_path), _q(tag=""), st) is None  # graph source, not mirrored


def test_list_tags_pages_and_falls_back_to_project_id(web):
    q = _q()
    web.routes["/api/v4/projects?search=era-ontology&simple=true&per_page=100"] = (
        200, [{"id": 1, "path_with_namespace": "other/era-ontology"},
              {"id": 42, "path_with_namespace": q.shapes_repo}])
    web.routes["/projects/42/repository/tags?per_page=100&page=1"] = (
        200, [{"name": "v3.3.4", "commit": {"committed_date": "2026-08-10T10:00:00Z"}}])
    web.headers["/projects/42/repository/tags?per_page=100&page=1"] = {"x-next-page": "2"}
    web.routes["/projects/42/repository/tags?per_page=100&page=2"] = (200, [{"name": "v3.3.3", "commit": {}}])
    assert era_shapes.list_tags(q) == [("v3.3.4", "2026-08-10"), ("v3.3.3", "")]


def test_per_environment_quality_settings(tmp_path, monkeypatch):
    y = tmp_path / "env.yaml"
    y.write_text("""
environments:
  prod: {sparql: "http://p", quality: {shapes_tag: v3.3.4, max_triples: 20000000}}
  uat: {sparql: "http://u"}
quality: {max_triples: 1000, exclude_shapes: []}
""")
    monkeypatch.setenv("ATLAS_CONFIG", str(y))
    for k in ("ATLAS_QUALITY_MAX_TRIPLES", "ATLAS_QUALITY_SHAPES_TAG", "ATLAS_ENVS", "ATLAS_QUALITY"):
        monkeypatch.delenv(k, raising=False)

    def load():
        config.settings.cache_clear()
        return config.settings()

    try:
        s = load()
        assert (s.env("prod").quality.shapes_tag, s.env("prod").quality.max_triples) == ("v3.3.4", 20_000_000)
        assert (s.env("uat").quality.shapes_tag, s.env("uat").quality.max_triples) == ("", 1000)
        assert s.env("uat").quality.exclude_shapes == []
        monkeypatch.setenv("ATLAS_QUALITY_MAX_TRIPLES", "5e6")
        monkeypatch.setenv("ATLAS_QUALITY_SHAPES_TAG", "uat=v4.0.0")
        s = load()
        assert s.env("prod").quality.max_triples == s.env("uat").quality.max_triples == 5_000_000
        assert (s.env("prod").quality.shapes_tag, s.env("uat").quality.shapes_tag) == ("v3.3.4", "v4.0.0")
    finally:
        config.settings.cache_clear()


def test_too_large_note():
    assert quality.too_large_note(18_400_000, 15_000_000) == \
        "not validated: too large (18,400,000 triples > limit 15,000,000)"
