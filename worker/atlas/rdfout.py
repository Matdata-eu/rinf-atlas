"""RDF serialisations of resolved descriptions: Turtle, N-Triples, JSON-LD.

Each writer takes resolved root nodes (atlas.resolve.Node) and yields bytes, so an
export of any size streams to its file. Blank nodes the mirror skolemised
(`urn:atlas:bnode:...`) come out as blank nodes again. A component shared by
several roots (a validity instant, a corridor) is written once per root: duplicate
triples are harmless in RDF, and remembering every node written would not fit in
memory for a whole network.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator

from .resolve import RDF_TYPE, Lit, Node, Ref

XSD = "http://www.w3.org/2001/XMLSchema#"
RDFS_LABEL = "http://www.w3.org/2000/01/rdf-schema#label"
ATLAS = "https://rinf-atlas.matdata.eu/ns#"
PREFIXES = {
    "era": "http://data.europa.eu/949/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": XSD,
    "owl": "http://www.w3.org/2002/07/owl#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "dcterms": "http://purl.org/dc/terms/",
    "geo": "http://www.opengis.net/ont/geosparql#",
    "time": "http://www.w3.org/2006/time#",
    "org": "http://www.w3.org/ns/org#",
    "wgs": "http://www.w3.org/2003/01/geo/wgs84_pos#",
    "country": "http://publications.europa.eu/resource/authority/country/",
    "atlas": ATLAS,
}
_BY_LENGTH = sorted(PREFIXES.items(), key=lambda kv: -len(kv[1]))
_LOCAL = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_ESC_RE = re.compile(r'[\\"\n\r\t]')
_NUMERIC = {XSD + t for t in ("integer", "decimal", "double", "float", "int", "long", "short", "byte",
                               "nonNegativeInteger", "positiveInteger", "negativeInteger", "nonPositiveInteger",
                               "unsignedInt", "unsignedLong", "unsignedShort", "unsignedByte")}


def compact(iri: str) -> str:
    """Prefixed name when a prefix fits, else <iri>."""
    for p, ns in _BY_LENGTH:
        if iri.startswith(ns) and _LOCAL.match(iri[len(ns):]):
            return f"{p}:{iri[len(ns):]}"
    return full(iri)


def full(iri: str) -> str:
    return "<" + "".join(c if c not in '<>"{}|^`\\ ' and ord(c) > 0x20 else f"\\u{ord(c):04X}" for c in iri) + ">"


def _literal(lit: Lit, compact_dt: bool) -> str:
    s = '"' + _ESC_RE.sub(lambda m: _ESCAPES[m.group()], lit.value) + '"'
    if lit.lang:
        return f"{s}@{lit.lang}"
    if lit.datatype and lit.datatype != XSD + "string":
        return f"{s}^^{compact(lit.datatype) if compact_dt else full(lit.datatype)}"
    return s


def _subject(n: Node, compact_iri: bool) -> str:
    b = n.bnode
    if b:
        return "_:" + b
    return compact(n.id) if compact_iri else full(n.id)


def _term(v, compact_iri: bool) -> str:
    if isinstance(v, Lit):
        return _literal(v, compact_iri)
    if isinstance(v, Ref):
        return compact(v.iri) if compact_iri else full(v.iri)
    return _subject(v, compact_iri)


def header(lines: Iterable[str]) -> str:
    out = [f"# {c}" for c in lines]
    out += [f"@prefix {p}: <{ns}> ." for p, ns in PREFIXES.items()]
    return "\n".join(out) + "\n\n"


def turtle(roots: Iterable[Node], comments: dict[str, str] | None = None) -> Iterator[bytes]:
    """One block per resolved node (root, then its components, depth first)."""
    for root in roots:
        c = (comments or {}).get(root.id)
        buf = [f"# {c}\n"] if c else []
        for n in root.walk():
            lines = []
            if n.types:
                lines.append("    a " + ", ".join(compact(t) for t in n.types))
            for p in sorted(n.props, key=lambda p: (p != RDFS_LABEL, p)):
                vals = n.props[p]
                lines.append(f"    {compact(p)} " + ", ".join(_term(v, True) for v in vals))
            if not lines:
                buf.append(f"# {_subject(n, True)}: no triples in the mirror\n\n")
                continue
            buf.append(_subject(n, True) + "\n" + " ;\n".join(lines) + " .\n\n")
        yield "".join(buf).encode("utf-8")


def ntriples(roots: Iterable[Node]) -> Iterator[bytes]:
    for root in roots:
        buf = []
        for n in root.walk():
            s = _subject(n, False)
            for t in n.types:
                buf.append(f"{s} <{RDF_TYPE}> {full(t)} .\n")
            for p, vals in n.props.items():
                for v in vals:
                    buf.append(f"{s} {full(p)} {_term(v, False)} .\n")
        yield "".join(buf).encode("utf-8")


# --- JSON-LD ----------------------------------------------------------------------

def _json_value(v, embedded: set[str]):
    if isinstance(v, Lit):
        if v.lang:
            return {"@value": v.value, "@language": v.lang}
        dt = v.datatype
        if not dt or dt == XSD + "string":
            return v.value
        if dt == XSD + "boolean":
            if v.value in ("true", "1"):
                return True
            if v.value in ("false", "0"):
                return False
        if dt in _NUMERIC:
            try:
                return int(v.value) if dt in (XSD + "integer", XSD + "int", XSD + "long", XSD + "short", XSD + "byte",
                                              XSD + "nonNegativeInteger", XSD + "positiveInteger") else float(v.value)
            except ValueError:
                pass
        return {"@value": v.value, "@type": compact(dt)}
    if isinstance(v, Ref):
        o = {"@id": v.iri}
        if v.label:
            o["rdfs:label"] = v.label
        return o
    return _json_node(v, embedded)


def _json_node(n: Node, embedded: set[str]) -> dict:
    nid = "_:" + n.bnode if n.bnode else n.id
    if n.id in embedded:
        return {"@id": nid}   # a cycle, or a component shared within this root: embedded once
    embedded.add(n.id)
    o: dict = {"@id": nid}
    if n.types:
        o["@type"] = [compact(t) for t in n.types]
    for p, vals in n.props.items():
        key = compact(p)
        out = [_json_value(v, embedded) for v in vals]
        o[key] = out[0] if len(out) == 1 else out
    return o


def jsonld_context() -> dict:
    ctx: dict = dict(PREFIXES)
    ctx["rdfs:label"] = {"@id": RDFS_LABEL}
    return ctx


def jsonld(roots: Iterable[Node], meta: dict | None = None, extra: dict[str, dict] | None = None) -> Iterator[bytes]:
    """A JSON-LD document: `@context`, optional `atlas:export` metadata, and `@graph` with one
    framed object per root (components embedded, other elements as `{"@id", "rdfs:label"}`).
    `extra[root.id]` adds keys (e.g. the dataset) to that root's object."""
    head = {"@context": jsonld_context()}
    if meta:
        head["atlas:export"] = meta
    text = json.dumps(head, ensure_ascii=False)
    yield (text[:-1] + ', "@graph": [\n').encode("utf-8")
    first = True
    for root in roots:
        o = _json_node(root, set())
        for k, v in (extra or {}).get(root.id, {}).items():
            o[k] = v
        yield (("" if first else ",\n") + json.dumps(o, ensure_ascii=False)).encode("utf-8")
        first = False
    yield b"\n]}\n"
