"""Where the data quality shapes come from.

By default the worker validates against the SHACL shapes ERA publishes in its
repository (the named graph `quality.shapes_graph`, mirrored like the
vocabularies). A deployer can instead pin a tag of ERA's ontology repository on
GitLab (`quality.shapes_tag`, e.g. `v3.3.4`): the first of `quality.shapes_files`
that exists at that tag is downloaded once, cached on disk under
`{ATLAS_SHAPES_CACHE}/{repo}/{tag}/` and loaded into its own graph of the mirror
(`urn:atlas:shapes:{tag}`). Changing the tag changes the shapes' fingerprint, so
every graph is validated again, from the mirror, without downloading any data.
"""
from __future__ import annotations

import json
import logging
import re
import tempfile
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pyoxigraph as ox

from . import mirror
from .config import Quality, Settings

log = logging.getLogger(__name__)

UA = {"User-Agent": "rinf-atlas/0.1 (+https://github.com/Matdata-eu)"}
OWL_VERSION = "http://www.w3.org/2002/07/owl#versionInfo"
DCT_MODIFIED = "http://purl.org/dc/terms/modified"
SH_NODE_SHAPE = "http://www.w3.org/ns/shacl#NodeShape"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"


class ShapesError(RuntimeError):
    pass


@dataclass
class Source:
    """The shapes a dataset is validated with, as shown in the UI and written into the reports."""
    kind: str                   # "graph" (ERA's mirrored shapes graph) or "tag" (a GitLab tag)
    graph: str                  # the mirror graph holding them
    label: str                  # e.g. "era-ontology v3.3.4 (era-shacl/ERA-RINF-shapes.ttl)"
    url: str | None = None      # where they came from (a file at a tag, or the graph IRI)
    tag: str | None = None
    repo: str | None = None
    file: str | None = None
    version: str | None = None  # owl:versionInfo found in the shapes
    modified: str | None = None  # latest dct:modified found in the shapes

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def _client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(120, connect=30), headers=UA, follow_redirects=True)


def _get(url: str) -> httpx.Response:
    """GET with the worker's client; a seam for the tests (no network there)."""
    with _client() as c:
        return c.get(url)


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_") or "x"


def tag_graph(tag: str) -> str:
    return f"urn:atlas:shapes:{_safe(tag)}"


def raw_url(q: Quality, tag: str, path: str) -> str:
    return f"{q.gitlab_url}/{q.shapes_repo}/-/raw/{urllib.parse.quote(tag, safe='')}/{path}"


def tree_url(q: Quality, tag: str, path: str = "") -> str:
    return f"{q.gitlab_url}/{q.shapes_repo}/-/blob/{urllib.parse.quote(tag, safe='')}/{path}"


def list_tags(q: Quality, limit: int = 500) -> list[tuple[str, str]]:
    """(tag, commit date) of the ontology repository, newest first, from GitLab's tags API."""
    project = urllib.parse.quote(q.shapes_repo, safe="")
    out: list[tuple[str, str]] = []
    page = 1
    while len(out) < limit:
        r = _get(f"{q.gitlab_url}/api/v4/projects/{project}/repository/tags?per_page=100&page={page}")
        if r.status_code == 404 and page == 1 and not project.isdigit():
            # Some proxies decode the %2F of an encoded project path; look the numeric id up instead.
            project = _project_id(q)
            continue
        r.raise_for_status()
        rows = r.json()
        out += [(t["name"], (t.get("commit") or {}).get("committed_date", "")[:10]) for t in rows]
        nxt = r.headers.get("x-next-page")
        if not rows or not nxt:
            break
        page = int(nxt)
    return out[:limit]


def _project_id(q: Quality) -> str:
    name = q.shapes_repo.rstrip("/").rsplit("/", 1)[-1]
    r = _get(f"{q.gitlab_url}/api/v4/projects?search={urllib.parse.quote(name)}&simple=true&per_page=100")
    r.raise_for_status()
    for p in r.json():
        if p.get("path_with_namespace") == q.shapes_repo:
            return str(p["id"])
    raise ShapesError(f"GitLab project {q.shapes_repo} not found on {q.gitlab_url}")


