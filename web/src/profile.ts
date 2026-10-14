// Line profile: the sections of a national line, of sections picked on the map, or of
// a route between two operational points, laid out along cumulative distance with one
// row per parameter (taken from the tracks of each section). POST /api/profile.
import {
  api, isAbort, type FacetMeta, type LineHit, type ProfileCell, type ProfileParam, type ProfileResponse,
  type ProfileSection, type ProfileSpec, type SearchItem,
} from './api';
import { fitBbox, onProfileHover, resizeMap, setClickHook, setProfileData, setProfileHover, type ClickedFeature } from './map';
import { changed, debounce, fmt, h, store, unitLabel } from './state';

type Mode = ProfileSpec['mode'];
interface Pick { iri: string; label: string }

const SECTION_CLASS = 'section-of-line';
const OP_CLASS = 'operational-point';
/** Classes whose facets can be strip rows (a section's own facets and its tracks'). */
const ROW_CLASSES = new Set(['section-of-line', 'running-track', 'track']);
const PARAMS_KEY = 'atlas-profile-rows';
const MODES: [Mode, string, string][] = [
  ['line', 'National line', 'Every section of a national line'],
  ['sections', 'Sections', 'Sections of line you click on the map'],
  ['route', 'From → to', 'The shortest chain of sections between two operational points'],
];
// Categorical colours for terms rows; a sequential ramp (low = light) for numbers.
const PALETTE = ['#4e79a7', '#f28e2b', '#59a14f', '#e15759', '#76b7b2', '#edc948', '#b07aa1', '#ff9da7', '#9c755f', '#bab0ac'];
const RAMP = ['#f7fcb9', '#addd8e', '#41ab5d', '#1d6f8f', '#253494'];
const GAP_W = 26;
const MIN_W = 14;
const MIN_W_PHONE = 44; // readable, tappable cells; the strip scrolls sideways
const PAD_L = 10, PAD_R = 120; // room for the first and last (rotated) operational point label
const km1 = new Intl.NumberFormat('en', { maximumFractionDigits: 1 });

const panel = document.getElementById('profile') as HTMLElement;
const toggleBtn = document.getElementById('profile-btn') as HTMLButtonElement;

let mode: Mode = 'line';
const lastSpec: Partial<Record<Mode, ProfileSpec>> = {};
let data: ProfileResponse | null = null;
let error: string | null = null;
let loading = false;
let ctl: AbortController | null = null;
let zoom = 1;
let hovered: number | null = null;
let lastPointer = 'mouse';
let fittedFor = '';
let routeDraft: { from?: Pick; to?: Pick } = {};
let lineHits: LineHit[] = [];
let opHits: SearchItem[] = [];
let params: string[] | null = readParams();
/** Elements of each strip column (section i), for highlighting. */
let columns: HTMLElement[][] = [];

// ---------- persisted row choice (per viewer) ----------
function readParams(): string[] | null {
  try {
    const v = JSON.parse(localStorage.getItem(PARAMS_KEY) ?? 'null');
    return Array.isArray(v) && v.every((x) => typeof x === 'string') ? v : null;
  } catch { return null; }
}
function writeParams(v: string[] | null) {
  try { if (v) localStorage.setItem(PARAMS_KEY, JSON.stringify(v)); else localStorage.removeItem(PARAMS_KEY); } catch { /* private mode */ }
}

// ---------- panel skeleton ----------
const lineList = h('datalist', { id: 'profile-lines' });
const opList = h('datalist', { id: 'profile-ops' });
const lineInput = h('input', { type: 'search', list: 'profile-lines', class: 'profile-input', placeholder: 'National line, e.g. 50A',
  'aria-label': 'National line identifier', autocomplete: 'off' });
const graphSelect = h('select', { class: 'profile-input', 'aria-label': 'Dataset of the line', hidden: true });
const fromInput = h('input', { type: 'search', list: 'profile-ops', class: 'profile-input', placeholder: 'From operational point',
  'aria-label': 'From operational point', autocomplete: 'off' });
const toInput = h('input', { type: 'search', list: 'profile-ops', class: 'profile-input', placeholder: 'To operational point',
  'aria-label': 'To operational point', autocomplete: 'off' });
const form = h('div', { class: 'profile-form' });
const summary = h('div', { class: 'profile-summary', 'aria-live': 'polite' });
const body = h('div', { class: 'profile-body' });
const tip = h('div', { class: 'profile-tip', role: 'tooltip', hidden: true });
const modeSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Profile of' });

