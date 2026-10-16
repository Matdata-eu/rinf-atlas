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

/** The SHACL shapes a validation used: ERA's mirrored shapes graph, or a pinned GitLab tag. */
export interface ShapesSource {
  kind: 'graph' | 'tag';
  graph: string;
  /** e.g. "era-ontology v3.3.4 (era-shacl/ERA-RINF-shapes.ttl)" or "ERA's shapes graph (version 3.3.4)". */
  label: string;
  url?: string;
  tag?: string;
  repo?: string;
  file?: string;
  /** owl:versionInfo found in the shapes. */
  version?: string;
  modified?: string;
}

/** Why a dataset was not validated. */
export type QualityReason = 'too_large' | 'no_shapes' | null;

export interface QualityMeta {
  /** The worker validates datasets against ERA's SHACL shapes (and the `quality` facet exists). */
  enabled: boolean;
  validated: number;
  violations: number;
  warnings: number;
  infos: number;
  checked_at: string | null;
  shapes_graph: string | null;
  shapes_loaded_at: string | null;
  /** The shapes the environment validates with now, and why they could not be loaded (if so). */
  shapes?: ShapesSource | null;
  shapes_error?: string | null;
  /** Datasets above this many triples are not validated. */
  max_triples?: number | null;
  /** Datasets skipped because they are above max_triples. */
  too_large?: number;
  /** A shacl-report instance to explore downloaded reports with (null = no link). */
  shacl_report_url: string | null;
}

export interface Meta {
  classes: ClassMeta[];
  facets: FacetMeta[];
  /** Turtle export queries ERA live and is capped at this many entities. */
  limits?: { ttl_export: number };
  datasets: { total: number; loaded: number; failed?: number; pending?: number; last_ingest: string | null; last_check?: string | null };
  quality?: QualityMeta;
}

/** Worst SHACL severity found on a feature: 3 violation, 2 warning, 1 info, 0 none; null = not validated. */
export type QaLevel = 0 | 1 | 2 | 3 | null;
export type Severity = 'violation' | 'warning' | 'info';
export const SH = 'http://www.w3.org/ns/shacl#';
/** Values of the `quality` facet. */
export const QUALITY_VALUES: Record<Severity, string> = { violation: `${SH}Violation`, warning: `${SH}Warning`, info: `${SH}Info` };

export interface QualityResult {
  severity: Severity;
  message: string | null;
  path: string | null;
  /** N-Triples form of the offending value. */
  value: string | null;
  value_label?: string | null;
  shape: string;
  component: string;
}

export interface FeatureQuality {
  /** Dataset validation: null = not yet, ok, partial, skipped, failed. */
  status: 'ok' | 'partial' | 'skipped' | 'failed' | null;
  checked_at: string | null;
  note: string | null;
  reason?: QualityReason;
  shapes?: ShapesSource | null;
  results: QualityResult[];
  truncated: boolean;
  /** The dataset's report can be downloaded (Turtle) / opened in shacl-report's viewer. */
  report?: boolean;
  html?: boolean;
}

export interface DatasetQuality {
  status: FeatureQuality['status'];
  violations: number | null; warnings: number | null; infos: number | null;
  focus_nodes: number | null; kept: number | null;
  checked_at: string | null; seconds: number | null; note: string | null;
  /** Why it was skipped; for too_large, `limit` is the size limit it exceeded. */
  reason?: QualityReason; limit?: number | null;
  /** The shapes it was validated with. */
  shapes?: ShapesSource | null;
  /** A Turtle sh:ValidationReport / shacl-report HTML viewer can be downloaded. */
  report: boolean; html: boolean;
}

export interface QualityRule { shape: string; component: string; severity: Severity; count: number; message: string | null }

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
  /** Per class, how many of by_class have no geometry (classes without any are left out). */
  unlocated_by_class: Record<string, number>;
  facets: Record<string, FacetResult>;
}

