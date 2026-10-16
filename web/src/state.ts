// Tiny observable store (the URL hash is read and written by url.ts).
import type { FilterValue, Meta, ProfileSpec, QueryState, Theme, VehicleValues } from './api';

export type DetailTab = 'properties' | 'describe' | 'graph';
export type SideTab = 'filters' | 'results';

export interface MapView { lng: number; lat: number; zoom: number }

/** Live Describe navigation inside the detail panel, for the feature it was started from. */
export interface DescribeNav { for: string; path: string[]; dir: 'out' | 'in' }

/** The vehicle profile the compatibility check is shown for (its content, so a link carries it). */
export interface SharedVehicle { name: string; values: VehicleValues }

export interface AppState {
  env: string;
  classes: string[];
  filters: Record<string, FilterValue>;
  /** Provider graph IRIs (datasets) to restrict to; empty = all. */
  graphs: string[];
  q: string;
  limitToView: boolean;
  bbox: [number, number, number, number] | null; // current map bounds (only used when limitToView)
  view: MapView;
  selected: string | null; // feature IRI
  detailTab: DetailTab;
  /** IRIs followed in the Describe tab and its direction; ignored unless `for` is the selected feature. */
  describe: DescribeNav | null;
  orm: boolean;
  /** Ids of the deployer's extra map layers that are switched on (config/layers.yaml). */
  layers: string[];
  /** Facet key (as in filters: "class/key" or a shared key) the map is coloured by; null = class colours. */
  colorBy: string | null;
  /** Legend entries switched off: values, "bin:<i>" for range classes, or NODATA / MIXED / OTHER / FADED. */
  colorHidden: string[];
  /** Highlight features with data quality issues on the map. */
  qa: boolean;
  sideTab: SideTab;
  /** Line profile panel open, and what it shows (null = nothing chosen yet). */
  profileOpen: boolean;
  profile: ProfileSpec | null;
  /** Rows (facet keys) of the line profile strip; null = the API's default rows. */
  profileRows: string[] | null;
  /** Vehicle compatibility shown on the map, for this profile; null = off. */
  vehicle: SharedVehicle | null;
  /** The vehicle check panel is open (the map colouring has its own on/off, kept per browser). */
  vehicleOpen: boolean;
  meta: Meta | null; // per-env metadata (not in the URL)
  theme: Theme | null; // legend of the current colouring (not in the URL)
}

type Listener = (s: AppState, prev: AppState) => void;

export const EUROPE: MapView = { lng: 10, lat: 50.5, zoom: 3.6 };

class Store {
  private s: AppState;
  private listeners = new Set<Listener>();
  constructor(init: AppState) { this.s = init; }
  get(): AppState { return this.s; }
  set(patch: Partial<AppState>) {
    const prev = this.s;
    const next = { ...prev, ...patch };
    if ((Object.keys(patch) as (keyof AppState)[]).every((k) => prev[k] === next[k])) return;
    this.s = next;
    for (const l of this.listeners) l(next, prev);
  }
  subscribe(fn: Listener): () => void { this.listeners.add(fn); return () => this.listeners.delete(fn); }
}

export const store = new Store({
  env: '', classes: [], filters: {}, graphs: [], q: '', limitToView: false, bbox: null,
  view: EUROPE, selected: null, detailTab: 'properties', describe: null, orm: false, layers: [], qa: false, sideTab: 'filters', vehicleOpen: false,
  colorBy: null, colorHidden: [], profileOpen: false, profile: null, profileRows: null, vehicle: null, meta: null, theme: null,
});

/** Did any of the given keys change between prev and next? */
export const changed = (s: AppState, prev: AppState, ...keys: (keyof AppState)[]) => keys.some((k) => s[k] !== prev[k]);

/** The API query state derived from the app state. */
export function queryState(s: AppState = store.get(), withBbox = true): QueryState {
  const qs: QueryState = { env: s.env, classes: s.classes, filters: s.filters };
  if (s.graphs.length) qs.graphs = s.graphs;
  if (s.q.trim()) qs.q = s.q.trim();
  if (withBbox && s.limitToView && s.bbox) qs.bbox = s.bbox;
  return qs;
}

export function isFilterEmpty(v: FilterValue | undefined): boolean {
  if (v == null) return true;
  if (Array.isArray(v)) return v.length === 0;
  return v.min == null && v.max == null;
}

export function debounce<A extends unknown[]>(fn: (...a: A) => void, ms: number) {
  let t: ReturnType<typeof setTimeout> | undefined;
  return (...a: A) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

/** Small DOM helper: h('div', {class: 'x'}, child, 'text'). */
export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K, attrs: Record<string, string | number | boolean | EventListener | undefined> = {}, ...children: (Node | string | null | undefined | false)[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = String(v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, String(v));
  }
  for (const c of children) if (c != null && c !== false) el.append(c);
  return el;
}

export const fmt = new Intl.NumberFormat('en');

/** Short display form of the QUDT unit codes used by the ERA ontology. */
const UNITS: Record<string, string> = {
  M: 'm', KiloM: 'km', MilliM: 'mm', CentiM: 'cm', M2: 'm²', 'KiloM-PER-HR': 'km/h', 'M-PER-SEC': 'm/s',
  'M-PER-SEC2': 'm/s²', 'MilliM-PER-M': 'mm/m', V: 'V', KiloV: 'kV', A: 'A', TONNE: 't', 'TONNE-PER-M': 't/m', KiloGM: 'kg',
  DEG: '°', SEC: 's', HZ: 'Hz', W: 'W', KiloW: 'kW', PERCENT: '%',
};
export const unitLabel = (u: string) => UNITS[u] ?? u;
