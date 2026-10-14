// Typed client for the RINF Atlas HTTP API (docs/api.md).

export interface Env { key: string; label: string; sparql: string; default: boolean }

export interface ClassMeta {
  class: string;
  class_iri: string;
  label: string;
  color: string;
  geometry: string;
  /** Below this map zoom the tiles leave the class out (the list and search still have it). */
  min_zoom: number;
  count: number;
}

export type FacetKind = 'terms' | 'range' | 'boolean';

export interface FacetMeta {
  key: string;
  class: string; // '*' = applies to every class
  label: string;
  kind: FacetKind;
  datatype: string;
  position: number;
  /** QUDT unit code for range facets, e.g. KiloM, MilliM, KiloM-PER-HR. */
  unit?: string | null;
}

export interface Meta {
  classes: ClassMeta[];
  facets: FacetMeta[];
  /** Turtle export queries ERA live and is capped at this many entities. */
  limits?: { ttl_export: number };
  datasets: { total: number; loaded: number; failed?: number; pending?: number; last_ingest: string | null; last_check?: string | null };
}

export type TermValue = string | number | boolean;
export interface RangeFilter { min?: number | null; max?: number | null }
export type FilterValue = TermValue[] | RangeFilter;

export interface QueryState {
  env: string;
  classes: string[];
  filters: Record<string, FilterValue>;
  q?: string;
  graphs?: string[];
  /** Only compute these facet keys (default: shared + selected classes' facets). */
  facets?: string[];
  bbox?: [number, number, number, number];
}

export interface TermCount { value: TermValue; label?: string; count: number }
export type FacetResult =
  | { kind: 'terms'; values: TermCount[]; more?: boolean }
  | { kind: 'range'; min: number | null; max: number | null }
  | { kind: 'boolean'; values: (TermCount | TermValue)[] };

export interface FacetsResponse {
  total: number;
  by_class: Record<string, number>;
  facets: Record<string, FacetResult>;
}

/** lon/lat are null when the feature has no geometry. */
export interface SearchItem { id: string; iri: string; class: string; label: string; lon: number | null; lat: number | null }
export interface SearchResponse { total: number; items: SearchItem[] }

export interface Feature {
  iri: string;
  label: string;
  classes: string[];
  graph_iri: string;
  facets: Record<string, { value: TermValue; label?: string }[]>;
  geometry: GeoJSON.Geometry | null;
}

export interface RdfTerm { type: 'uri' | 'literal' | 'bnode'; value: string; datatype?: string; lang?: string }
export interface Triple { s: string; p: string; o: RdfTerm }
export interface DescribeResponse { iri: string; triples: Triple[]; labels: Record<string, string> }

export type DatasetStatus = 'pending' | 'loading' | 'loaded' | 'failed' | 'removed';
export interface Dataset {
  graph_iri: string; title: string | null; provider: string | null; country: string | null;
  modified: string | null; status: DatasetStatus; triples: number | null; ingested_at: string | null;
  error: string | null; dataset_id: string | null; file_name: string | null; features: number;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, init);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try { const j = await res.json(); if (j?.detail) msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail); } catch { /* ignore */ }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

const post = <T>(path: string, body: unknown, signal?: AbortSignal) =>
  request<T>(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body), signal });

const qs = (o: Record<string, string>) => new URLSearchParams(o).toString();

export const api = {
  envs: () => request<Env[]>('/envs'),
  meta: (env: string) => request<Meta>(`/meta?${qs({ env })}`),
  facets: (st: QueryState, signal?: AbortSignal) => post<FacetsResponse>('/facets', st, signal),
  search: (st: QueryState, limit: number, offset: number, signal?: AbortSignal) =>
    post<SearchResponse>('/search', { ...st, limit, offset }, signal),
  extent: (st: QueryState, signal?: AbortSignal) => post<{ bbox: [number, number, number, number] | null }>('/extent', st, signal),
  feature: (env: string, iri: string, signal?: AbortSignal) => request<Feature>(`/feature?${qs({ env, iri })}`, { signal }),
  /** Low-zoom tiles carry only a numeric id; this resolves it. */
  featureById: (env: string, id: number | string, signal?: AbortSignal) =>
    request<Feature>(`/feature?${qs({ env, id: String(id) })}`, { signal }),
  describe: (env: string, iri: string, direction: 'out' | 'in', signal?: AbortSignal) =>
    request<DescribeResponse>(`/describe?${qs({ env, iri, direction })}`, { signal }),
  datasets: (env: string) => request<Dataset[]>(`/datasets?${qs({ env })}`),
  /** Download URL for every feature matching the state, as CSV or XLSX (one sheet per class). */
  exportUrl: (st: QueryState, format: 'csv' | 'xlsx' | 'ttl') => {
    const { facets: _facets, ...rest } = st;
    return `${BASE}/export?${qs({ state: base64url(JSON.stringify(rest)), format })}`;
  },
  /** Absolute URLs for desktop GIS (docs/api.md, "GIS access") for a state; bbox is left out. */
  gisUrls: (st: QueryState) => {
    const { bbox: _bbox, facets: _facets, ...rest } = st;
    const abs = new URL(BASE, location.href).href.replace(/\/$/, '');
    const filtered = rest.classes.length > 0 || Object.keys(rest.filters).length > 0 || !!rest.q || !!rest.graphs?.length;
    const enc = base64url(JSON.stringify(rest));
    const ogc = filtered ? `${abs}/ogc/filtered/${enc}` : `${abs}/ogc`;
    /** Collection id of the one selected class; a class mixing geometry types is served as
     *  `{id}.point`, `{id}.line`, `{id}.polygon` collections instead (see /collections). */
    const collection = rest.classes.length === 1 ? `${rest.env}.${rest.classes[0]}` : null;
    return { ogc, collection, tilejson: `${abs}/tiles.json?state=${enc}`, style: `${abs}/style.json?state=${enc}` };
  },
  /** Tile URL template; `state` is base64url(JSON query state without bbox). */
  tilesUrl: (st: QueryState) => {
    const { bbox: _bbox, ...rest } = st;
    const abs = new URL(BASE, location.href).href.replace(/\/$/, '');
    return `${abs}/tiles/{z}/{x}/{y}.pbf?state=${base64url(JSON.stringify(rest))}`;
  },
};

export function base64url(s: string): string {
  const bytes = new TextEncoder().encode(s);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError';
