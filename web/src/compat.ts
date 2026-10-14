// Vehicle compatibility: the "Vehicle check" map panel (profiles kept in this browser), the
// green / yellow / red thematic layer of sections of line, its map legend, and the
// explanation shown in the detail panel. Self-contained: its own source and layers.
import type * as maplibregl from 'maplibre-gl';
import type { ExpressionSpecification, GeoJSONSource, MapGeoJSONFeature } from 'maplibre-gl';
import {
  api, isAbort, type CompatCheck, type CompatColour, type CompatExplain, type CompatResponse, type CompatRule,
  type CompatRules, type Feature, type TermCount, type TermValue,
} from './api';
import { addPickableLayer, onMapReady, OVERLAY_BEFORE, setTipNote, TILE_MODE } from './map';
import { changed, debounce, fmt, h, store, unitLabel } from './state';
import * as V from './vehicles';

const SRC = 'compat';
const LAYERS = ['compat-casing', 'compat-line', 'compat-circle'];
const COLOUR: Record<CompatColour, string> = { green: '#12b76a', yellow: '#f5b301', red: '#e5484d' };
const CODE: Record<CompatColour, number> = { green: 0, yellow: 1, red: 2 };
const STATUS_TEXT: Record<CompatColour, string> = {
  green: 'Compatible', yellow: 'Compatible as far as known', red: 'Not compatible',
};
const STATUS_HELP: Record<CompatColour, string> = {
  green: 'at least one running track meets every filled-in characteristic',
  yellow: 'no track fails, but some characteristics could not be checked (no data on the section)',
  red: 'every running track fails at least one filled-in characteristic (an electric vehicle fails on a track without a contact line)',
};
const RESULT_TEXT: Record<CompatCheck['result'], string> = { ok: 'Compatible', unknown: 'No data', fail: 'Not compatible' };
const RESULT_ICON: Record<CompatCheck['result'], string> = { ok: '✓', unknown: '?', fail: '✗' };
export const RULE_HELP = 'A section of line is green when at least one of its running tracks is compatible with every ' +
  'characteristic you filled in, yellow when no track is incompatible but some characteristics could not be checked ' +
  'because the section has no data for them, and red when every track is incompatible on at least one characteristic. ' +
  'A track without a contact line is incompatible with an electric vehicle (energy supply, pantograph, contact wire and ' +
  'current), unless you add “Not electrified” to its energy supply systems for a bi-mode vehicle. ' +
  'Characteristics left empty are not checked.';
const TOP_N = 8;

const pane = document.getElementById('pane-vehicle') as HTMLElement;

let saved = V.loadSaved();
let rules: CompatRules | null = null;
let rulesEnv = '';
let rulesErr: string | null = null;
let result: CompatResponse | null = null;
let resultErr: string | null = null;
let busy = false;
let ctl: AbortController | null = null;
/** feature id (as string) -> colour, from the last result. */
const statusById = new Map<string, CompatColour>();
/** Detail-panel blocks to refresh when the result changes. */
const liveBlocks = new Set<{ el: HTMLElement; feature: Feature }>();
const expandedRules = new Set<string>();
const openRules = new Set<string>();
const needles = new Map<string, string>();
let message: { text: string; error?: boolean } | null = null;
let exportAll = false;

