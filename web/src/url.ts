// Shareable links: every meaningful piece of UI state lives in the URL hash, so a view can
// be sent to a colleague and reopens identically. The scheme is documented in docs/urls.md.
//
//   #env=prod&map=7.5/50.84/4.36&c=section-of-line&f=country:cty:BEL;section-of-line/length:1000..
//    &sel=era:functionalInfrastructure/sectionsOfLine/SOL_1&tab=describe&col=running-track/maximumPermittedSpeed
//    &lp=line,50A&veh=wheelSetGauge:erac:nominal-track-gauges/30;trainLength:200&vehn=My+EMU
//
// Older links (the JSON `f` and `lp`, space separated `ds` and `colh`, full IRIs) still open.
// Continuous changes (pan, zoom, legend toggles, typing) replace the current history entry;
// meaningful navigation (filters, selection, opening a tool) pushes one, so Back undoes it.
import type { FilterValue, ProfileSpec, TermValue, VehicleValues } from './api';
import { isFilterEmpty, type AppState, type DescribeNav, type DetailTab, type MapView, type SideTab } from './state';

// ---------- IRI prefixes ----------
/** Prefixes for IRIs in links; longest namespace wins when writing. Never change a mapping (old links). */
export const PREFIXES: Record<string, string> = {
  erac: 'http://data.europa.eu/949/concepts/',
  era: 'http://data.europa.eu/949/',
  cty: 'http://publications.europa.eu/resource/authority/country/',
  eu: 'http://publications.europa.eu/resource/authority/',
  sh: 'http://www.w3.org/ns/shacl#',
  atlas: 'https://rinf-atlas.matdata.eu/ns#',
};
const BY_LENGTH = Object.entries(PREFIXES).sort((a, b) => b[1].length - a[1].length);
const CURIE = /^([a-z]+):(.*)$/s;

export function compactIri(s: string): string {
  for (const [p, ns] of BY_LENGTH) if (s.length > ns.length && s.startsWith(ns)) return `${p}:${s.slice(ns.length)}`;
  return s;
}
export function expandIri(s: string): string {
  const m = CURIE.exec(s);
  return m && PREFIXES[m[1]] && !m[2].startsWith('//') ? PREFIXES[m[1]] + m[2] : s;
}

