// Tiny observable store + URL-hash (de)serialisation for shareable links.
import type { FilterValue, Meta, QueryState } from './api';

export type DetailTab = 'properties' | 'describe' | 'graph';

export interface MapView { lng: number; lat: number; zoom: number }

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
  orm: boolean;
  sideTab: 'filters' | 'results';
  meta: Meta | null; // per-env metadata (not in the URL)
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
  view: EUROPE, selected: null, detailTab: 'properties', orm: false, sideTab: 'filters', meta: null,
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

// ---------- URL hash ----------
// #env=prod&c=tunnel,switch&f={"country":[...]}&q=brussel&v=1&map=6.2/50.8/4.4&sel=<iri>&tab=describe&orm=1

export function readHash(): Partial<AppState> {
  const p = new URLSearchParams(location.hash.replace(/^#/, ''));
  const out: Partial<AppState> = {};
  if (p.get('env')) out.env = p.get('env')!;
  if (p.get('c')) out.classes = p.get('c')!.split(',').filter(Boolean);
  if (p.get('f')) { try { out.filters = JSON.parse(p.get('f')!); } catch { /* ignore bad links */ } }
  if (p.get('q')) out.q = p.get('q')!;
  if (p.get('ds')) out.graphs = p.get('ds')!.split(' ').filter(Boolean);
  if (p.get('v') === '1') out.limitToView = true;
  if (p.get('orm') === '1') out.orm = true;
  const m = p.get('map')?.split('/').map(Number);
  if (m && m.length === 3 && m.every(Number.isFinite)) out.view = { zoom: m[0], lat: m[1], lng: m[2] };
  if (p.get('sel')) out.selected = p.get('sel');
  const tab = p.get('tab');
  if (tab === 'describe' || tab === 'graph' || tab === 'properties') out.detailTab = tab;
  return out;
}

export function writeHash(s: AppState) {
  const p = new URLSearchParams();
  if (s.env) p.set('env', s.env);
  if (s.classes.length) p.set('c', s.classes.join(','));
  const f = Object.fromEntries(Object.entries(s.filters).filter(([, v]) => !isFilterEmpty(v)));
  if (Object.keys(f).length) p.set('f', JSON.stringify(f));
  if (s.q) p.set('q', s.q);
  if (s.graphs.length) p.set('ds', s.graphs.join(' '));
  if (s.limitToView) p.set('v', '1');
  if (s.orm) p.set('orm', '1');
  p.set('map', `${s.view.zoom.toFixed(2)}/${s.view.lat.toFixed(5)}/${s.view.lng.toFixed(5)}`);
  if (s.selected) { p.set('sel', s.selected); if (s.detailTab !== 'properties') p.set('tab', s.detailTab); }
  const hash = '#' + p.toString();
  if (hash !== location.hash) history.replaceState(null, '', hash);
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
  'M-PER-SEC2': 'm/s²', 'MilliM-PER-M': 'mm/m', V: 'V', KiloV: 'kV', A: 'A', TONNE: 't', KiloGM: 'kg',
  DEG: '°', SEC: 's', HZ: 'Hz', W: 'W', KiloW: 'kW', PERCENT: '%',
};
export const unitLabel = (u: string) => UNITS[u] ?? u;