function iconBtn(title: string, svg: string, onclick: () => void, extra = '') {
  const b = h('button', { class: `icon-btn profile-act ${extra}`, type: 'button', title, 'aria-label': title, onclick });
  b.innerHTML = svg;
  return b;
}
const ICON = {
  rows: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M2 4h12M2 8h12M2 12h12" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><circle cx="5" cy="4" r="1.6" fill="currentColor"/><circle cx="10" cy="8" r="1.6" fill="currentColor"/><circle cx="6.5" cy="12" r="1.6" fill="currentColor"/></svg>',
  reverse: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M2 5h11l-3-3M14 11H3l3 3" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  csv: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M8 2v8m-3.5-3.5L8 10l3.5-3.5M3 13.5h10" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  map: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M2 3.5 6 2l4 1.5L14 2v10.5L10 14l-4-1.5L2 14z M6 2v10.5M10 3.5V14" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
  minus: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M3.5 8h9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  plus: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M3.5 8h9M8 3.5v9" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
};
const actions = h('div', { class: 'profile-actions' },
  iconBtn('Narrower strip', ICON.minus, () => setZoom(zoom / 1.5)),
  iconBtn('Wider strip', ICON.plus, () => setZoom(zoom * 1.5)),
  iconBtn('Zoom the map to the profile', ICON.map, () => fitMap()),
  iconBtn('Reverse the direction', ICON.reverse, () => {
    const p = store.get().profile;
    if (p) store.set({ profile: { ...p, reverse: !p.reverse } });
  }),
  iconBtn('Choose the rows (parameters)', ICON.rows, () => openRows()),
  iconBtn('Download the strip as CSV', ICON.csv, () => downloadCsv()),
  h('button', { class: 'icon-btn profile-close', type: 'button', 'aria-label': 'Close the line profile', title: 'Close',
    onclick: () => store.set({ profileOpen: false }) }, '×'));

function skeleton() {
  modeSeg.replaceChildren(...MODES.map(([m, label, title]) => h('button', {
    class: 'seg-btn', type: 'button', 'data-mode': m, title, 'aria-pressed': String(mode === m), onclick: () => setMode(m),
  }, label)));
  panel.replaceChildren(
    h('button', { class: 'sheet-handle', type: 'button', 'aria-label': 'Resize the line profile',
      onclick: () => { panel.classList.toggle('expanded'); afterLayout(); } }, h('span')),
    h('div', { class: 'profile-head' }, h('h2', {}, 'Line profile'), modeSeg, h('span', { class: 'spacer' }), actions),
    form, summary, body, tip, lineList, opList);
}

// ---------- mode + spec ----------
function setMode(m: Mode) {
  if (m === mode) return;
  const cur = store.get().profile;
  if (cur) lastSpec[cur.mode] = cur;
  mode = m;
  for (const b of modeSeg.querySelectorAll<HTMLButtonElement>('.seg-btn')) b.setAttribute('aria-pressed', String(b.dataset.mode === m));
  data = null; error = null;
  store.set({ profile: lastSpec[m] ?? null });
  renderAll();
  syncClickHook();
}

const setSpec = (spec: ProfileSpec | null) => store.set({ profile: spec });

function renderForm() {
  const spec = store.get().profile;
  if (mode === 'line') {
    if (spec?.mode === 'line' && document.activeElement !== lineInput) lineInput.value = spec.line;
    const alts = data?.line?.alternatives ?? [];
    graphSelect.hidden = alts.length < 2;
    graphSelect.replaceChildren(...alts.map((a) => h('option', { value: a.graph_iri, selected: a.graph_iri === data?.line?.graph_iri },
      `${a.provider ?? a.graph_iri.split('/').pop()} (${a.sections} sections)`)));
    form.replaceChildren(lineInput, graphSelect,
      h('span', { class: 'muted small profile-hint' }, 'Or open a section of line on the map and choose “Line profile”.'));
  } else if (mode === 'sections') {
    const n = spec?.mode === 'sections' ? spec.sections.length : 0;
    form.replaceChildren(...[
      h('span', { class: 'profile-hint' }, h('strong', {}, n ? `${n} section${n === 1 ? '' : 's'} selected. ` : ''),
        'Click sections of line on the map to add or remove them; they are put in order and gaps are flagged.'),
      n ? h('button', { class: 'btn btn-small', type: 'button', onclick: () => {
        if (spec?.mode === 'sections') setSpec(spec.sections.length > 1 ? { ...spec, sections: spec.sections.slice(0, -1) } : null);
      } }, 'Undo') : null,
      n ? h('button', { class: 'btn btn-small', type: 'button', onclick: () => setSpec(null) }, 'Clear') : null,
    ].filter((x): x is HTMLElement => x !== null));
  } else {
    if (document.activeElement !== fromInput) fromInput.value = routeDraft.from?.label ?? '';
    if (document.activeElement !== toInput) toInput.value = routeDraft.to?.label ?? '';
    form.replaceChildren(fromInput, h('span', { class: 'muted', 'aria-hidden': 'true' }, '→'), toInput,
      h('span', { class: 'muted small profile-hint' }, 'Type a name, or click two operational points on the map.'));
  }
}

