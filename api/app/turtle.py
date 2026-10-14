"""Turtle export: the outgoing triples (one-level describe) of every result entity.

Triples come live from the environment's ERA SPARQL endpoint, like
/api/describe, in batches of IRIs. The worker's Oxigraph mirror is not used:
it is a RocksDB store owned by the worker process, and reading it from another
process while the worker loads graphs is undefined behaviour.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor

XSD = "http://www.w3.org/2001/XMLSchema#"
PREFIXES = {
    "era": "http://data.europa.eu/949/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": XSD,
    "owl": "http://www.w3.org/2002/07/owl#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dcterms": "http://purl.org/dc/terms/",
    "geo": "http://www.opengis.net/ont/geosparql#",
    "wgs": "http://www.w3.org/2003/01/geo/wgs84_pos#",
    "country": "http://publications.europa.eu/resource/authority/country/",
}
RDF_TYPE = PREFIXES["rdf"] + "type"
# Longest namespace first, so era:... never wins over a longer, more specific prefix.
_BY_LENGTH = sorted(PREFIXES.items(), key=lambda kv: -len(kv[1]))
_LOCAL = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_ESC_RE = re.compile(r'[\\"\n\r\t]')


def header(comment_lines: list[str]) -> str:
    out = [f"# {c}" for c in comment_lines]
    out += [f"@prefix {p}: <{ns}> ." for p, ns in PREFIXES.items()]
    return "\n".join(out) + "\n\n"


def iri(value: str) -> str:
    for p, ns in _BY_LENGTH:
        if value.startswith(ns) and _LOCAL.match(value[len(ns):]):
            return f"{p}:{value[len(ns):]}"
    return "<" + "".join(c if c not in '<>"{}|^`\\ ' and ord(c) > 0x20 else f"\\u{ord(c):04X}" for c in value) + ">"


def literal(value: str, datatype: str | None = None, lang: str | None = None) -> str:
    s = '"' + _ESC_RE.sub(lambda m: _ESCAPES[m.group()], value) + '"'
    if lang:
        return f"{s}@{lang}"
    if datatype and datatype != XSD + "string":
        return f"{s}^^{iri(datatype)}"
    return s


def term(b: dict) -> str:
    """A SPARQL JSON results binding as a Turtle term."""
    t = b["type"]
    if t == "uri":
        return iri(b["value"])
    if t == "bnode":
        return "_:" + re.sub(r"[^A-Za-z0-9_]", "_", b["value"])
    return literal(b["value"], b.get("datatype"), b.get("xml:lang"))


def describe_query(iris: list[str]) -> str:
    values = " ".join(f"<{i}>" for i in iris)
    return f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {values} }} ?s ?p ?o }}"


def serialize(iris: list[str], bindings: list[dict]) -> str:
    """Turtle blocks for the subjects, in the given order; rdf:type first."""
    by_s: dict[str, dict[str, list[str]]] = {i: {} for i in iris}
    for b in bindings:
        preds = by_s.setdefault(b["s"]["value"], {})
        objs = preds.setdefault(b["p"]["value"], [])
        o = term(b["o"])
        if o not in objs:
            objs.append(o)
    out = []
    for s, preds in by_s.items():
        if not preds:
            out.append(f"# {iri(s)}: no outgoing triples at the endpoint\n\n")
            continue
        order = sorted(preds, key=lambda p: (p != RDF_TYPE, p))
        lines = [f"    {'a' if p == RDF_TYPE else iri(p)} {', '.join(preds[p])}" for p in order]
        out.append(iri(s) + "\n" + " ;\n".join(lines) + " .\n\n")
    return "".join(out)


def stream(iris: list[str], fetch: Callable[[str], list[dict]], head: str,
           batch: int = 100, parallel: int = 3) -> Iterator[bytes]:
    """Yields the Turtle document batch by batch. fetch(query) returns SPARQL JSON
    bindings. A failing batch ends the document with an error comment (the HTTP
    status is already sent by then)."""
    yield head.encode("utf-8")
    chunks = [iris[i:i + batch] for i in range(0, len(iris), batch)]

    def run(chunk: list[str]) -> str:
        return serialize(chunk, fetch(describe_query(chunk)))

    with ThreadPoolExecutor(max_workers=parallel) as ex:
        futures = [ex.submit(run, c) for c in chunks[:parallel]]
        nxt = len(futures)
        done = 0
        while done < len(chunks):
            try:
                text = futures[done].result()
            except Exception as e:  # noqa: BLE001 - reported inside the document
                for f in futures[done + 1:]:
                    f.cancel()
                yield f"\n# ERROR: export stopped after {done * batch} of {len(iris)} entities: {e}\n".encode()
                return
            if nxt < len(chunks):
                futures.append(ex.submit(run, chunks[nxt]))
                nxt += 1
            done += 1
            yield text.encode("utf-8")
