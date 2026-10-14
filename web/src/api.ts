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

// ---------- vehicle compatibility (shapes/compatibility.ttl) ----------
export type CompatKind = 'terms' | 'number' | 'boolean';
export interface CompatRule {
  key: string;
  label: string;
  comment: string | null;
  /** ERA ontology path from era:VehicleType to the characteristic (predicate IRIs). */
  path: string[];
  /** SHACL constraint component applied to the infrastructure values: hasValue, in, minInclusive... */
  constraint: string;
  kind: CompatKind;
  datatype: string | null;
  unit: string | null;
  facets: string[];
  applies_to: 'track' | 'section';
  /** What a track without a value means: unknown (yellow, default) or incompatible (red). */
  when_missing?: 'unknown' | 'incompatible';
  /** A value meaning "also runs where the system is absent" (e.g. not electrified); listed in values with none: true. */
  none_value?: string | null;
  /** The rule whose none_value also covers this one (e.g. the energy supply for the pantograph). */
  none_from?: string | null;
  /** Shown for a track where the system is absent, e.g. "No contact line". */
  missing_reason?: string | null;
  values?: TermCount[];
  range?: { min: number | null; max: number | null; count: number };
}
export interface CompatRules { track_classes: string[]; section_class: string; rules: CompatRule[] }
/** characteristic key -> IRIs / booleans (terms, boolean) or a number. */
export type VehicleValues = Record<string, (string | boolean)[] | number>;
export type CompatColour = 'green' | 'yellow' | 'red';
export interface CompatResponse {
  env: string;
  /** The indexed data the ids refer to: put in the tile URL of the coloured layer. */
  version?: string;
  checked: string[]; ignored: string[];
  counts: Record<CompatColour, number>;
  green: (number | string)[]; yellow: (number | string)[]; red: (number | string)[];
}
export type CompatResult = 'ok' | 'unknown' | 'fail';
export interface CompatCheck { key: string; result: CompatResult; values: { value: TermValue; label: string }[]; note?: string }
export interface CompatExplain {
  iri: string; id: number | string; status: CompatColour; checked: string[]; ignored: string[];
  section: CompatCheck[];
  tracks: { id: number | string; iri: string; label: string | null; class: string; status: CompatColour; results: CompatCheck[] }[];
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
  compatRules: (env: string, signal?: AbortSignal) => request<CompatRules>(`/compatibility/rules?${qs({ env })}`, { signal }),
  /** Status of every section of line for a vehicle profile (ids per colour). */
  compat: (env: string, profile: VehicleValues, signal?: AbortSignal) =>
    post<CompatResponse>('/compatibility', { env, profile }, signal),
  compatExplain: (env: string, profile: VehicleValues, iri: string, signal?: AbortSignal) =>
    post<CompatExplain>('/compatibility/explain', { env, profile, iri }, signal),
  /** Download URL for every feature matching the state, as CSV or XLSX (one sheet per class). */
  exportUrl: (st: QueryState, format: 'csv' | 'xlsx' | 'ttl') => {
    const { facets: _facets, ...rest } = st;
    return `${BASE}/export?${qs({ state: base64url(JSON.stringify(rest)), format })}`;
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