const fetchLines = debounce(async () => {
  const env = store.get().env;
  try {
    lineHits = await api.lines(env, lineInput.value.trim());
    lineList.replaceChildren(...lineHits.map((l) => h('option', { value: l.line },
      `${l.provider ?? ''} · ${l.sections} section${l.sections === 1 ? '' : 's'}${l.length_km ? ` · ${km1.format(l.length_km)} km` : ''}`)));
  } catch (e) { console.warn('lines', e); }
}, 200);

function commitLine() {
  const v = lineInput.value.trim();
  if (!v) return;
  const hit = lineHits.find((l) => l.line === v);
  setSpec({ mode: 'line', line: v, ...(hit ? { graph: hit.graph_iri } : {}) });
}

const opKey = (it: SearchItem, dup: boolean) => (dup ? `${it.label} (${it.id})` : it.label);
const fetchOps = debounce(async (q: string) => {
  const s = store.get();
  try {
    const r = await api.search({ env: s.env, classes: [OP_CLASS], filters: {}, q }, 12, 0);
    opHits = r.items;
    const counts = new Map<string, number>();
    for (const it of opHits) counts.set(it.label, (counts.get(it.label) ?? 0) + 1);
    opList.replaceChildren(...opHits.map((it) => h('option', { value: opKey(it, (counts.get(it.label) ?? 0) > 1) })));
  } catch (e) { console.warn('ops', e); }
}, 200);

function commitOp(which: 'from' | 'to', input: HTMLInputElement) {
  const v = input.value.trim();
  const counts = new Map<string, number>();
  for (const it of opHits) counts.set(it.label, (counts.get(it.label) ?? 0) + 1);
  const it = opHits.find((x) => opKey(x, (counts.get(x.label) ?? 0) > 1) === v) ?? opHits.find((x) => x.label.toLowerCase() === v.toLowerCase());
  if (!it) return;
  routeDraft[which] = { iri: it.iri, label: it.label };
  input.value = it.label;
  commitRoute();
}

function commitRoute() {
  const { from, to } = routeDraft;
  if (from && to) setSpec({ mode: 'route', from: from.iri, to: to.iri });
  else renderStatus();
}

// ---------- map clicks ----------
async function resolve(f: ClickedFeature): Promise<{ iri: string; label: string } | null> {
  try {
    if (f.iri && f.class !== OP_CLASS) return { iri: f.iri, label: '' };
    const ft = f.iri ? await api.feature(store.get().env, f.iri) : f.id != null ? await api.featureById(store.get().env, f.id) : null;
    return ft ? { iri: ft.iri, label: ft.label } : null;
  } catch { return null; }
}

function onMapClick(f: ClickedFeature): boolean {
  if (mode === 'sections' && f.class === SECTION_CLASS) {
    resolve(f).then((r) => {
      if (!r) return;
      const cur = store.get().profile;
      const list = cur?.mode === 'sections' ? cur.sections : [];
      const next = list.includes(r.iri) ? list.filter((x) => x !== r.iri) : [...list, r.iri];
      setSpec(next.length ? { mode: 'sections', sections: next, ...(cur?.reverse ? { reverse: true } : {}) } : null);
    });
    return true;
  }
  if (mode === 'route' && f.class === OP_CLASS) {
    resolve(f).then((r) => {
      if (!r) return;
      if (!routeDraft.from || routeDraft.to) routeDraft = { from: r };
      else routeDraft.to = r;
      renderForm();
      commitRoute();
    });
    return true;
  }
  return false;
}

function syncClickHook() {
  setClickHook(store.get().profileOpen && mode !== 'line' ? onMapClick : null, mode === 'sections' ? SECTION_CLASS : OP_CLASS);
}

/** Opens the profile of a national line (from the detail panel of a section). */
export function showLineProfile(line: string, graph?: string) {
  mode = 'line';
  store.set({ profileOpen: true, profile: { mode: 'line', line, ...(graph ? { graph } : {}) } });
  renderForm();
  for (const b of modeSeg.querySelectorAll<HTMLButtonElement>('.seg-btn')) b.setAttribute('aria-pressed', String(b.dataset.mode === mode));
}

// ---------- loading ----------
async function load() {
  const s = store.get();
  const spec = s.profile;
  ctl?.abort();
  if (!s.profileOpen || !spec || !s.env) {
    data = null; error = null; loading = false;
    renderAll();
    return;
  }
  ctl = new AbortController();
  loading = true;
  renderStatus();
  try {
    data = await api.profile(s.env, spec, params, ctl.signal);
    error = null;
  } catch (e) {
    if (isAbort(e)) return;
    data = null;
    error = (e as Error).message;
  }
  loading = false;
  if (data && spec.mode === 'route' && (!routeDraft.from || !routeDraft.to)) {
    const pts = data.points;
    if (pts.length) routeDraft = { from: { iri: spec.from, label: pts[0].label ?? spec.from }, to: { iri: spec.to, label: pts[pts.length - 1].label ?? spec.to } };
  }
  renderAll();
  // Zoom the map to a newly chosen line or route (not on every section click).
  const key = spec.mode === 'sections' ? '' : JSON.stringify({ ...spec, reverse: false });
  if (data && key && key !== fittedFor) { fittedFor = key; fitMap(); }
}
const loadSoon = debounce(load, 120);