// ---------- list items ----------
// Items are joined with "," and entries with ";": inside an item those two and "%" are
// percent-encoded (and spaces too where a legacy space-separated form must stay distinguishable).
const esc = (s: string, space = false) => s.replace(space ? /[%,; ]/g : /[%,;]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase().padStart(2, '0')}`);
const unesc = (s: string) => s.replace(/%(25|2C|3B|20)/gi, (m) => decodeURIComponent(m));
const NUM = '-?\\d+(?:\\.\\d+)?(?:[eE][+-]?\\d+)?';
const IS_NUM = new RegExp(`^${NUM}$`);
const IS_RANGE = new RegExp(`^(${NUM})?\\.\\.(${NUM})?$`);

/** A string item: IRIs with a known namespace become prefix:local; strings that would read as something else get a leading '. */
export function encStr(s: string, space = false): string {
  const c = compactIri(s);
  if (c !== s) return esc(c, space);
  const m = CURIE.exec(s);
  const ambiguous = s === '' || /^['!]/.test(s) || IS_NUM.test(s) || s.includes('..') || (m && PREFIXES[m[1]] && !m[2].startsWith('//'));
  return (ambiguous ? "'" : '') + esc(s, space);
}
export function decStr(s: string): string {
  return s.startsWith("'") ? unesc(s.slice(1)) : expandIri(unesc(s));
}
/** A typed facet value: !t / !f booleans, bare numbers, strings as encStr. */
export function encTerm(v: TermValue): string {
  if (typeof v === 'boolean') return v ? '!t' : '!f';
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : "'NaN";
  return encStr(v);
}
export function decTerm(s: string): TermValue {
  if (s === '!t') return true;
  if (s === '!f') return false;
  if (IS_NUM.test(s)) return Number(s);
  return decStr(s);
}
const encList = (xs: string[], space = false) => xs.map((x) => encStr(x, space)).join(',');
const decList = (s: string) => s.split(',').filter(Boolean).map(decStr);

// ---------- filters: key:item,item;key:min..max ----------
export function encFilters(f: Record<string, FilterValue>): string {
  return Object.entries(f).filter(([, v]) => !isFilterEmpty(v)).map(([k, v]) => {
    const val = Array.isArray(v) ? v.map(encTerm).join(',') : `${v.min ?? ''}..${v.max ?? ''}`;
    return `${esc(k).replace(/:/g, '%3A')}:${val}`;
  }).join(';');
}
export function decFilters(s: string): Record<string, FilterValue> {
  if (s.startsWith('{')) { // legacy JSON
    try { const o = JSON.parse(s); return o && typeof o === 'object' && !Array.isArray(o) ? o : {}; } catch { return {}; }
  }
  const out: Record<string, FilterValue> = {};
  for (const part of s.split(';')) {
    const i = part.indexOf(':');
    if (i <= 0) continue;
    const key = unesc(part.slice(0, i).replace(/%3A/gi, ':'));
    const v = part.slice(i + 1);
    const r = IS_RANGE.exec(v);
    if (r && (r[1] || r[2])) out[key] = { min: r[1] ? Number(r[1]) : null, max: r[2] ? Number(r[2]) : null };
    else if (v) out[key] = v.split(',').filter(Boolean).map(decTerm);
  }
  return out;
}

// ---------- vehicle profile: key:item,item;key:number ----------
export function encVehicle(values: VehicleValues): string {
  return Object.entries(values).filter(([, v]) => (Array.isArray(v) ? v.length > 0 : Number.isFinite(v)))
    .map(([k, v]) => `${esc(k)}:${Array.isArray(v) ? v.map(encTerm).join(',') : String(v)}`).join(';');
}
export function decVehicle(s: string): VehicleValues {
  const out: VehicleValues = {};
  for (const part of s.split(';')) {
    const i = part.indexOf(':');
    if (i <= 0) continue;
    const key = unesc(part.slice(0, i));
    const items = part.slice(i + 1).split(',').filter(Boolean).map(decTerm);
    if (items.length === 1 && typeof items[0] === 'number') out[key] = items[0];
    else {
      const xs = items.filter((x): x is string | boolean => typeof x !== 'number');
      if (xs.length) out[key] = xs;
    }
  }
  return out;
}

// ---------- line profile: line,<id>[,<dataset>] | sec,<iri>,... | route,<from>,<to>; "-r" = reversed ----------
export function encProfile(p: ProfileSpec): string {
  const m = (p.mode === 'sections' ? 'sec' : p.mode) + (p.reverse ? '-r' : '');
  if (p.mode === 'line') return [m, encStr(p.line), ...(p.graph ? [encStr(p.graph)] : [])].join(',');
  if (p.mode === 'sections') return [m, encList(p.sections)].join(',');
  return [m, encStr(p.from), encStr(p.to)].join(',');
}
export function decProfile(s: string): ProfileSpec | null {
  if (s.startsWith('{')) { // legacy JSON
    try { const o = JSON.parse(s); return o && typeof o === 'object' && o.mode ? o as ProfileSpec : null; } catch { return null; }
  }
  const [head, ...rest] = s.split(',');
  const reverse = head.endsWith('-r');
  const mode = head.replace(/-r$/, '');
  const items = rest.filter(Boolean).map(decStr);
  const rev = reverse ? { reverse: true } : {};
  if (mode === 'line' && items[0]) return { mode: 'line', line: items[0], ...(items[1] ? { graph: items[1] } : {}), ...rev };
  if (mode === 'sec' && items.length) return { mode: 'sections', sections: items, ...rev };
  if (mode === 'route' && items.length >= 2) return { mode: 'route', from: items[0], to: items[1], ...rev };
  return null;
}

// ---------- map view: zoom/lat/lng ----------
const round = (x: number, n: number) => String(Number(x.toFixed(n)));
export const encView = (v: MapView) => `${round(v.zoom, 2)}/${round(v.lat, 5)}/${round(v.lng, 5)}`;
export function decView(s: string): MapView | null {
  const m = s.split('/').map(Number);
  return m.length === 3 && m.every(Number.isFinite) ? { zoom: m[0], lat: m[1], lng: m[2] } : null;
}

// ---------- the whole hash ----------
const DETAIL_TABS: DetailTab[] = ['properties', 'describe', 'graph'];
const SIDE_TABS: SideTab[] = ['filters', 'results'];

/** URL-backed state with the value it has when its parameter is absent (applied on Back / Forward). */
export const URL_DEFAULTS: Partial<AppState> = {
  classes: [], filters: {}, graphs: [], q: '', limitToView: false, orm: false, layers: [], qa: false, allZoom: [],
  selected: null, detailTab: 'properties', describe: null, sideTab: 'filters',
  colorBy: null, colorHidden: [], profileOpen: false, profile: null, profileRows: null, vehicle: null, vehicleOpen: false,
  lens: null,
};

/** The state a hash describes; only the parameters present (see URL_DEFAULTS for the rest). */
export function parseHash(hash: string): Partial<AppState> {
  const p = new URLSearchParams(hash.replace(/^#/, ''));
  const get = (k: string) => p.get(k) ?? '';
  const out: Partial<AppState> = {};
  if (get('env')) out.env = get('env');
  if (get('vw')) out.lens = get('vw');
  const view = decView(get('map'));
  if (view) out.view = view;
  if (get('c')) out.classes = get('c').split(',').filter(Boolean);
  if (get('f')) out.filters = decFilters(get('f'));
  if (get('q')) out.q = get('q');
  if (get('ds')) out.graphs = get('ds').split(/[\s,]+/).filter(Boolean).map(decStr);
  if (get('v') === '1') out.limitToView = true;
  if (get('orm') === '1') out.orm = true;
  if (get('lyr')) out.layers = get('lyr').split(',').filter(Boolean);
  if (get('qa') === '1') out.qa = true;
  if (get('az')) out.allZoom = get('az').split(',').filter(Boolean);
  if (get('sel')) {
    out.selected = decStr(get('sel'));
    const tab = get('tab') as DetailTab;
    if (DETAIL_TABS.includes(tab)) out.detailTab = tab;
    const nav = decList(get('nav'));
    const dir = get('dir') === 'in' ? 'in' : 'out';
    if (nav.length || dir === 'in') out.describe = { for: out.selected, path: nav, dir };
  }
  const side = get('side');
  if (SIDE_TABS.includes(side as SideTab)) out.sideTab = side as SideTab;
  // legacy: the vehicle check was a side tab (side=vehicle); it is now a map tool panel
  if (get('vp') === '1' || side === 'vehicle') out.vehicleOpen = true;
  if (get('col')) {
    out.colorBy = get('col');
    const h = get('colh');
    // legacy: space separated; now "," separated items (spaces inside items are escaped)
    if (h) out.colorHidden = !h.includes(',') && h.includes(' ') ? h.split(' ').filter(Boolean) : decList(h);
  }
  if (p.has('lp')) {
    out.profileOpen = true;
    out.profile = get('lp') ? decProfile(get('lp')) : null;
    if (get('lpr')) out.profileRows = get('lpr') === '-' ? [] : get('lpr').split(',').filter(Boolean);
  }
  if (p.has('veh')) {
    out.vehicle = { name: get('vehn').trim().slice(0, 200) || 'Shared vehicle', values: decVehicle(get('veh')) };
  }
  return out;
}

/** Hash parameters for a state, in a fixed order. */
export function hashParams(s: AppState): [string, string][] {
  const p: [string, string][] = [];
  if (s.env) p.push(['env', s.env]);
  p.push(['map', encView(s.view)]);
  if (s.lens) p.push(['vw', s.lens]);
  if (s.classes.length) p.push(['c', s.classes.join(',')]);
  const f = encFilters(s.filters);
  if (f) p.push(['f', f]);
  if (s.q) p.push(['q', s.q]);
  if (s.graphs.length) p.push(['ds', encList(s.graphs)]);
  if (s.limitToView) p.push(['v', '1']);
  if (s.selected) {
    p.push(['sel', encStr(s.selected)]);
    if (s.detailTab !== 'properties') p.push(['tab', s.detailTab]);
    const d = describeOf(s);
    if (d.path.length) p.push(['nav', encList(d.path)]);
    if (d.dir === 'in') p.push(['dir', 'in']);
  }
  if (s.sideTab !== 'filters') p.push(['side', s.sideTab]);
  if (s.colorBy) {
    p.push(['col', s.colorBy]);
    if (s.colorHidden.length) p.push(['colh', encList(s.colorHidden, true)]);
  }
  if (s.qa) p.push(['qa', '1']);
  if (s.allZoom.length) p.push(['az', s.allZoom.join(',')]);
  if (s.profileOpen) {
    p.push(['lp', s.profile ? encProfile(s.profile) : '']);
    if (s.profileRows) p.push(['lpr', s.profileRows.length ? s.profileRows.join(',') : '-']);
  }
  if (s.vehicle) {
    p.push(['veh', encVehicle(s.vehicle.values)]);
    if (s.vehicle.name) p.push(['vehn', s.vehicle.name]);
  }
  if (s.vehicleOpen) p.push(['vp', '1']);
  if (s.orm) p.push(['orm', '1']);
  if (s.layers.length) p.push(['lyr', s.layers.join(',')]);
  return p;
}

/** The Describe navigation that applies to the selected feature. */
export function describeOf(s: AppState): DescribeNav {
  return s.describe && s.describe.for === s.selected ? s.describe : { for: s.selected ?? '', path: [], dir: 'out' };
}

// Percent-encode a value, but keep the characters that are safe and readable in a fragment.
const encUrl = (v: string) => encodeURIComponent(v)
  .replace(/%2F/g, '/').replace(/%3A/g, ':').replace(/%2C/g, ',').replace(/%3B/g, ';').replace(/%40/g, '@').replace(/%20/g, '+');

export const buildHash = (s: AppState) => '#' + hashParams(s).map(([k, v]) => `${k}=${encUrl(v)}`).join('&');

/** What Back should step through: a change here pushes a history entry, anything else replaces it. */
function navSignature(s: AppState): string {
  const d = s.selected ? describeOf(s) : null;
  return JSON.stringify([s.env, s.lens, s.classes, encFilters(s.filters), s.graphs, s.q, s.selected, s.selected ? s.detailTab : null,
    d && [d.path, d.dir], s.colorBy, s.qa, s.allZoom, s.profileOpen, s.profile, s.vehicleOpen, s.vehicle?.name ?? null]);
}

// ---------- history ----------
/** Several meaningful changes within this time make a single history entry (e.g. ticking a few values). */
const COALESCE_MS = 1500;
let currentNav: string | null = null;
let lastPush = 0;

/** Writes the state to the URL: pushState for navigation, replaceState for the rest. */
export function writeUrl(s: AppState, forceReplace = false) {
  if (!s.env) return; // not started yet: keep the incoming link as it is
  const hash = buildHash(s);
  const nav = navSignature(s);
  const push = !forceReplace && currentNav !== null && nav !== currentNav && Date.now() - lastPush > COALESCE_MS;
  if (hash !== location.hash) {
    if (push) { history.pushState(null, '', hash); lastPush = Date.now(); } else history.replaceState(null, '', hash);
  }
  currentNav = nav;
}

let applying = false;
/** True while the store is being set from the URL (modules then keep the link's view, e.g. no zoom-to). */
export const isApplyingUrl = () => applying;

/** Sets the store from the URL through `apply`; Back / Forward / an edited hash add no history entry. */
export function fromUrl(apply: () => void, s: () => AppState) {
  applying = true;
  try { apply(); } finally { applying = false; }
  currentNav = navSignature(s());
}

/** The full link to the current view. */
export function currentLink(s: AppState): string {
  writeUrl(s, true);
  return location.href;
}