const active = () => saved.profiles.find((p) => p.id === saved.active) ?? saved.profiles[0];
const sectionClass = () => rules?.section_class ?? 'section-of-line';
const persist = () => V.save(saved);
const localName = (iri: string) => iri.replace(/[/#]$/, '').split(/[/#]/).pop() ?? iri;

function valueLabel(v: TermValue, fromServer?: string): string {
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'number') return fmt.format(v);
  const known = V.KNOWN_LABELS[v];
  // The index labels concepts from the ERA SKOS; without it a label is just the local name.
  if (fromServer && (fromServer !== localName(v) || !known)) return fromServer;
  return known ?? localName(v);
}

function ruleValues(r: CompatRule): Map<string, TermCount> {
  return new Map((r.values ?? []).map((v) => [String(v.value), v]));
}

// ---------- computing ----------
function hasFilled(): boolean {
  if (!rules) return false;
  const vals = V.filledValues(active());
  return rules.rules.some((r) => r.key in vals);
}

async function recompute() {
  ctl?.abort();
  const env = store.get().env;
  if (!saved.show || !env || !rules || rulesEnv !== env) { applyResult(null); return; }
  if (!hasFilled()) { result = null; resultErr = null; busy = false; applyResult(null); renderResult(); return; }
  ctl = new AbortController();
  busy = true; resultErr = null; renderStatus();
  try {
    result = await api.compat(env, V.filledValues(active()), ctl.signal);
    busy = false;
    applyResult(result);
  } catch (e) {
    if (isAbort(e)) return;
    busy = false; result = null; resultErr = (e as Error).message;
    applyResult(null);
  }
  renderResult();
}
const recomputeSoon = debounce(recompute, 350);

async function loadRules(env: string) {
  rules = null; rulesErr = null; rulesEnv = '';
  renderPane();
  try {
    const r = await api.compatRules(env);
    if (store.get().env !== env) return;
    rules = r; rulesEnv = env;
    for (const ru of r.rules) if (V.isFilled(active()?.values[ru.key])) openRules.add(ru.key);
    updateSource();
  } catch (e) {
    rulesErr = (e as Error).message;
  }
  renderPane();
  recompute();
}

// ---------- map layer ----------
let mapRef: maplibregl.Map | null = null;

// Data version of the last result. The tile URL carries it, so the coloured tiles
// always come from the same index as the ids in the result: after a sync the map
// reloads them instead of keeping (or the browser serving) tiles from before.
let tilesVersion = '';

function sourceTiles(): string {
  const url = api.tilesUrl({ env: store.get().env, classes: [sectionClass()], filters: {} });
  return tilesVersion ? `${url}&v=${encodeURIComponent(tilesVersion)}` : url;
}

function ensureLayers(m: maplibregl.Map) {
  if (m.getSource(SRC)) return;
  const vis = saved.show ? 'visible' : 'none';
  if (TILE_MODE === 'vector') {
    m.addSource(SRC, { type: 'vector', tiles: [sourceTiles()], minzoom: 0, maxzoom: 14 });
    const st = ['coalesce', ['feature-state', 's'], -1] as ExpressionSpecification;
    const colour = ['match', st, 0, COLOUR.green, 1, COLOUR.yellow, 2, COLOUR.red, 'rgba(0,0,0,0)'] as ExpressionSpecification;
    const width = ['interpolate', ['linear'], ['zoom'], 4, 2.2, 10, 4, 15, 7] as ExpressionSpecification;
    m.addLayer({ id: 'compat-casing', type: 'line', source: SRC, 'source-layer': 'features', layout: { 'line-cap': 'round', 'line-join': 'round', visibility: vis },
      paint: { 'line-color': '#101828', 'line-opacity': ['case', ['>=', st, 0], 0.45, 0] as ExpressionSpecification,
        'line-width': ['interpolate', ['linear'], ['zoom'], 4, 3.6, 10, 6, 15, 10] } }, OVERLAY_BEFORE);
    m.addLayer({ id: 'compat-line', type: 'line', source: SRC, 'source-layer': 'features', layout: { 'line-cap': 'round', 'line-join': 'round', visibility: vis },
      paint: { 'line-color': colour, 'line-width': width } }, OVERLAY_BEFORE);
    addPickableLayer('compat-line');
  } else {
    // Mock / no tile server: sections as points from /api/search, coloured by a property.
    m.addSource(SRC, { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    m.addLayer({ id: 'compat-circle', type: 'circle', source: SRC, layout: { visibility: vis },
      paint: { 'circle-color': ['match', ['get', 'status'], 'green', COLOUR.green, 'yellow', COLOUR.yellow, COLOUR.red] as ExpressionSpecification,
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 4, 4, 10, 7], 'circle-stroke-color': '#101828', 'circle-stroke-width': 1 } }, OVERLAY_BEFORE);
    addPickableLayer('compat-circle');
  }
}

function updateSource() {
  if (!mapRef || TILE_MODE !== 'vector') return;
  const src = mapRef.getSource(SRC) as maplibregl.VectorTileSource | undefined;
  src?.setTiles([sourceTiles()]);
}

function setVisible(on: boolean) {
  if (!mapRef) return;
  for (const id of LAYERS) if (mapRef.getLayer(id)) mapRef.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');
}

let geoCtl: AbortController | null = null;

function applyResult(r: CompatResponse | null) {
  statusById.clear();
  if (r) for (const c of ['green', 'yellow', 'red'] as const) for (const id of r[c]) statusById.set(String(id), c);
  setVisible(saved.show);
  if (r?.version && r.version !== tilesVersion) { tilesVersion = r.version; updateSource(); }
  if (!mapRef) return;
  if (TILE_MODE === 'vector') {
    mapRef.removeFeatureState({ source: SRC, sourceLayer: 'features' });
    for (const [id, c] of statusById) {
      const n = Number(id);
      if (Number.isFinite(n)) mapRef.setFeatureState({ source: SRC, sourceLayer: 'features', id: n }, { s: CODE[c] });
    }
    return;
  }
  const src = mapRef.getSource(SRC) as GeoJSONSource | undefined;
  geoCtl?.abort();
  if (!src) return;
  if (!r) { src.setData({ type: 'FeatureCollection', features: [] }); return; }
  geoCtl = new AbortController();
  api.search({ env: r.env, classes: [sectionClass()], filters: {} }, 5000, 0, geoCtl.signal).then((res) => {
    src.setData({ type: 'FeatureCollection', features: res.items.filter((it) => it.lon != null && statusById.has(String(it.id))).map((it) => ({
      type: 'Feature', geometry: { type: 'Point', coordinates: [it.lon!, it.lat!] },
      properties: { id: it.id, iri: it.iri, class: it.class, label: it.label, status: statusById.get(String(it.id)) },
    })) });
  }).catch((e) => { if (!isAbort(e)) console.warn('compatibility points failed', e); });
}

function tipNote(f: MapGeoJSONFeature): string | null {
  if (!saved.show || f.properties.class !== sectionClass()) return null;
  const c = statusById.get(String(f.properties.id ?? f.id));
  return c ? `${STATUS_TEXT[c]} · ${active()?.name ?? ''}` : null;
}

// ---------- map legend ----------
const legend = h('div', { class: 'compat-legend', hidden: true, role: 'region', 'aria-label': 'Vehicle compatibility legend' });

function renderLegend() {
  legend.hidden = !saved.show;
  if (!saved.show) return;
  const p = active();
  const rows = (['green', 'yellow', 'red'] as const).map((c) => h('li', { title: STATUS_HELP[c] },
    h('span', { class: 'compat-swatch', style: `background:${COLOUR[c]}` }),
    h('span', { class: 'lbl' }, STATUS_TEXT[c]),
    h('span', { class: 'count' }, result ? fmt.format(result.counts[c]) : '')));
  const body = !rules ? h('p', { class: 'muted small' }, rulesErr ? 'Compatibility rules unavailable.' : 'Loading…')
    : !hasFilled() ? h('p', { class: 'muted small' }, 'Fill in at least one characteristic of the vehicle.')
    : resultErr ? h('p', { class: 'error small' }, resultErr)
    : h('ul', { class: 'compat-rows' + (busy ? ' busy' : '') }, ...rows);
  legend.replaceChildren(
    h('div', { class: 'compat-legend-head' },
      h('span', { class: 'compat-legend-name', title: p?.name ?? '' }, p?.name ?? 'Vehicle'),
      h('button', { class: 'btn btn-small compat-legend-edit', type: 'button', title: 'Open the vehicle check panel to edit the profile',
        'aria-expanded': String(store.get().vehicleOpen), 'aria-controls': 'vehicle-panel', onclick: () => setPanelOpen(true) }, 'Edit'),
      h('button', { class: 'icon-btn', type: 'button', title: 'Turn the vehicle check off', 'aria-label': 'Turn the vehicle check off', onclick: () => setShow(false) }, '×')),
    body,
    h('p', { class: 'muted small compat-legend-help' }, 'Sections of line, by their running tracks. ',
      h('button', { class: 'link small', type: 'button', title: RULE_HELP, onclick: () => setPanelOpen(true, true) }, 'How?')));
}

// ---------- panel ----------
const panel = document.getElementById('vehicle-panel') as HTMLElement;
const panelBtn = document.getElementById('vehicle-btn') as HTMLButtonElement;

/** Open or close the vehicle check panel (store.vehicleOpen). `help`: also unfold "How sections are coloured". */
export function setPanelOpen(open: boolean, help = false) {
  store.set({ vehicleOpen: open });
  if (open && help) {
    const d = pane.querySelector<HTMLDetailsElement>('.compat-help');
    if (d) { d.open = true; d.scrollIntoView({ block: 'nearest' }); }
  }
}

function applyPanel(focus: boolean) {
  const open = store.get().vehicleOpen;
  const wasOpen = !panel.hidden;
  panel.hidden = !open;
  document.body.classList.toggle('vehicle-open', open);
  panelBtn.setAttribute('aria-pressed', String(open));
  panelBtn.setAttribute('aria-expanded', String(open));
  panelBtn.classList.toggle('compat-on', saved.show);
  if (!open) panel.classList.remove('expanded');
  renderLegend();
  if (!focus || open === wasOpen) return;
  if (open) (pane.querySelector<HTMLElement>('.compat-show input') ?? panel).focus();
  else if (panel.contains(document.activeElement) || document.activeElement === document.body) panelBtn.focus();
}

function setShow(on: boolean) {
  saved.show = on; persist();
  setVisible(on);
  panelBtn.classList.toggle('compat-on', on);
  const box = pane.querySelector<HTMLInputElement>('.compat-show input');
  if (box) box.checked = on;
  renderResult();
  recompute();
}

function setActive(id: string) {
  saved.active = id; persist();
  openRules.clear();
  for (const r of rules?.rules ?? []) if (V.isFilled(active()?.values[r.key])) openRules.add(r.key);
  renderPane();
  recompute();
}

function updateValue(key: string, v: V.VehicleProfile['values'][string] | undefined) {
  const p = active();
  if (!p) return;
  if (v === undefined || !V.isFilled(v)) delete p.values[key];
  else p.values[key] = v;
  persist();
  renderEditorHeads();
  renderLegend();
  if (saved.show) { busy = true; renderStatus(); recomputeSoon(); }
}

function download(name: string, type: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = h('a', { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'vehicle';

function exportProfiles(fmtName: 'json' | 'ttl') {
  const list = exportAll ? saved.profiles : [active()].filter(Boolean) as V.VehicleProfile[];
  const base = exportAll ? 'rinf-atlas-vehicles' : slug(list[0]?.name ?? 'vehicle');
  if (fmtName === 'json') download(`${base}.json`, 'application/json', V.toJson(list));
  else download(`${base}.ttl`, 'text/turtle', V.toTurtle(list, rules?.rules ?? []));
}

async function importFile(file: File) {
  try {
    if (!rules) throw new Error('the compatibility rules have not loaded yet');
    const list = V.importProfiles(await file.text(), rules.rules);
    const ids = new Set(saved.profiles.map((p) => p.id));
    for (const p of list) {
      if (ids.has(p.id)) p.id = V.newId();
      p.example = undefined;
      ids.add(p.id);
    }
    saved.profiles.push(...list);
    message = { text: `Imported ${list.length} profile${list.length === 1 ? '' : 's'} from ${file.name}.` };
    setActive(list[0].id);
  } catch (e) {
    message = { text: `Import failed: ${(e as Error).message}`, error: true };
    renderPane();
  }
}

function restoreExamples() {
  const ex = V.exampleProfiles();
  saved.profiles = [...saved.profiles.filter((p) => !ex.some((x) => x.id === p.id)), ...ex];
  message = { text: 'The example profiles were restored.' };
  setActive(saved.active && saved.profiles.some((p) => p.id === saved.active) ? saved.active : ex[0].id);
}

let statusEl: HTMLElement | null = null;
let headEls = new Map<string, HTMLElement>();

function renderStatus() {
  renderLegend();
  if (!statusEl) return;
  const parts: (Node | string)[] = [];
  if (!saved.show) parts.push(h('p', { class: 'muted small' }, 'Turn on to colour the sections of line on the map.'));
  else if (!rules) parts.push(h('p', { class: 'muted small' }, rulesErr ? '' : 'Loading the rules…'));
  else if (!hasFilled()) parts.push(h('p', { class: 'muted small' }, 'Fill in at least one characteristic below.'));
  else if (resultErr) parts.push(h('p', { class: 'error small' }, `Check failed: ${resultErr}`));
  else {
    parts.push(h('ul', { class: 'compat-rows' + (busy ? ' busy' : '') }, ...(['green', 'yellow', 'red'] as const).map((c) =>
      h('li', {}, h('span', { class: 'compat-swatch', style: `background:${COLOUR[c]}` }),
        h('span', { class: 'lbl' }, h('strong', {}, STATUS_TEXT[c]), h('small', { class: 'muted' }, ` · ${STATUS_HELP[c]}`)),
        h('span', { class: 'count' }, result ? fmt.format(result.counts[c]) : '…')))));
    if (result?.ignored.length) parts.push(h('p', { class: 'muted small' }, `Not checked (no rule): ${result.ignored.join(', ')}`));
  }
  statusEl.replaceChildren(...parts);
}

function renderEditorHeads() {
  const p = active();
  for (const r of rules?.rules ?? []) {
    const el = headEls.get(r.key);
    if (!el) continue;
    const v = p?.values[r.key];
    el.textContent = !V.isFilled(v) ? 'not checked'
      : typeof v === 'number' ? `${fmt.format(v)}${r.unit ? ` ${unitLabel(r.unit)}` : ''}`
      : `${(v as unknown[]).length} selected`;
    el.classList.toggle('filled', V.isFilled(v));
  }
}

/** The "none" option of a rule (e.g. not electrified), saying what picking it means. */
function noneLabel(r: CompatRule, label: string): string {
  return `${label} (also runs where the track has ${r.missing_reason ? r.missing_reason.toLowerCase() : 'none'})`;
}

function termEditor(r: CompatRule, body: HTMLElement) {
  const p = active()!;
  const cur = (Array.isArray(p.values[r.key]) ? p.values[r.key] : []) as (string | boolean)[];
  const selected = new Set(cur.map(String));
  const known = ruleValues(r);
  let values: { value: string | boolean; label: string; count: number | null }[] = r.kind === 'boolean'
    ? [true, false].map((b) => ({ value: b, label: b ? 'Yes' : 'No', count: known.get(String(b))?.count ?? 0 }))
    : (r.values ?? []).map((v) => ({ value: v.value as string, count: v.count,
      label: String(v.value) === r.none_value ? noneLabel(r, valueLabel(v.value, v.label)) : valueLabel(v.value, v.label) }));
  for (const v of cur) if (!values.some((x) => String(x.value) === String(v))) values.unshift({ value: v, label: valueLabel(v), count: null });
  const needle = (needles.get(r.key) ?? '').toLowerCase();
  const shown = needle ? values.filter((v) => v.label.toLowerCase().includes(needle) || String(v.value).toLowerCase().includes(needle)) : values;
  const isExpanded = expandedRules.has(r.key);
  const list = isExpanded || needle ? shown : shown.slice(0, TOP_N);
  const toggle = (v: string | boolean) => {
    const now = (Array.isArray(active()!.values[r.key]) ? active()!.values[r.key] : []) as (string | boolean)[];
    updateValue(r.key, now.some((x) => String(x) === String(v)) ? now.filter((x) => String(x) !== String(v)) : [...now, v]);
  };
  const parts: Node[] = [];
  if (values.length > TOP_N) {
    const f = h('input', { type: 'search', class: 'facet-filter', placeholder: `Filter ${r.label.toLowerCase()}…`, 'aria-label': `Filter ${r.label}`, value: needles.get(r.key) ?? '' });
    f.addEventListener('input', () => {
      needles.set(r.key, f.value);
      const pos = f.selectionStart;
      termEditor(r, body);
      const again = body.querySelector<HTMLInputElement>('input[type=search]');
      again?.focus(); again?.setSelectionRange(pos, pos);
    });
    parts.push(f);
  }
  parts.push(h('ul', { class: 'terms' }, ...list.map((v) => h('li', {},
    h('label', { class: 'check' + (v.count === 0 ? ' zero' : ''), title: String(v.value) },
      h('input', { type: 'checkbox', checked: selected.has(String(v.value)), onchange: () => toggle(v.value) }),
      h('span', { class: 'lbl' }, v.label),
      h('span', { class: 'count', title: 'Tracks with this value' }, v.count == null ? 'not in data' : fmt.format(v.count)))))));
  if (!values.length) parts.push(h('p', { class: 'muted small' }, 'No values in the indexed data.'));
  if (!needle && shown.length > TOP_N) {
    parts.push(h('button', { class: 'link small', onclick: () => {
      isExpanded ? expandedRules.delete(r.key) : expandedRules.add(r.key);
      termEditor(r, body);
    } }, isExpanded ? 'Show less' : `Show ${shown.length - TOP_N} more`));
  }
  body.querySelector('.compat-values')?.replaceChildren(...parts);
}

function numberEditor(r: CompatRule, body: HTMLElement) {
  const v = active()!.values[r.key];
  const input = h('input', { type: 'number', inputmode: 'decimal', step: 'any', class: 'compat-number', placeholder: 'not checked',
    'aria-label': r.label, value: typeof v === 'number' ? String(v) : '' });
  input.addEventListener('input', () => {
    const t = input.value.trim();
    const n = Number(t);
    updateValue(r.key, t === '' || !Number.isFinite(n) ? undefined : n);
  });
  const rg = r.range;
  const unit = r.unit ? unitLabel(r.unit) : '';
  body.querySelector('.compat-values')?.replaceChildren(
    h('div', { class: 'compat-number-row' }, input, unit ? h('span', { class: 'muted' }, unit) : null),
    rg && rg.min != null ? h('p', { class: 'muted small' }, `In the data: ${fmt.format(rg.min)}–${fmt.format(rg.max ?? rg.min)}${unit ? ` ${unit}` : ''}.`) : '');
}

function ruleBlock(r: CompatRule): HTMLElement {
  const isOpen = openRules.has(r.key);
  const state = h('span', { class: 'compat-filled' });
  headEls.set(r.key, state);
  const body = h('div', { class: 'facet-body' },
    r.comment ? h('p', { class: 'muted small compat-comment' }, r.comment) : null,
    h('div', { class: 'compat-values' }));
  const root = h('section', { class: 'facet' + (isOpen ? '' : ' collapsed'), 'data-rule': r.key },
    h('button', { class: 'facet-head', 'aria-expanded': String(isOpen), onclick: () => {
      openRules.has(r.key) ? openRules.delete(r.key) : openRules.add(r.key);
      const open = openRules.has(r.key);
      root.classList.toggle('collapsed', !open);
      root.querySelector('.facet-head')!.setAttribute('aria-expanded', String(open));
    } }, h('span', {}, r.label, ' ', state), h('span', { class: 'chev', 'aria-hidden': 'true' })),
    body);
  if (r.kind === 'number') numberEditor(r, body); else termEditor(r, body);
  return root;
}

function renderPane() {
  const p = active();
  headEls = new Map();
  const show = h('label', { class: 'toggle compat-show' },
    h('input', { type: 'checkbox', checked: saved.show, onchange: (e: Event) => setShow((e.target as HTMLInputElement).checked) }),
    h('strong', {}, 'Show compatibility on the map'));
  statusEl = h('div', { class: 'compat-status' });

  const select = h('select', { class: 'compat-select', 'aria-label': 'Vehicle profile', onchange: (e: Event) => setActive((e.target as HTMLSelectElement).value) },
    ...saved.profiles.map((x) => h('option', { value: x.id, selected: x.id === p?.id }, x.name)));
  const btn = (label: string, title: string, fn: () => void, disabled = false) =>
    h('button', { class: 'btn btn-small', type: 'button', title, disabled, onclick: fn }, label);
  const profileBar = h('div', { class: 'compat-bar' },
    btn('New', 'Start an empty vehicle profile', () => {
      const n: V.VehicleProfile = { id: V.newId(), name: 'New vehicle', values: {} };
      saved.profiles.push(n); setActive(n.id);
      pane.querySelector<HTMLInputElement>('.compat-name')?.select();
    }),
    btn('Duplicate', 'Copy this profile', () => {
      if (!p) return;
      const n: V.VehicleProfile = { id: V.newId(), name: `${p.name} (copy)`.replace(/^Example: /, ''), note: p.note, values: structuredClone(p.values) };
      saved.profiles.push(n); setActive(n.id);
    }, !p),
    btn('Delete', 'Delete this profile from this browser', () => {
      if (!p || !confirm(`Delete the vehicle profile “${p.name}” from this browser?`)) return;
      saved.profiles = saved.profiles.filter((x) => x.id !== p.id);
      setActive(saved.profiles[0]?.id ?? '');
    }, !p));

  const editor: (Node | null)[] = [];
  if (p) {
    const name = h('input', { type: 'text', class: 'facet-filter compat-name', value: p.name, 'aria-label': 'Profile name', maxlength: 200 });
    name.addEventListener('input', () => {
      p.name = name.value || 'Unnamed vehicle'; persist();
      const opt = select.querySelector<HTMLOptionElement>(`option[value="${CSS.escape(p.id)}"]`);
      if (opt) opt.textContent = p.name;
      renderLegend();
    });
    const note = h('textarea', { class: 'compat-note', rows: 2, placeholder: 'Note (optional)', 'aria-label': 'Note', maxlength: 2000 });
    note.value = p.note ?? '';
    note.addEventListener('input', () => { p.note = note.value || undefined; persist(); });
    editor.push(h('label', { class: 'compat-field' }, h('span', { class: 'muted small' }, 'Name'), name), note);
    if (p.example) editor.push(h('p', { class: 'compat-example small' }, 'Example profile with illustrative values, not an authorised vehicle type. Duplicate it or edit it to match your vehicle.'));
    editor.push(h('h2', { class: 'section-title' }, 'Characteristics'),
      h('p', { class: 'muted small' }, 'Fill in only what you know: a characteristic left empty is not checked. ',
        'Values are compared with the running tracks of each section of line.'));
    if (rules) editor.push(...rules.rules.map(ruleBlock));
    else editor.push(h('p', { class: rulesErr ? 'error small' : 'muted small loading-text' }, rulesErr ? `Could not load the compatibility rules: ${rulesErr}` : 'Loading the rules…'));
  } else {
    editor.push(h('p', { class: 'muted' }, 'No vehicle profiles. Create one or restore the examples.'));
  }

  const file = h('input', { type: 'file', accept: '.json,.ttl,application/json,text/turtle', hidden: true });
  file.addEventListener('change', () => { const f = file.files?.[0]; if (f) importFile(f); file.value = ''; });
  const scope = h('select', { class: 'compat-scope', 'aria-label': 'What to export', onchange: (e: Event) => { exportAll = (e.target as HTMLSelectElement).value === 'all'; } },
    h('option', { value: 'one', selected: !exportAll }, 'This profile'), h('option', { value: 'all', selected: exportAll }, 'All profiles'));
  const io = h('div', { class: 'compat-io' },
    h('h2', { class: 'section-title' }, 'Share and back up'),
    h('p', { class: 'muted small' }, 'Profiles are kept in this browser only. Export them as JSON, or as Turtle using ',
      h('code', {}, 'era:VehicleType'), ' from the ERA ontology, and import them elsewhere.'),
    h('div', { class: 'compat-bar' }, scope,
      btn('JSON', 'Download as JSON', () => exportProfiles('json'), !p),
      btn('Turtle', 'Download as Turtle (era:VehicleType)', () => exportProfiles('ttl'), !p || !rules)),
    h('div', { class: 'compat-bar' },
      btn('Import…', 'Import profiles from a JSON or Turtle file', () => file.click(), !rules),
      btn('Restore examples', 'Add the example profiles again, with their original values', restoreExamples), file),
    message ? h('p', { class: `small ${message.error ? 'error' : 'muted'}` }, message.text) : null,
    h('p', { class: 'muted small compat-eratv' }, 'Profiles are made by hand. Once ERATV, the European register of authorised ',
      'types of vehicles, is published as linked data, profiles could be imported from authorised vehicle types instead.'));

  pane.replaceChildren(
    h('p', { class: 'muted small compat-intro' }, 'A lightweight check of where a vehicle type can run, from the technical ',
      'characteristics you fill in and the RINF data of each section of line.'),
    show, statusEl,
    h('details', { class: 'compat-help' }, h('summary', { class: 'small' }, 'How sections are coloured'), h('p', { class: 'small' }, RULE_HELP),
      h('p', { class: 'small muted' }, 'Each characteristic is compared with an RINF track parameter using a SHACL constraint ',
        '(rules in shapes/compatibility.ttl). This is an indication from published data, not a route compatibility check.')),
    h('h2', { class: 'section-title' }, 'Vehicle profile'),
    h('div', { class: 'compat-bar' }, select), profileBar,
    ...editor.filter((x): x is Node => x !== null), io);
  renderEditorHeads();
  renderStatus();
}

/** After a new result (or none): counts, legend and open detail blocks; the editor stays. */
function renderResult() {
  renderStatus();
  for (const b of liveBlocks) refreshBlock(b);
}

// ---------- detail panel ----------
function checksTable(checks: CompatCheck[]): HTMLElement {
  const byKey = new Map((rules?.rules ?? []).map((r) => [r.key, r]));
  return h('table', { class: 'kv compat-table' }, h('tbody', {}, ...checks.map((c) => {
    const r = byKey.get(c.key);
    const unit = r?.unit ? ` ${unitLabel(r.unit)}` : '';
    const vals = c.values.map((v) => typeof v.value === 'number' ? `${fmt.format(v.value)}${unit}` : valueLabel(v.value, v.label));
    return h('tr', {},
      h('th', {}, r?.label ?? c.key),
      h('td', {}, h('span', { class: `compat-result ${c.result}` }, `${RESULT_ICON[c.result]} ${RESULT_TEXT[c.result]}`),
        c.note ? h('div', { class: 'compat-note-reason small' }, c.note) : null,
        vals.length ? h('div', { class: 'muted small' }, `Track: ${vals.join(', ')}`) : null));
  })));
}

function renderExplain(el: HTMLElement, x: CompatExplain) {
  const p = active();
  const tracks = x.tracks.map((t, i) => h('details', { class: 'compat-track', open: i === 0 || x.tracks.length <= 2 },
    h('summary', {}, h('span', { class: 'compat-swatch', style: `background:${COLOUR[t.status]}` }), ' ',
      t.label ?? t.iri, h('small', { class: 'muted' }, ` · ${STATUS_TEXT[t.status]}`)),
    checksTable(t.results)));
  el.replaceChildren(
    h('div', { class: 'compat-detail-head' },
      h('span', { class: 'compat-swatch big', style: `background:${COLOUR[x.status]}` }),
      h('div', {}, h('strong', {}, STATUS_TEXT[x.status]), h('div', { class: 'muted small' }, `for “${p?.name ?? 'vehicle'}”: ${STATUS_HELP[x.status]}`))),
    x.section.length ? h('div', {}, h('h3', { class: 'compat-sub' }, 'Section of line'), checksTable(x.section)) : '',
    x.tracks.length ? h('h3', { class: 'compat-sub' }, `Running tracks (${x.tracks.length})`) : '',
    ...tracks,
    !x.tracks.length && rules?.rules.some((r) => r.applies_to === 'track' && x.checked.includes(r.key))
      ? h('p', { class: 'muted small' }, 'No running tracks of this section are indexed, so its track characteristics could not be checked.') : '');
}

function refreshBlock(b: { el: HTMLElement; feature: Feature }) {
  if (!b.el.isConnected) { liveBlocks.delete(b); return; }
  const env = store.get().env;
  const p = active();
  b.el.hidden = !saved.show || !rules || !hasFilled();
  if (b.el.hidden) return;
  const body = b.el.querySelector<HTMLElement>('.compat-detail-body')!;
  body.replaceChildren(h('p', { class: 'muted small loading-text' }, 'Checking…'));
  api.compatExplain(env, V.filledValues(p), b.feature.iri).then((x) => {
    if (b.el.isConnected) renderExplain(body, x);
  }).catch((e) => { if (b.el.isConnected) body.replaceChildren(h('p', { class: 'error small' }, (e as Error).message)); });
}

/** Block for the detail panel of a section of line, when the compatibility map is on. */
export function compatBlock(f: Feature): HTMLElement | null {
  if (!f.classes.includes(sectionClass())) return null;
  const el = h('section', { class: 'compat-detail', 'aria-label': 'Vehicle compatibility' },
    h('h3', { class: 'section-title' }, 'Vehicle compatibility'),
    h('div', { class: 'compat-detail-body' }));
  const b = { el, feature: f };
  liveBlocks.add(b);
  queueMicrotask(() => refreshBlock(b));
  return el;
}

export function initCompat() {
  document.querySelector('.map-wrap')?.append(legend);
  panelBtn.addEventListener('click', () => setPanelOpen(!store.get().vehicleOpen));
  document.getElementById('vehicle-close')!.addEventListener('click', () => setPanelOpen(false));
  panel.querySelector('.vehicle-handle')!.addEventListener('click', () => panel.classList.toggle('expanded'));
  panel.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !(e.target as HTMLElement).closest('select')) { e.stopPropagation(); setPanelOpen(false); }
  });
  setTipNote(tipNote);
  onMapReady((m) => {
    mapRef = m;
    ensureLayers(m);
    applyResult(result);
  });
  store.subscribe((s, prev) => {
    if (changed(s, prev, 'env') && s.env) { result = null; applyResult(null); updateSource(); loadRules(s.env); }
    if (changed(s, prev, 'vehicleOpen')) applyPanel(true);
  });
  renderPane();
  applyPanel(false);
}