function fitMap() {
  const xs: number[] = [], ys: number[] = [];
  const walk = (c: unknown): void => {
    if (Array.isArray(c) && typeof c[0] === 'number') { xs.push(c[0] as number); ys.push(c[1] as number); } else if (Array.isArray(c)) c.forEach(walk);
  };
  for (const s of data?.sections ?? []) if (s.geometry && 'coordinates' in s.geometry) walk(s.geometry.coordinates);
  if (xs.length) fitBbox([Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)], 13);
}

function updateMap() {
  const secs = data?.sections ?? [];
  setProfileData(
    { type: 'FeatureCollection', features: secs.flatMap((s, i) => s.geometry ? [{ type: 'Feature' as const, geometry: s.geometry, properties: { i } }] : []) },
    { type: 'FeatureCollection', features: (data?.points ?? []).flatMap((p) => p.lon != null && p.lat != null
      ? [{ type: 'Feature' as const, geometry: { type: 'Point' as const, coordinates: [p.lon, p.lat] }, properties: { label: p.label } }] : []) });
}

// ---------- rendering ----------
function renderAll() {
  renderForm();
  renderStatus();
  renderStrip();
  updateMap();
}

function title(d: ProfileResponse): string {
  if (d.mode === 'line' && d.line) return `Line ${d.line.id}${d.line.provider ? ` · ${d.line.provider}` : ''}`;
  if (d.mode === 'route' && d.points.length) return `${d.points[0].label} → ${d.points[d.points.length - 1].label}`;
  return `${d.sections.length} selected section${d.sections.length === 1 ? '' : 's'}`;
}

function renderStatus() {
  const parts: (Node | string)[] = [];
  if (loading) parts.push(h('span', { class: 'loading-text muted' }, 'Loading…'));
  else if (error) parts.push(h('span', { class: 'error' }, error));
  else if (data) {
    parts.push(h('strong', {}, title(data)),
      ` · ${km1.format(data.length_km)} km · ${data.sections.length} section${data.sections.length === 1 ? '' : 's'}`);
    if (data.gaps) parts.push(' · ', h('span', { class: 'gap-note' }, `${data.gaps} gap${data.gaps === 1 ? '' : 's'}`));
    if (data.missing.length) parts.push(' · ', h('span', { class: 'error' }, `${data.missing.length} not found`));
    if (data.truncated) parts.push(' · ', h('span', { class: 'error' }, 'cut off (too many sections)'));
  } else if (mode === 'route' && routeDraft.from && !routeDraft.to) parts.push(h('span', { class: 'muted' }, `From ${routeDraft.from.label}: now pick where to go.`));
  summary.replaceChildren(...parts);
}

function emptyText(): string {
  if (mode === 'line') return 'Choose a national line to see its sections and parameters along the line.';
  if (mode === 'sections') return 'No sections yet: click sections of line on the map.';
  return 'Choose where to start and where to go.';
}

