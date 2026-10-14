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
  /** Suggested in the map's "Colour by" picker, lower first (atlas:colorBy in the shapes). */
  color_by?: number | null;
}

export interface Meta {
  classes: ClassMeta[];
  facets: FacetMeta[];
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

/** lon/lat are null when the feature has no geometry. `v`: display value when the search asked for a `color` key. */
export interface SearchItem { id: string; iri: string; class: string; label: string; lon: number | null; lat: number | null; v?: string | number | null }

/** POST /api/theme: colouring the map by a facet. */
interface ThemeBase {
  key: string;
  label: string;
  unit: string | null;
  /** Classes coloured by their own values; empty = every class (shared facet). */
  classes: string[];
  /** Line classes coloured by the values of their parts (sections of line from their tracks). */
  rollup: string[];
  /** Coloured features matching the query state, and how many of them have no value. */
  total: number;
  nodata: number;
}
export interface ThemeValue { value: string; label: string; count: number; slot: number | null }
export interface TermsTheme extends ThemeBase {
  kind: 'terms' | 'boolean';
  /** Values with a palette slot (stable for the environment) first, then the rest by count. */
  values: ThemeValue[];
  more: boolean;
  /** Features whose value has no slot colour, and features with several different values. */
  other: number;
  mixed: number;
}
export interface RangeBin { min: number; max: number; count: number }
export interface RangeTheme extends ThemeBase {
  kind: 'range';
  breaks: number[];
  bins: RangeBin[];
  min: number | null;
  max: number | null;
  /** One class per distinct value. */
  discrete: boolean;
}
export type Theme = TermsTheme | RangeTheme;
/** Tile / search value of a feature with several different values of a terms facet. */
export const MIXED = '__mixed__';
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
  search: (st: QueryState, limit: number, offset: number, signal?: AbortSignal, color?: string | null) =>
    post<SearchResponse>('/search', { ...st, limit, offset, ...(color ? { color } : {}) }, signal),
  /** Legend of the map coloured by facet `key` (palette slots or classes, counts for the state). */
  theme: (st: QueryState, key: string, signal?: AbortSignal) => post<Theme>('/theme', { ...st, key }, signal),
  extent: (st: QueryState, signal?: AbortSignal) => post<{ bbox: [number, number, number, number] | null }>('/extent', st, signal),
  feature: (env: string, iri: string, signal?: AbortSignal) => request<Feature>(`/feature?${qs({ env, iri })}`, { signal }),
  /** Low-zoom tiles carry only a numeric id; this resolves it. */
  featureById: (env: string, id: number | string, signal?: AbortSignal) =>
    request<Feature>(`/feature?${qs({ env, id: String(id) })}`, { signal }),
  describe: (env: string, iri: string, direction: 'out' | 'in', signal?: AbortSignal) =>
    request<DescribeResponse>(`/describe?${qs({ env, iri, direction })}`, { signal }),
  datasets: (env: string) => request<Dataset[]>(`/datasets?${qs({ env })}`),
  /** Download URL for every feature matching the state, as CSV or XLSX (one sheet per class). */
  exportUrl: (st: QueryState, format: 'csv' | 'xlsx') => {
    const { facets: _facets, ...rest } = st;
    return `${BASE}/export?${qs({ state: base64url(JSON.stringify(rest)), format })}`;
  },
  /** Tile URL template; `state` is base64url(JSON query state without bbox); `color` adds that facet's value `v`. */
  tilesUrl: (st: QueryState, color?: string | null) => {
    const { bbox: _bbox, ...rest } = st;
    const abs = new URL(BASE, location.href).href.replace(/\/$/, '');
    const c = color ? `&${qs({ color })}` : '';
    return `${abs}/tiles/{z}/{x}/{y}.pbf?state=${base64url(JSON.stringify(rest))}${c}`;
  },
};

export function base64url(s: string): string {
  const bytes = new TextEncoder().encode(s);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export const isAbort = (e: unknown) => e instanceof DOMException && e.name === 'AbortError';
