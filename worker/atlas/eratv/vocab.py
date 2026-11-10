"""The ERATV parameter numbers and value lists, read from the ERA ontology and SKOS.

The ontology annotates the properties of era:VehicleType (and of the classes around
it) with `era:eratvIndex` (the ERATV parameter number), their range, unit
(`era:unitOfMeasure`, QUDT) and concept scheme (`era:inSkosConceptScheme`). The SKOS
lists hold ERATV-specific concepts (`.../eratv/...`) and the exact ERATV
configuration strings (`concepts/vehicle-type-configurations`). Both graphs are already
in the worker's mirror of every environment (`vocab_graphs` in environments.yaml), so
nothing here talks to ERA.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

import pyoxigraph as ox

ERA = "http://data.europa.eu/949/"
XSD = "http://www.w3.org/2001/XMLSchema#"
SKOS_CONCEPT = "http://www.w3.org/2004/02/skos/core#Concept"
CONFIG_SCHEME = ERA + "concepts/vehicle-type-configurations/VehicleTypeConfigurations"
ENERGY_SCHEME = ERA + "concepts/energy-supply-systems/EnergySupplySystems"
GAUGE_SCHEME = ERA + "concepts/nominal-track-gauges/NominalTrackGauges"
TPLS_SCHEME = ERA + "concepts/train-protection-legacy-systems/TrainProtectionLegacySystems"
CATEGORY_SCHEME = ERA + "concepts/vehicle-types/Categories"
SUBCATEGORY_SCHEME = ERA + "concepts/vehicle-types/SubCategories"

_Q_PROPS = """
PREFIX era: <http://data.europa.eu/949/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
SELECT ?p ?i (SAMPLE(?r) AS ?range) (SAMPLE(?d) AS ?domain) (SAMPLE(?u) AS ?unit)
       (SAMPLE(?s) AS ?scheme) (SAMPLE(?dep) AS ?deprecated) (SAMPLE(?l) AS ?label)
WHERE {
  ?p era:eratvIndex ?i .
  OPTIONAL { ?p rdfs:range ?r FILTER(isIRI(?r)) }
  OPTIONAL { ?p rdfs:domain ?d FILTER(isIRI(?d)) }
  OPTIONAL { ?p era:unitOfMeasure ?u }
  OPTIONAL { ?p era:inSkosConceptScheme ?s }
  OPTIONAL { ?p owl:deprecated ?dep }
  OPTIONAL { ?p rdfs:label ?l }
} GROUP BY ?p ?i
"""

_Q_CONCEPTS = """
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT ?c ?s ?l WHERE {
  ?c skos:inScheme ?s .
  { ?c skos:prefLabel ?l } UNION { ?c skos:altLabel ?l } UNION { ?c skos:notation ?l }
}
"""


# ERATV wording -> the label of the same concept in ERA's lists
_ALIASES = {
    "hauled passenger vehicles": "hauled vehicles",
    "a": "cat a", "b": "cat b",                    # fire safety category
}


@dataclass(frozen=True)
class Prop:
    iri: str
    index: str
    range: str | None
    domain: str | None
    unit: str | None
    scheme: str | None
    deprecated: bool
    label: str | None

    @property
    def is_concept(self) -> bool:
        return self.range == SKOS_CONCEPT or bool(self.scheme)

    @property
    def local(self) -> str:
        return self.iri.removeprefix(ERA)


def norm(s: str) -> str:
    """Comparison key of a label: case, accents, spacing and punctuation ignored."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"(\d)([a-z])", r"\1 \2", s.lower())      # '1800mm' == '1800 mm'
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


@dataclass
class Vocab:
    by_index: dict[str, list[Prop]] = field(default_factory=dict)
    by_iri: dict[str, Prop] = field(default_factory=dict)
    # scheme -> normalised label -> concept IRIs (RINF's concepts before ERATV-only ones)
    concepts: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    def props(self, index: str, include_deprecated: bool = False) -> list[Prop]:
        ps = self.by_index.get(index.strip(), [])
        return ps if include_deprecated else [p for p in ps if not p.deprecated]

    def prop(self, local: str) -> Prop | None:
        return self.by_iri.get(ERA + local)

    def concept(self, scheme: str | None, label: str) -> str | None:
        """The concept of `scheme` with this label; RINF's concepts before `.../eratv/...` ones."""
        if not scheme or not label:
            return None
        labels = self.concepts.get(scheme, {})
        key = norm(label)
        hits = labels.get(key) or labels.get(_ALIASES.get(key, ""))
        if hits:
            return hits[0]
        # 'SRT TSI Freight Locomotive' is the start of exactly one label: take it
        if len(key) >= 4:
            found = {cs[0] for k, cs in labels.items() if k.startswith(key + " ")}
            if len(found) == 1:
                return found.pop()
        return None

    def concept_any(self, label: str, schemes: list[str]) -> str | None:
        for s in schemes:
            c = self.concept(s, label)
            if c:
                return c
        return None

    def is_empty(self) -> bool:
        return not self.by_index


def _val(t) -> str | None:
    return None if t is None else t.value


def load(store: ox.Store) -> Vocab:
    """Builds the vocabulary from every graph of `store` (ontology + SKOS)."""
    v = Vocab()
    for row in store.query(_Q_PROPS, use_default_graph_as_union=True):
        dep = _val(row["deprecated"])
        p = Prop(iri=row["p"].value, index=row["i"].value.strip(), range=_val(row["range"]),
                 domain=_val(row["domain"]), unit=_val(row["unit"]), scheme=_val(row["scheme"]),
                 deprecated=(dep or "").lower() in ("true", "1"), label=_val(row["label"]))
        v.by_index.setdefault(p.index, []).append(p)
        v.by_iri[p.iri] = p
    for row in store.query(_Q_CONCEPTS, use_default_graph_as_union=True):
        c, s, label = row["c"].value, row["s"].value, row["l"].value
        lst = v.concepts.setdefault(s, {}).setdefault(norm(label), [])
        if c not in lst:
            # The concepts RINF uses win over ERATV-only ones with the same label, so a
            # vehicle compares with track data on the same IRI
            if "/eratv/" in c:
                lst.append(c)
            else:
                lst.insert(0, c)
    return v


def load_file(path) -> Vocab:
    s = ox.Store()
    s.load(path=str(path), format=ox.RdfFormat.TURTLE)
    return load(s)


def to_json(v: Vocab) -> dict:
    """The vocabulary as JSON, so the harvester and the CLI need not open the mirror."""
    return {"props": [vars(p) for ps in v.by_index.values() for p in ps], "concepts": v.concepts}


def from_json(d: dict) -> Vocab:
    v = Vocab(concepts={s: {k: list(cs) for k, cs in m.items()} for s, m in (d.get("concepts") or {}).items()})
    for raw in d.get("props") or []:
        p = Prop(**raw)
        v.by_index.setdefault(p.index, []).append(p)
        v.by_iri[p.iri] = p
    return v