const lum = (hex: string) => {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const textOn = (hex: string) => (lum(hex) > 0.35 ? '#111' : '#fff');

function ramp(t: number): string {
  const x = Math.max(0, Math.min(1, t)) * (RAMP.length - 1);
  const i = Math.min(RAMP.length - 2, Math.floor(x));
  const f = x - i;
  const a = parseInt(RAMP[i].slice(1), 16), b = parseInt(RAMP[i + 1].slice(1), 16);
  const mix = (sh: number) => Math.round(((a >> sh) & 255) * (1 - f) + ((b >> sh) & 255) * f);
  return '#' + [16, 8, 0].map((sh) => mix(sh).toString(16).padStart(2, '0')).join('');
}

interface RowScale { min: number; max: number; colors: Map<string, string>; order: { value: string; label: string }[] }

function scaleFor(p: ProfileParam, secs: ProfileSection[]): RowScale {
  let min = Infinity, max = -Infinity;
  const km = new Map<string, { label: string; km: number }>();
  for (const s of secs) {
    const c = s.cells[p.key];
    if (!c) continue;
    if (c.min != null) min = Math.min(min, c.min);
    if (c.max != null) max = Math.max(max, c.max);
    for (const v of c.values ?? []) {
      const k = String(v.value);
      const cur = km.get(k) ?? { label: v.label, km: 0 };
      cur.km += s.length_km || 0.001;
      km.set(k, cur);
    }
  }
  const order = [...km.entries()].sort((a, b) => b[1].km - a[1].km).map(([value, x]) => ({ value, label: x.label }));
  const colors = new Map<string, string>();
  order.forEach((o, i) => colors.set(o.value, p.kind === 'boolean' ? (o.value === 'true' ? '#59a14f' : '#c9ced6') : PALETTE[i % PALETTE.length]));
  return { min, max, colors, order };
}

const num = (v: number, p: ProfileParam) => fmt.format(v) + (p.unit ? ` ${unitLabel(p.unit)}` : '');

/** Cell text for the strip and the CSV. */
function cellText(p: ProfileParam, c: ProfileCell | undefined, withUnit = false): string {
  if (!c) return '';
  if (c.min != null && c.max != null) {
    const f = (v: number) => (withUnit ? num(v, p) : fmt.format(v));
    return c.min === c.max ? f(c.min) : `${fmt.format(c.min)}–${f(c.max)}`;
  }
  return (c.values ?? []).map((v) => v.label).join('; ');
}

function cellStyle(p: ProfileParam, c: ProfileCell | undefined, sc: RowScale): { bg: string; fg: string; text: string } | null {
  if (!c) return null;
  if (c.min != null && c.max != null) {
    const t = (v: number) => (sc.max > sc.min ? (v - sc.min) / (sc.max - sc.min) : 0.6);
    const lo = ramp(t(c.min)), hi = ramp(t(c.max));
    return { bg: c.min === c.max ? lo : `linear-gradient(to bottom, ${hi} 0 50%, ${lo} 50% 100%)`, fg: textOn(lo), text: cellText(p, c) };
  }
  const vals = c.values ?? [];
  if (!vals.length) return null;
  const cols = vals.map((v) => sc.colors.get(String(v.value)) ?? '#999');
  const step = 100 / cols.length;
  const bg = cols.length === 1 ? cols[0] : `linear-gradient(to bottom, ${cols.map((col, i) => `${col} ${i * step}% ${(i + 1) * step}%`).join(', ')})`;
  return { bg, fg: textOn(cols[0]), text: vals[0].label + (vals.length > 1 ? ` +${vals.length - 1}` : '') };
}

function renderStrip() {
  columns = [];
  hideTip();
  if (!data || !data.sections.length) {
    const msg = error ? '' : data ? 'No sections found.' : emptyText();
    body.replaceChildren(h('p', { class: 'muted profile-empty' }, msg));
    return;
  }
  const d = data;
  const phone = window.matchMedia('(max-width: 760px)').matches;
  const labelW = phone ? 96 : 176;
  const avail = Math.max(240, body.clientWidth - labelW - PAD_L - 24);
  const total = d.sections.reduce((t, s) => t + (s.length_km || 0), 0) || d.sections.length;
  const per = Math.max(0.02, (avail - d.gaps * GAP_W) / total) * zoom;
  const xs: { x: number; w: number; gap: number | null }[] = [];
  let x = PAD_L;
  d.sections.forEach((s, i) => {
    let gap: number | null = null;
    if (i && s.gap_before) { gap = x; x += GAP_W; }
    const w = Math.max(phone ? MIN_W_PHONE : MIN_W, (s.length_km || 0) * per);
    xs.push({ x, w, gap });
    x += w;
  });
  const width = x + PAD_R;
  columns = d.sections.map(() => []);

  const row = (cls: string, label: string | Node, children: Node[], titleText?: string) =>
    h('div', { class: `strip-row ${cls}` },
      h('div', { class: 'strip-label', style: `width:${labelW}px`, title: titleText ?? (typeof label === 'string' ? label : '') }, label),
      h('div', { class: 'strip-lane', style: `width:${width}px` }, ...children));
  const gaps = (withText = false) => xs.filter((p) => p.gap != null).map((p) => h('div', {
    class: 'strip-gap', style: `left:${p.gap}px;width:${GAP_W}px`, title: 'Gap: these sections are not connected',
  }, withText ? '⋯' : ''));

  // operational points and kilometres, at section boundaries
  const marks: { x: number; label: string; km: number }[] = [];
  let pi = 0;
  d.sections.forEach((s, i) => {
    if (i === 0 || s.gap_before) { const p = d.points[pi++]; if (p) marks.push({ x: xs[i].x, label: p.label ?? '?', km: p.km }); }
    const p = d.points[pi++];
    if (p) marks.push({ x: xs[i].x + xs[i].w, label: p.label ?? '?', km: p.km });
  });
  let lastLabel = -1e9, lastKm = -1e9;
  const opEls: Node[] = [], kmEls: Node[] = [];
  for (const m of marks) {
    const showLabel = m.x - lastLabel >= 15;
    if (showLabel) lastLabel = m.x;
    opEls.push(h('div', { class: 'op-tick', style: `left:${m.x}px`, title: `${m.label} · km ${fmt.format(m.km)}` }));
    if (showLabel) opEls.push(h('div', { class: 'op-label', style: `left:${m.x}px`, title: `${m.label} · km ${fmt.format(m.km)}` }, m.label));
    if (m.x - lastKm >= 46) { lastKm = m.x; kmEls.push(h('div', { class: 'km-label', style: `left:${m.x}px` }, km1.format(m.km))); }
  }

  const sectionCells = d.sections.map((s, i) => {
    const el = h('div', {
      class: 'cell cell-section' + (s.length_estimated ? ' estimated' : ''), 'data-i': String(i),
      style: `left:${xs[i].x}px;width:${xs[i].w}px`,
    }, xs[i].w > 34 ? km1.format(s.length_km) : '');
    columns[i].push(el);
    return el;
  });

  const rows: HTMLElement[] = [
    row('strip-ops', 'Operational points', opEls),
    row('strip-km', 'km', kmEls, 'Cumulative distance along the profile (km)'),
    row('strip-sections', 'Sections (km)', [...gaps(true), ...sectionCells], 'Sections of line; width follows length (short ones get a minimum width)'),
  ];
  const legends: HTMLElement[] = [];
  for (const p of d.params) {
    const sc = scaleFor(p, d.sections);
    const cells = d.sections.map((s, i) => {
      const st = cellStyle(p, s.cells[p.key], sc);
      const el = h('div', {
        class: 'cell' + (st ? '' : ' empty'), 'data-i': String(i),
        style: `left:${xs[i].x}px;width:${xs[i].w}px;` + (st ? `background:${st.bg};color:${st.fg}` : ''),
      }, st && xs[i].w > 30 ? h('span', {}, st.text) : '');
      columns[i].push(el);
      return el;
    });
    const label = h('span', {}, p.label, p.unit ? h('small', { class: 'muted' }, ` (${unitLabel(p.unit)})`) : null);
    rows.push(row('strip-param', label, [...gaps(), ...cells], p.label));
    // legend
    if (p.kind === 'range' && Number.isFinite(sc.min)) {
      legends.push(h('div', { class: 'legend-row' }, h('span', { class: 'legend-name' }, p.label),
        h('span', { class: 'legend-item' }, num(sc.min, p)),
        h('span', { class: 'legend-ramp', style: `background:linear-gradient(to right, ${RAMP.join(', ')})` }),
        h('span', { class: 'legend-item' }, num(sc.max, p)),
        h('span', { class: 'muted small' }, ' · two colours = tracks differ (top: highest)')));
    } else if (sc.order.length) {
      legends.push(h('div', { class: 'legend-row' }, h('span', { class: 'legend-name' }, p.label),
        ...sc.order.slice(0, 12).map((o) => h('span', { class: 'legend-item' },
          h('span', { class: 'swatch', style: `background:${sc.colors.get(o.value)}` }), o.label)),
        sc.order.length > 12 ? h('span', { class: 'muted small' }, `+${sc.order.length - 12} more`) : null));
    }
  }
  if (!d.params.length) rows.push(h('p', { class: 'muted small profile-empty' }, 'No parameter rows: choose some with the rows button.'));
  const strip = h('div', { class: 'strip', style: `width:${labelW + width + 8}px` }, ...rows);
  strip.addEventListener('pointerdown', (e) => { lastPointer = e.pointerType; });
  strip.addEventListener('pointermove', (e) => {
    if (e.pointerType !== 'mouse') return;
    const i = cellIndex(e.target);
    if (i !== hovered) setHover(i, e);
    else if (i != null) placeTip(e.clientX, e.clientY);
  });
  strip.addEventListener('pointerleave', (e) => { if (e.pointerType === 'mouse') setHover(null); });
  strip.addEventListener('click', (e) => {
    const i = cellIndex(e.target);
    if (i == null) return;
    // touch: the first tap shows the values, the second opens the section
    if (lastPointer !== 'mouse' && hovered !== i) { setHover(i, e as MouseEvent); return; }
    store.set({ selected: d.sections[i].iri, detailTab: 'properties' });
  });
  body.replaceChildren(strip, h('div', { class: 'strip-legend', style: `max-width:${body.clientWidth - 8}px` }, ...legends));
}

function cellIndex(t: EventTarget | null): number | null {
  const el = (t as HTMLElement | null)?.closest?.('[data-i]') as HTMLElement | null;
  return el ? Number(el.dataset.i) : null;
}

function setHover(i: number | null, ev?: MouseEvent | null) {
  if (hovered != null) for (const el of columns[hovered] ?? []) el.classList.remove('hot');
  hovered = i;
  setProfileHover(i);
  if (i == null || !data?.sections[i]) { hideTip(); return; }
  for (const el of columns[i] ?? []) el.classList.add('hot');
  fillTip(data.sections[i]);
  if (ev) placeTip(ev.clientX, ev.clientY);
  else {
    const el = columns[i]?.[0];
    if (!el) return;
    // from the map: bring the column into view and put the tip above it
    const b = body.getBoundingClientRect(), r = el.getBoundingClientRect();
    if (r.left < b.left + 100 || r.right > b.right) body.scrollLeft += r.left - b.left - b.width / 2 + r.width / 2;
    const r2 = el.getBoundingClientRect();
    placeTip(r2.left + r2.width / 2, r2.top);
  }
}

function fillTip(s: ProfileSection) {
  const d = data!;
  const opLabel = (iri: string | null) => d.points.find((p) => p.iri === iri)?.label ?? '?';
  const lines: Node[] = [
    h('strong', {}, s.label),
    h('div', { class: 'muted' }, `km ${km1.format(s.start_km)}–${km1.format(s.end_km)} · ${km1.format(s.length_km)} km${s.length_estimated ? ' (from the geometry)' : ''}`),
    h('div', { class: 'muted' }, `${opLabel(s.from)} → ${opLabel(s.to)}${s.reversed ? ' (recorded the other way)' : ''}`),
  ];
  if (s.gap_before) lines.push(h('div', { class: 'gap-note' }, `Not connected to the previous section${s.gap_km != null ? ` (${km1.format(s.gap_km)} km apart)` : ''}`));
  const tbl = h('table', {});
  for (const p of d.params) {
    const c = s.cells[p.key];
    const perTrack = s.tracks.filter((t) => t.values[p.key]?.length);
    const differ = new Set(perTrack.map((t) => t.values[p.key].map((v) => v.label).join('; '))).size > 1;
    tbl.append(h('tr', {}, h('th', {}, p.label),
      h('td', {}, differ
        ? h('span', {}, ...perTrack.map((t) => h('div', {}, h('span', { class: 'muted' }, `${t.direction ?? t.label}: `), t.values[p.key].map((v) => v.label).join('; '))))
        : cellText(p, c, true) || '–')));
  }
  lines.push(tbl, h('div', { class: 'muted small' }, `${s.tracks.length} track${s.tracks.length === 1 ? '' : 's'}` +
    (s.tracks.length ? `: ${s.tracks.map((t) => t.direction ?? t.label).join(', ')}` : '') +
    (lastPointer === 'mouse' ? ' · click for details' : ' · tap again for details')));
  tip.replaceChildren(...lines);
  tip.hidden = false;
}

function placeTip(cx: number, cy: number) {
  const w = tip.offsetWidth, ht = tip.offsetHeight;
  const x = Math.min(window.innerWidth - w - 8, Math.max(8, cx + 14));
  const y = cy - ht - 12 >= 8 ? cy - ht - 12 : Math.min(window.innerHeight - ht - 8, cy + 16);
  tip.style.transform = `translate(${x}px, ${y}px)`;
}
function hideTip() { tip.hidden = true; }

function setZoom(z: number) {
  const centre = body.scrollLeft + body.clientWidth / 2;
  const ratio = Math.max(0.5, Math.min(40, z)) / zoom;
  zoom = Math.max(0.5, Math.min(40, z));
  renderStrip();
  body.scrollLeft = centre * ratio - body.clientWidth / 2;
}

// ---------- rows dialog ----------
let rowsDlg: HTMLDialogElement | null = null;

function candidates(): FacetMeta[] {
  const seen = new Set<string>();
  return (store.get().meta?.facets ?? []).filter((f) => ROW_CLASSES.has(f.class) && ['terms', 'range', 'boolean'].includes(f.kind)
    && !seen.has(f.key) && seen.add(f.key));
}

function openRows() {
  if (!rowsDlg) {
    rowsDlg = h('dialog', { class: 'datasets-dialog rows-dialog', 'aria-label': 'Rows of the line profile' });
    rowsDlg.addEventListener('click', (e) => { if (e.target === rowsDlg) rowsDlg!.close(); });
    document.body.append(rowsDlg);
  }
  const current = new Set(data?.params.map((p) => p.key) ?? params ?? []);
  const cands = candidates();
  const filter = h('input', { type: 'search', class: 'facet-filter', placeholder: 'Filter parameters…', 'aria-label': 'Filter parameters' });
  const boxes = cands.map((f) => {
    const box = h('input', { type: 'checkbox', value: f.key, checked: current.has(f.key) });
    return { f, li: h('li', {}, h('label', { class: 'check' }, box, h('span', { class: 'lbl', title: f.label }, f.label),
      h('span', { class: 'count' }, f.class === SECTION_CLASS ? 'section' : 'track'))), box };
  });
  filter.addEventListener('input', () => {
    const q = filter.value.trim().toLowerCase();
    for (const b of boxes) b.li.hidden = !!q && !b.f.label.toLowerCase().includes(q);
  });
  const apply = (v: string[] | null) => { params = v; writeParams(v); rowsDlg!.close(); load(); };
  rowsDlg.replaceChildren(
    h('div', { class: 'dlg-head' }, h('h2', {}, 'Rows of the line profile'),
      h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => rowsDlg!.close() }, '×')),
    h('p', { class: 'muted small pad' }, 'Each row is a facet of the sections or of their tracks. Several tracks per section: numbers show the lowest–highest value, other values all appear (stacked colours). Your choice is remembered in this browser.'),
    h('div', { class: 'pad' }, filter),
    h('ul', { class: 'terms dlg-scroll rows-list' }, ...boxes.map((b) => b.li)),
    h('div', { class: 'dlg-tools rows-actions' },
      h('button', { class: 'btn', type: 'button', onclick: () => apply(null) }, 'Defaults'),
      h('span', { class: 'spacer' }),
      h('button', { class: 'btn', type: 'button', onclick: () => rowsDlg!.close() }, 'Cancel'),
      h('button', { class: 'btn btn-primary', type: 'button', onclick: () => {
        // keep the current order; newly ticked rows go at the end
        const ticked = new Set(boxes.filter((b) => b.box.checked).map((b) => b.f.key));
        apply([...[...current].filter((k) => ticked.has(k)), ...[...ticked].filter((k) => !current.has(k))]);
      } }, 'Apply')),
  );
  rowsDlg.showModal();
}