/** lon/lat are null when the feature has no geometry. `v`: display value when the search asked for a `color` key. */
export interface SearchItem {
  id: string; iri: string; class: string; label: string; lon: number | null; lat: number | null;
  v?: string | number | null; qa?: QaLevel;
}

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
/** `unlocated`: how many of `total` have no geometry, so they are listed but not drawn on the map. */
export interface SearchResponse { total: number; unlocated: number; items: SearchItem[] }

export interface Feature {
  iri: string;
  label: string;
  classes: string[];
  graph_iri: string;
  facets: Record<string, { value: TermValue; label?: string }[]>;
  /** Facet label per key, including keys that are not filters (link facets). */
  facet_labels?: Record<string, string>;
  /** terms | range | boolean | link (IRI of another indexed feature). */
  facet_kinds?: Record<string, string>;
  geometry: GeoJSON.Geometry | null;
  quality?: FeatureQuality;
}

// ---------- line profile ----------
/** What the profile shows: a national line, sections picked on the map, or a route between operational points. */
export type ProfileSpec =
  | { mode: 'line'; line: string; graph?: string; reverse?: boolean }
  | { mode: 'sections'; sections: string[]; reverse?: boolean }
  | { mode: 'route'; from: string; to: string; reverse?: boolean };

export interface LineHit { line: string; graph_iri: string; provider: string | null; sections: number; length_km: number | null }

export interface ProfileParam { key: string; label: string; kind: 'terms' | 'range' | 'boolean'; unit: string | null }
export interface ProfileValue { value: TermValue; label: string }
/** A strip cell: min/max over the section and its tracks (range) or every value with the number of tracks having it. */
export type ProfileCell = { min?: number; max?: number; values?: (ProfileValue & { n: number })[] };
export interface ProfileTrack { iri: string; label: string; direction: string | null; values: Record<string, ProfileValue[]> }
export interface ProfileSection {
  id: string | number; iri: string; label: string; graph_iri: string;
  from: string | null; to: string | null; reversed: boolean;
  start_km: number; end_km: number; length_km: number; length_estimated: boolean;
  gap_before: boolean; gap_km: number | null;
  geometry: GeoJSON.Geometry | null;
  cells: Record<string, ProfileCell>;
  tracks: ProfileTrack[];
}
export interface ProfilePoint { iri: string | null; id: string | number | null; label: string | null; km: number; lon: number | null; lat: number | null }
export interface ProfileResponse {
  env: string; mode: 'line' | 'sections' | 'route';
  line: { id: string; graph_iri: string | null; provider: string | null; alternatives: { graph_iri: string; provider: string | null; sections: number }[] } | null;
  params: ProfileParam[]; length_km: number; gaps: number;
  sections: ProfileSection[]; points: ProfilePoint[]; missing: string[]; truncated: boolean;
}

export interface RdfTerm { type: 'uri' | 'literal' | 'bnode'; value: string; datatype?: string; lang?: string }
export interface Triple { s: string; p: string; o: RdfTerm }
export interface DescribeResponse { iri: string; triples: Triple[]; labels: Record<string, string> }

