"""Local RDF mirror per environment (embedded Oxigraph).

Holds the vocabulary graphs and every provider graph, so facet extraction and
re-indexing after a shape change never query ERA.
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
from pathlib import Path

import pyoxigraph as ox

log = logging.getLogger(__name__)

_stores: dict[str, ox.Store] = {}
_lock = threading.Lock()   # export threads open stores too; RocksDB allows one handle per path


def store(root: Path, env: str) -> ox.Store:
    with _lock:
        if env not in _stores:
            path = root / env
            path.mkdir(parents=True, exist_ok=True)
            _stores[env] = ox.Store(str(path))
        return _stores[env]


def close_all() -> None:
    _stores.clear()


def has_graph(s: ox.Store, graph: str) -> bool:
    return s.contains_named_graph(ox.NamedNode(graph))


def replace_graph(s: ox.Store, graph: str, ntriples: Path) -> None:
    g = ox.NamedNode(graph)
    if s.contains_named_graph(g):
        s.remove_graph(g)
    src = prepare(ntriples, graph, drop_long=False)
    try:
        try:
            s.bulk_load(path=str(src), format=ox.RdfFormat.N_TRIPLES, to_graph=g, lenient=True)
        except MemoryError:
            # A literal larger than Oxigraph's 16 MB parser buffer (seen: digital
            # schematic overviews as huge WKT strings). Drop such triples and retry.
            s.remove_graph(g)
            if src != ntriples:
                src.unlink(missing_ok=True)
            src = prepare(ntriples, graph, drop_long=True)
            s.bulk_load(path=str(src), format=ox.RdfFormat.N_TRIPLES, to_graph=g, lenient=True)
    finally:
        if src != ntriples:
            src.unlink(missing_ok=True)
    s.flush()


MAX_LINE = 1 << 20  # triples longer than 1 MB are dropped when the parser chokes


def prepare(ntriples: Path, graph: str, drop_long: bool) -> Path:
    """Rewrite blank nodes to IRIs (and optionally drop oversized triples).

    The parallel bulk loader does not keep blank node identity across chunks,
    which breaks RDF lists (owl:unionOf domains in the ontology, ...). Blank
    nodes are rare in RINF data, so files without them are loaded as they are.
    """
    if not drop_long:
        with ntriples.open("rb") as f:
            if not any(b"_:" in chunk for chunk in iter(lambda: f.read(1 << 22), b"")):
                return ntriples
    prefix = "<urn:atlas:bnode:" + hashlib.sha1(graph.encode()).hexdigest()[:10] + ":"
    out = ntriples.with_suffix(".prepared.nt")
    dropped = 0
    with ntriples.open("r", encoding="utf-8", errors="surrogateescape") as fi, \
            out.open("w", encoding="utf-8", errors="surrogateescape") as fo:
        for line in fi:
            if drop_long and len(line) > MAX_LINE:
                dropped += 1
                continue
            if "_:" in line:
                if line.startswith("_:"):
                    sp = line.find(" ")
                    line = prefix + line[2:sp] + ">" + line[sp:]
                m = _OBJ_BNODE.search(line)
                if m:
                    line = line[:m.start(1)] + prefix + m.group(2) + ">" + line[m.end(1):]
            fo.write(line)
    if dropped:
        log.warning("%s: dropped %d triples longer than %d bytes", graph, dropped, MAX_LINE)
    return out


skolemize = lambda ntriples, graph: prepare(ntriples, graph, drop_long=False)  # noqa: E731


_OBJ_BNODE = re.compile(r"\s(_:([A-Za-z0-9_\-.]+))\s*\.\s*$")


def remove_graph(s: ox.Store, graph: str) -> None:
    g = ox.NamedNode(graph)
    if s.contains_named_graph(g):
        s.remove_graph(g)
        s.flush()


def select(s: ox.Store, query: str, union: bool = True):
    """Run a SELECT and yield dicts of variable -> term (None when unbound)."""
    res = s.query(query, use_default_graph_as_union=union)
    names = [v.value for v in res.variables]
    for sol in res:
        yield {n: sol[n] for n in names}