// ---------- CSV ----------
function csvCell(v: unknown): string {
  const s = v == null ? '' : String(v);
  return /[",;\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function downloadCsv() {
  const d = data;
  if (!d) return;
  const opLabel = (iri: string | null) => d.points.find((p) => p.iri === iri)?.label ?? '';
  const head = ['#', 'Section', 'Section IRI', 'From', 'To', 'Start km', 'End km', 'Length km', 'Gap before (km)', 'Tracks',
    ...d.params.flatMap((p) => p.kind === 'range'
      ? [`${p.label} min${p.unit ? ` [${unitLabel(p.unit)}]` : ''}`, `${p.label} max${p.unit ? ` [${unitLabel(p.unit)}]` : ''}`]
      : [p.label])];
  const rows = d.sections.map((s, i) => [i + 1, s.label, s.iri, opLabel(s.from), opLabel(s.to), s.start_km, s.end_km, s.length_km,
    s.gap_before ? (s.gap_km ?? 'yes') : '', s.tracks.length,
    ...d.params.flatMap((p) => p.kind === 'range' ? [s.cells[p.key]?.min ?? '', s.cells[p.key]?.max ?? ''] : [cellText(p, s.cells[p.key])])]);
  const text = '﻿' + [head, ...rows].map((r) => r.map(csvCell).join(',')).join('\r\n') + '\r\n';
  const name = title(d).replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '').toLowerCase().slice(0, 60) || 'profile';
  const a = h('a', { href: URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' })), download: `rinf-atlas-profile-${name}.csv` });
  document.body.append(a);
  a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
}

// ---------- open / close ----------
function afterLayout() {
  requestAnimationFrame(() => { resizeMap(); if (store.get().profileOpen) renderStrip(); });
}

function applyOpen() {
  const open = store.get().profileOpen;
  panel.hidden = !open;
  document.body.classList.toggle('profile-open', open);
  toggleBtn.setAttribute('aria-pressed', String(open));
  if (!open) { setProfileData({ type: 'FeatureCollection', features: [] }, { type: 'FeatureCollection', features: [] }); setHover(null); }
  syncClickHook();
  afterLayout();
}

export function initProfile() {
  const spec = store.get().profile;
  if (spec) mode = spec.mode;
  skeleton();
  lineInput.addEventListener('input', () => fetchLines());
  lineInput.addEventListener('focus', () => { if (!lineHits.length) fetchLines(); });
  lineInput.addEventListener('change', commitLine);
  lineInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') commitLine(); });
  graphSelect.addEventListener('change', () => {
    const p = store.get().profile;
    if (p?.mode === 'line') setSpec({ ...p, graph: graphSelect.value });
  });
  for (const [which, input] of [['from', fromInput], ['to', toInput]] as const) {
    input.addEventListener('input', () => fetchOps(input.value.trim()));
    input.addEventListener('change', () => commitOp(which, input));
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter') commitOp(which, input); });
  }
  toggleBtn.addEventListener('click', () => store.set({ profileOpen: !store.get().profileOpen }));
  onProfileHover((i) => setHover(i, null));
  window.addEventListener('resize', debounce(() => { if (store.get().profileOpen) renderStrip(); }, 150));
  store.subscribe((s, prev) => {
    if (changed(s, prev, 'env')) { lineHits = []; opHits = []; routeDraft = {}; fittedFor = ''; }
    if (changed(s, prev, 'profileOpen')) applyOpen();
    if (changed(s, prev, 'profile') && s.profile && s.profile.mode !== mode) {
      mode = s.profile.mode;
      for (const b of modeSeg.querySelectorAll<HTMLButtonElement>('.seg-btn')) b.setAttribute('aria-pressed', String(b.dataset.mode === mode));
      syncClickHook();
    }
    if (changed(s, prev, 'profile') && s.profile?.mode === 'route' && prev.profile?.mode === 'route'
      && (s.profile.from !== prev.profile.from || s.profile.to !== prev.profile.to) && routeDraft.from?.iri !== s.profile.from) routeDraft = {};
    if (changed(s, prev, 'profile', 'profileOpen', 'env')) { renderForm(); loadSoon(); }
    else if (changed(s, prev, 'meta') && s.profileOpen && !data) loadSoon();
  });
  applyOpen();
  renderAll();
  if (store.get().profileOpen) loadSoon();
}
