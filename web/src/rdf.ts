// Prefix handling and RDF term formatting.
import type { RdfTerm } from './api';

export const PREFIXES: Record<string, string> = {
  era: 'http://data.europa.eu/949/',
  rdf: 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
  rdfs: 'http://www.w3.org/2000/01/rdf-schema#',
  skos: 'http://www.w3.org/2004/02/skos/core#',
  gsp: 'http://www.opengis.net/ont/geosparql#',
  dcterms: 'http://purl.org/dc/terms/',
  xsd: 'http://www.w3.org/2001/XMLSchema#',
  wgs: 'http://www.w3.org/2003/01/geo/wgs84_pos#',
  owl: 'http://www.w3.org/2002/07/owl#',
};

// Longest namespace first so e.g. era: does not shadow a more specific one.
const ORDERED = Object.entries(PREFIXES).sort((a, b) => b[1].length - a[1].length);

/** Compact an IRI to prefix:local (local may contain '/' for readability), else return it unchanged. */
export function prefixed(iri: string): string {
  for (const [p, ns] of ORDERED) {
    if (iri.startsWith(ns)) {
      const local = iri.slice(ns.length);
      if (/^[\w./-]*$/.test(local)) return `${p}:${local}`;
    }
  }
  return iri;
}

/** Short display for an IRI: prefixed name, else the last path segment. */
export function shortIri(iri: string): string {
  const p = prefixed(iri);
  if (p !== iri && !p.includes('/')) return p;
  const m = iri.match(/[^/#]+$/);
  return m ? m[0] : iri;
}

export function literalText(t: RdfTerm): string {
  let s = t.value;
  if (t.lang) s += ` @${t.lang}`;
  else if (t.datatype && !t.datatype.endsWith('#string')) s += ` ^^${prefixed(t.datatype)}`;
  return s;
}

/** Display text for an N-Triples term (an IRI as a prefixed name, a literal as its lexical form). */
export function ntText(v: string): string {
  if (v.startsWith('<') && v.endsWith('>')) return prefixed(v.slice(1, -1));
  const m = /^"([\s\S]*)"(?:\^\^<[^>]*>|@[\w-]+)?$/.exec(v);
  if (!m) return v;
  return m[1].replace(/\\(["\\nrt])/g, (_, c: string) => ({ n: '\n', r: '\r', t: '\t' } as Record<string, string>)[c] ?? c);
}

/** A SPARQL property path with its IRIs as prefixed names. */
export function pathText(p: string): string {
  return p.replace(/<([^>]+)>/g, (_, iri: string) => prefixed(iri));
}