def fetch_tag(cfg: Settings, q: Quality, tag: str) -> tuple[Path, dict]:
    """The shapes file of a tag, from the cache or downloaded into it. Returns (path, meta)."""
    d = cfg.shapes_cache / _safe(q.shapes_repo) / _safe(tag)
    ttl, meta_path = d / "shapes.ttl", d / "meta.json"
    if ttl.exists() and ttl.stat().st_size:
        try:
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        except ValueError:
            meta = {}
        return ttl, meta
    tried = []
    for path in q.shapes_files:
        url = raw_url(q, tag, path)
        try:
            r = _get(url)
        except httpx.HTTPError as e:
            raise ShapesError(f"cannot download {url}: {e}") from e
        if r.status_code == 404:
            tried.append(path)
            continue
        if r.status_code != 200:
            raise ShapesError(f"{url}: HTTP {r.status_code}")
        try:  # never cache something that is not Turtle (an HTML error page, say)
            for _ in ox.parse(r.content, format=ox.RdfFormat.TURTLE):  # lazy: parse it all
                pass
        except (SyntaxError, ValueError) as e:
            raise ShapesError(f"{url} is not Turtle: {str(e)[:200]}") from e
        d.mkdir(parents=True, exist_ok=True)
        tmp = ttl.with_suffix(".part")
        tmp.write_bytes(r.content)
        tmp.replace(ttl)
        meta = {"tag": tag, "repo": q.shapes_repo, "file": path, "url": url,
                "downloaded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
        meta_path.write_text(json.dumps(meta, indent=2))
        log.info("downloaded shapes %s (%d bytes) into %s", url, len(r.content), d)
        return ttl, meta
    raise ShapesError(f"tag {tag!r} of {q.shapes_repo} has none of {', '.join(tried)} "
                      f"(see `atlas shapes-tags` for the tags)")


def _describe(store: ox.Store, graph: str) -> tuple[str | None, str | None]:
    g = ox.NamedNode(graph)
    versions = sorted({q.object.value for q in store.quads_for_pattern(None, ox.NamedNode(OWL_VERSION), None, g)})
    modified = sorted({q.object.value for q in store.quads_for_pattern(None, ox.NamedNode(DCT_MODIFIED), None, g)})
    return (", ".join(versions) or None), (modified[-1] if modified else None)


def _has_shapes(store: ox.Store, graph: str) -> bool:
    return any(store.quads_for_pattern(None, ox.NamedNode(RDF_TYPE), ox.NamedNode(SH_NODE_SHAPE), ox.NamedNode(graph)))


def resolve(cfg: Settings, q: Quality, store: ox.Store) -> Source | None:
    """The shapes to validate with, loaded in the mirror; None when the mirrored graph is not there yet.

    Raises ShapesError when a configured tag cannot be fetched."""
    if not q.shapes_tag:
        g = q.shapes_graph
        if not store.contains_named_graph(ox.NamedNode(g)):
            return None
        version, modified = _describe(store, g)
        label = "ERA's shapes graph" + (f" (version {version})" if version else "")
        return Source("graph", g, label, url=g, version=version, modified=modified)
    tag = q.shapes_tag
    g = tag_graph(tag)
    path, meta = fetch_tag(cfg, q, tag)
    if not _has_shapes(store, g):
        # Turtle -> N-Triples, so the mirror's loader can give blank nodes stable IRIs.
        triples = ox.parse(path=str(path), format=ox.RdfFormat.TURTLE)
        cfg.tmp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cfg.tmp_dir) as d:
            nt = Path(d) / "shapes.nt"
            ox.serialize(triples, output=str(nt), format=ox.RdfFormat.N_TRIPLES)
            mirror.replace_graph(store, g, nt)
        if not _has_shapes(store, g):
            raise ShapesError(f"{meta.get('url') or path} has no sh:NodeShape")
    version, modified = _describe(store, g)
    file = meta.get("file")
    label = f"era-ontology {tag}" + (f" ({file})" if file else "")
    return Source("tag", g, label, url=meta.get("url") or raw_url(q, tag, file or ""), tag=tag,
                  repo=q.shapes_repo, file=file, version=version, modified=modified)