export type DatasetStatus = 'pending' | 'loading' | 'loaded' | 'failed' | 'removed';
export interface Dataset {
  graph_iri: string; title: string | null; provider: string | null; country: string | null;
  modified: string | null; status: DatasetStatus; triples: number | null; ingested_at: string | null;
  error: string | null; dataset_id: string | null; file_name: string | null; features: number;
  quality?: DatasetQuality;
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
  /** A value meaning "the vehicle does not have the system" (e.g. no ETCS); listed first in values with without: true. */
  without_value?: string | null;
  without_label?: string | null;
  /** The track values a vehicle without the system runs on (e.g. no ETCS, level 0, NTC). */
  without_matches?: string[];
  /** A track whose facet has the value `when` counts as having `value` (e.g. ETCS not applicable: level N). */
  implied?: { facet: string; when: string; value: string }[];
  /** A numeric rule on a facet of concepts: the number each concept stands for (e.g. axle load per line category). */
  term_values?: Record<string, number>;
  /** The ERA property along path holds the value times this factor (e.g. 1000: t in the profile, kg in ERA). */
  path_factor?: number | null;
  /** Facets a track without a value takes from its section of line. */
  section_facets?: string[];
  /** What the rule compares: each facet, the RINF property path it is read from, the classes indexing it. */
  infra?: CompatInfra[];
  /** The rule as Turtle (from shapes/compatibility.ttl); null before the worker republished its defs. */
  source?: string | null;
  /** How the rule is checked, in words generated by the API from the rule's annotations. */
  explanation?: { vehicle_path: string; steps: { topic: string; text: string }[] };
  values?: TermCount[];
  range?: { min: number | null; max: number | null; count: number };
}
export interface CompatInfra { key: string; label: string; path: string | null; classes: string[]; unit?: string | null }
export interface CompatRules {
  track_classes: string[]; section_class: string; rules: CompatRule[];
  /** class key -> label, for the track and section classes. */
  class_labels?: Record<string, string>;
}
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
export interface CompatCheck { key: string; result: CompatResult; values: { value: TermValue; label: string }[]; note?: string; bypass?: string }
export interface CompatExplain {
  iri: string; id: number | string; status: CompatColour; checked: string[]; ignored: string[];
  section: CompatCheck[];
  tracks: { id: number | string; iri: string; label: string | null; class: string; status: CompatColour; results: CompatCheck[] }[];
}

/** An extra map layer configured by whoever deploys this instance (config/layers.yaml, docs/layers.md). */
export interface MapLayer {
  id: string; label: string; description: string;
  type: 'raster' | 'vector' | 'wms';
  /** Tile URL templates; a proxied layer's point at /api/layers/{id}/... so its key stays on the server. */
  tiles: string[]; proxied: boolean;
  tile_size: number; min_zoom: number; max_zoom: number; opacity: number;
  attribution: string;
  /** Under the RINF data or above it. */
  position: 'below' | 'above';
  /** Vector layers: MapLibre style layers (no id or source). */
  style?: Record<string, unknown>[];
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
  layers: () => request<MapLayer[]>('/layers'),
  /** Absolute tile URL of a proxied layer (MapLibre needs absolute URLs in workers). */
  layerTilesUrl: (id: string) => `${new URL(BASE, location.href).href.replace(/\/$/, '')}/layers/${encodeURIComponent(id)}/{z}/{x}/{y}`,
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
  compatRules: (env: string, signal?: AbortSignal) => request<CompatRules>(`/compatibility/rules?${qs({ env })}`, { signal }),
  /** Status of every section of line for a vehicle profile (ids per colour). */
  compat: (env: string, profile: VehicleValues, signal?: AbortSignal) =>
    post<CompatResponse>('/compatibility', { env, profile }, signal),
  compatExplain: (env: string, profile: VehicleValues, iri: string, signal?: AbortSignal) =>
    post<CompatExplain>('/compatibility/explain', { env, profile, iri }, signal),
  lines: (env: string, q: string, signal?: AbortSignal) => request<LineHit[]>(`/lines?${qs({ env, q })}`, { signal }),
  profile: (env: string, spec: ProfileSpec, params: string[] | null, signal?: AbortSignal) => {
    const { mode: _mode, ...rest } = spec;
    return post<ProfileResponse>('/profile', { env, ...rest, ...(params ? { params } : {}) }, signal);
  },
  qualitySummary: (env: string, graph: string) => request<QualityRule[]>(`/quality/summary?${qs({ env, graph })}`),
  /** The dataset's SHACL validation report: Turtle download, or shacl-report's HTML viewer. */
  qualityReportUrl: (env: string, graph: string, format: 'ttl' | 'html', download = false) =>
    `${BASE}/quality/report?${qs({ env, graph, format, ...(download ? { download: 'true' } : {}) })}`,
  qualityShapesUrl: (env: string) => `${BASE}/quality/shapes?${qs({ env })}`,
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
