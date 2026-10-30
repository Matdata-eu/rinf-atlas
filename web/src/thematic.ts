// Thematic map: colour features by the value of one facet ("Colour by"), with a legend.
//
// The API computes a display value per feature (/api/theme; tiles and search then carry it as
// property `v`): a terms/boolean value (or MIXED when a feature has several), or the highest
// number of a range facet. Sections of line take the values of their tracks. Colours are
// assigned here from palette slots / class breaks that are stable for the environment, so
// filtering never repaints a value. Hiding legend entries and highlighting one are pure
// style changes: no tile is fetched again.
import type { ExpressionSpecification } from 'maplibre-gl';
import { api, isAbort, MIXED, type FacetMeta, type RangeBin, type RangeTheme, type TermsTheme, type Theme } from './api';
import { changed, debounce, fmt, h, queryState, store, toggleAllZoom, unitLabel, type AppState } from './state';
import { checkColour, viewFacets } from './checks';
import { isDark, onThemeChange } from './theme';

type Expr = ExpressionSpecification;

export const NODATA = '__nodata__';
export const OTHER = '__other__';
export const FADED = '__faded__';

// ---------- palettes (validated with the dataviz palette checker against both map themes) ----------
// Categorical: eight hues in a fixed, colour-blind-checked order; light and dark steps of the same hues.
const CAT_LIGHT = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948'];
const CAT_DARK = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9', '#e66767'];
// Sequential: one blue hue, light->dark on the light map, dark->light on the dark map (high = most contrast).
const SEQ_LIGHT = ['#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b'];
const SEQ_DARK = ['#256abf', '#3987e5', '#6da7ec', '#9ec5f4', '#cde2fb'];
// Neutrals, told apart by lightness and line width: mixed (strong), other values (mid), no data (faint, thin).
const NEUTRAL = {
  light: { mixed: '#2b2a28', other: '#6f6d68', nodata: '#b4b2ab', casing: 'rgba(20,24,32,0.55)' },
  dark: { mixed: '#ecebe4', other: '#a8a69f', nodata: '#5a5955', casing: 'rgba(0,0,0,0.75)' },
};
const neutral = () => (isDark() ? NEUTRAL.dark : NEUTRAL.light);
const SEQ_PICK: Record<number, number[]> = { 1: [2], 2: [1, 3], 3: [0, 2, 4], 4: [0, 1, 3, 4], 5: [0, 1, 2, 3, 4] };
// Six to eight classes (atlas:colorClasses in the shapes): the same hue in eight even lightness steps.
const SEQ8_LIGHT = ['#86b6ef', '#65a2ea', '#438de6', '#2b79d7', '#2366ba', '#1a549e', '#114383', '#0c3366'];
const SEQ8_DARK = ['#184f95', '#2062b4', '#2976d2', '#418ce6', '#66a2ea', '#89b8f0', '#accdf5', '#cde2fb'];
const SEQ8_PICK: Record<number, number[]> = { 6: [0, 2, 3, 5, 6, 7], 7: [0, 2, 3, 4, 5, 6, 7], 8: [0, 1, 2, 3, 4, 5, 6, 7] };

export function slotColor(slot: number): string { return (isDark() ? CAT_DARK : CAT_LIGHT)[slot % 8]; }
export function binColors(n: number): string[] {
  if (n > 5) return SEQ8_PICK[Math.min(n, 8)].map((i) => (isDark() ? SEQ8_DARK : SEQ8_LIGHT)[i]);
  const ramp = isDark() ? SEQ_DARK : SEQ_LIGHT;
  return (SEQ_PICK[Math.min(Math.max(n, 1), 5)] ?? SEQ_PICK[5]).map((i) => ramp[i]);
}
export const casingColor = () => neutral().casing;

// ---------- theme state ----------
let loading = false;
let error: string | null = null;
let focusKey: string | null = null;
let onStyle: (() => void) | null = null;

/** The map calls this to be told when colours, hidden entries or the highlighted entry change. */
export function onThemeStyleChange(fn: () => void) { onStyle = fn; }

export function setColorBy(key: string | null) {
  const s = store.get();
  if (key === s.colorBy) return;
  store.set({ colorBy: key, colorHidden: [], theme: null });
}

/** Coloured classes of a theme; null = every class (a shared facet like country). */
function coloured(t: Theme): string[] | null { return t.classes.length ? [...t.classes, ...t.rollup] : null; }

const hasV: Expr = ['has', 'v'];
const vStr: Expr = ['to-string', ['get', 'v']];
const vNum: Expr = ['to-number', ['get', 'v'], 0];

function inColoured(t: Theme): Expr {
  const c = coloured(t);
  return c ? ['in', ['get', 'class'], ['literal', c]] : (['boolean', true] as Expr);
}

function binIndex(t: RangeTheme): Expr {
  if (!t.breaks.length) return ['literal', 0] as unknown as Expr;
  if (t.upper) return ['case', ...t.breaks.flatMap((b, i) => [['<=', vNum, b], i]), t.breaks.length] as unknown as Expr;
  return ['step', vNum, 0, ...t.breaks.flatMap((b, i) => [b, i + 1])] as unknown as Expr;
}

function slots(t: TermsTheme): string[] { return t.values.filter((v) => v.slot != null || checkColour(v.value)).map((v) => v.value); }

function valueColor(t: Theme): Expr | string {
  const n = neutral();
  if (t.kind === 'range') {
    const cs = binColors(t.bins.length);
    if (!t.breaks.length) return cs[0];
    if (t.upper) return ['case', ...t.breaks.flatMap((b, i) => [['<=', vNum, b], cs[i]]), cs[t.breaks.length]] as unknown as Expr;
    return ['step', vNum, cs[0], ...t.breaks.flatMap((b, i) => [b, cs[Math.min(i + 1, cs.length - 1)]])] as unknown as Expr;
  }
  // a requirement check's results keep their own colours (green / amber / red)
  const pairs = t.values.filter((v) => v.slot != null || checkColour(v.value)).flatMap((v) => [v.value, checkColour(v.value) ?? slotColor(v.slot!)]);
  return ['match', vStr, ...pairs, MIXED, n.mixed, n.other] as unknown as Expr;
}

/** Boolean expression: does the feature belong to this legend entry? */
function entryExpr(t: Theme, key: string): Expr {
  const col = inColoured(t);
  if (key === NODATA) return ['all', ['!', hasV], col];
  if (key === FADED) return ['all', ['!', hasV], ['!', col]];
  if (key.startsWith('bin:') && t.kind === 'range') return ['all', hasV, ['==', binIndex(t), Number(key.slice(4))]];
  if (key === OTHER && t.kind !== 'range') return ['all', hasV, ['!', ['in', vStr, ['literal', [...slots(t), MIXED]]]]];
  return ['all', hasV, ['==', vStr, key]];
}

export interface ThemeStyle {
  color: Expr;
  opacity: Expr;
  /** Multiplier on the normal line width / circle radius. */
  width: Expr;
  visible: Expr;
  /** Added to the class sort key: coloured features above faded ones. */
  boost: Expr;
}

/** Map style for the current theme, or null when the map uses class colours. */
export function themeStyle(classColor: Expr | string): ThemeStyle | null {
  const s = store.get();
  const t = s.theme;
  if (!s.colorBy || !t) return null;
  const col = inColoured(t);
  const n = neutral();
  const isOther = t.kind === 'range' ? (['boolean', false] as Expr) : entryExpr(t, OTHER);
  const hidden = s.colorHidden.map((k) => entryExpr(t, k));
  const focus = focusKey ? entryExpr(t, focusKey) : null;
  const base: Expr = ['case', hasV, 1, col, 0.85, 0.32];
  return {
    color: ['case', hasV, valueColor(t), col, n.nodata, classColor] as Expr,
    opacity: focus ? (['case', focus, 1, 0.12] as Expr) : base,
    width: ['case', hasV, ['case', isOther, 1.25, 1.6], col, 0.8, 0.75] as Expr,
    visible: hidden.length ? (['!', ['any', ...hidden]] as Expr) : (['boolean', true] as Expr),
    boost: ['case', hasV, 2000, col, 1000, 0] as Expr,
  };
}

// ---------- labels ----------
const numFmt = new Intl.NumberFormat('en', { maximumFractionDigits: 3 });
function unitOf(t: Theme) { return t.unit ? ` ${unitLabel(t.unit)}` : ''; }
function classLabel(c: string) { return store.get().meta?.classes.find((x) => x.class === c)?.label ?? c; }

/** Bin of a value: classes hold [min, max), fixed classes from the shapes (min, max]. */
const binOf = (t: RangeTheme, n: number) => t.breaks.filter((b) => (t.upper ? n > b : n >= b)).length;

/** Value range of a fixed class: "≤ 40", "41 – 80" (integers) or "> 40 – 80", "> 300". */
function upperRange(t: RangeTheme, b: RangeBin): string {
  const u = unitOf(t);
  if (b.min == null) return b.max == null ? '' : `≤ ${numFmt.format(b.max)}${u}`;
  if (b.max == null) return `> ${numFmt.format(b.min)}${u}`;
  const int = t.datatype != null && /#(integer|int|long|short|nonNegativeInteger|positiveInteger)$/.test(t.datatype);
  return int ? `${numFmt.format(b.min + 1)} – ${numFmt.format(b.max)}${u}` : `> ${numFmt.format(b.min)} – ${numFmt.format(b.max)}${u}`;
}

export function binLabel(t: RangeTheme, i: number): string {
  const b = t.bins[i];
  if (!b) return '';
  if (t.upper) return b.label ? `${upperRange(t, b)} · ${b.label}` : upperRange(t, b);
  if (b.min == null || b.max == null) return '';
  if (t.discrete || b.min === b.max) return `${numFmt.format(b.min)}${unitOf(t)}`;
  return `${numFmt.format(b.min)} – ${numFmt.format(b.max)}${unitOf(t)}`;
}

/** Tooltip line for a map feature: [swatch colour, text], or null when the map uses class colours. */
export function describeValue(props: Record<string, unknown>): [string, string] | null {
  const s = store.get();
  const t = s.theme;
  if (!s.colorBy || !t) return null;
  const v = props.v;
  const c = coloured(t);
  if (v == null) {
    if (c && !c.includes(String(props.class))) return null;
    return [neutral().nodata, `${t.label}: no data`];
  }
  if (t.kind === 'range') {
    const n = Number(v);
    const i = Math.min(binOf(t, n), t.bins.length - 1);
    const rolled = t.rollup.includes(String(props.class)) ? ' (highest of its parts)' : '';
    const name = t.upper && t.bins[i]?.label ? ` (${t.bins[i].label})` : '';
    return [binColors(t.bins.length)[i] ?? neutral().other, `${t.label}: ${numFmt.format(n)}${unitOf(t)}${name}${rolled}`];
  }
  if (v === MIXED) return [neutral().mixed, `${t.label}: mixed (several values)`];
  const tv = t.values.find((x) => x.value === String(v));
  const label = tv?.label ?? String(v).replace(/^.*[/#]/, '');
  return [checkColour(String(v)) ?? (tv?.slot != null ? slotColor(tv.slot) : neutral().other), `${t.label}: ${label}`];
}

// ---------- loading ----------
let ctl: AbortController | null = null;
async function load() {
  const s = store.get();
  if (!s.colorBy || !s.env || !s.meta) { renderLegend(); return; }
  ctl?.abort();
  ctl = new AbortController();
  loading = true;
  error = null;
  renderLegend();
  const key = s.colorBy;
  try {
    const t = await api.theme(queryState(s), key, ctl.signal);
    if (store.get().colorBy !== key) return;
    loading = false;
    store.set({ theme: t });
  } catch (e) {
    if (isAbort(e)) return;
    loading = false;
    error = (e as Error).message;
    store.set({ theme: null });
  }
  renderLegend();
}
const loadSoon = debounce(load, 60);

// ---------- legend (on the map) ----------
const legend = h('section', { class: 'legend', id: 'legend', 'aria-label': 'Map colours', hidden: true });
const PHONE = window.matchMedia('(max-width: 760px)');
let collapsed = PHONE.matches;
let showOthers = false;

export const PALETTE_ICON = '<svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true"><path d="M8 1.75a6.25 6.25 0 1 0 0 12.5c.9 0 1.4-.6 1.4-1.3 0-.9-.8-1.2-.8-2 0-.7.6-1.2 1.4-1.2h1.6a3.2 3.2 0 0 0 3.15-3.2C14.75 4 11.7 1.75 8 1.75z" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="4.9" cy="7.2" r="1" fill="currentColor"/><circle cx="7" cy="4.6" r="1" fill="currentColor"/><circle cx="10.2" cy="4.8" r="1" fill="currentColor"/></svg>';

function toggleHidden(key: string, only = false, keys: string[] = []) {
  const s = store.get();
  let hidden: string[];
  if (only) hidden = keys.filter((k) => k !== key);
  else hidden = s.colorHidden.includes(key) ? s.colorHidden.filter((k) => k !== key) : [...s.colorHidden, key];
  store.set({ colorHidden: hidden });
}

function setFocus(key: string | null) {
  if (focusKey === key) return;
  focusKey = key;
  onStyle?.();
}

interface Entry { key: string; label: string; color: string; count: number | null; line: 'strong' | 'normal' | 'thin'; title?: string }

function entries(t: Theme): { main: Entry[]; others: Entry[]; extra: Entry[] } {
  const n = neutral();
  const main: Entry[] = [];
  const others: Entry[] = [];
  const extra: Entry[] = [];
  if (t.kind === 'range') {
    const cs = binColors(t.bins.length);
    t.bins.forEach((b, i) => main.push({
      key: `bin:${i}`, label: binLabel(t, i), color: cs[i], count: b.count, line: 'strong',
      title: t.upper ? `${b.label ? `${b.label}: ` : ''}${upperRange(t, b)}`
        : t.discrete || b.min == null || b.max == null ? undefined
        : `from ${numFmt.format(b.min)} up to ${i === t.bins.length - 1 ? 'and including' : 'but not including'} ${numFmt.format(b.max)}${unitOf(t)}`,
    }));
  } else {
    for (const v of t.values) {
      const fixed = checkColour(v.value);
      const e: Entry = { key: v.value, label: v.label, color: fixed ?? (v.slot != null ? slotColor(v.slot) : n.other), count: v.count, line: 'strong', title: v.value };
      (v.slot != null || fixed ? main : others).push(e);
    }
    if (t.other > 0) main.push({ key: OTHER, label: `Other values (${others.length}${t.more ? '+' : ''})`, color: n.other, count: t.other, line: 'normal' });
    if (t.mixed > 0) main.push({ key: MIXED, label: 'Mixed (several values)', color: n.mixed, count: t.mixed, line: 'strong' });
  }
  // a requirement check: a feature without a result is one the requirement does not apply to
  const isCheck = t.kind !== 'range' && t.values.length > 0 && t.values.every((v) => checkColour(v.value));
  if (t.nodata > 0 || t.classes.length) extra.push({ key: NODATA, label: isCheck ? 'Requirement does not apply' : 'No data', color: n.nodata, count: t.nodata, line: 'thin' });
  if (t.classes.length) extra.push({ key: FADED, label: 'Other classes (faded)', color: 'var(--muted)', count: null, line: 'thin' });
  return { main, others, extra };
}

function swatch(e: Entry) {
  return h('span', { class: `lg-swatch lg-${e.line}`, style: `--sw:${e.color}`, 'aria-hidden': 'true' });
}

function row(e: Entry, allKeys: string[]) {
  const hidden = store.get().colorHidden.includes(e.key);
  const b = h('button', {
    type: 'button', class: 'lg-row' + (hidden ? ' off' : ''), 'aria-pressed': String(!hidden), 'data-key': e.key,
    title: `${e.title ? e.title + ' · ' : ''}Click to ${hidden ? 'show' : 'hide'}; Shift+click to show only this`,
    onclick: (ev: Event) => toggleHidden(e.key, (ev as MouseEvent).shiftKey, allKeys),
    onmouseenter: () => setFocus(hidden ? null : e.key), onmouseleave: () => setFocus(null),
    onfocus: () => setFocus(hidden ? null : e.key), onblur: () => setFocus(null),
  }, swatch(e), h('span', { class: 'lbl' }, e.label), h('span', { class: 'count' }, e.count == null ? '' : fmt.format(e.count)));
  return h('li', {}, b);
}

function notes(t: Theme, s: AppState): HTMLElement[] {
  const out: HTMLElement[] = [];
  const own = t.classes.map(classLabel);
  if (t.rollup.length && own.length) {
    const r = t.rollup.map(classLabel).join(', ');
    const o = own.join(', ').toLowerCase();
    out.push(h('p', { class: 'lg-note' }, t.kind === 'range'
      ? `${r}: the highest value of its ${o} parts.`
      : `${r}: the value its ${o} parts share; “Mixed” when they differ.`));
  }
  const meta = s.meta?.classes ?? [];
  const below = t.classes.map((c) => meta.find((m) => m.class === c)).filter((m) => m && m.min_zoom > s.view.zoom);
  const late = below.filter((m) => !s.allZoom.includes(m!.class));
  if (late.length) {
    const z = Math.min(...late.map((m) => m!.min_zoom));
    out.push(h('p', { class: 'lg-note muted' }, `${late.map((m) => m!.label).join(', ')}: drawn from zoom ${z}` +
      (t.rollup.length ? `; ${t.rollup.map(classLabel).join(', ')} carr${t.rollup.length === 1 ? 'ies' : 'y'} the colour until then. ` : '. '),
      h('button', { type: 'button', class: 'link', onclick: () => toggleAllZoom(...late.map((m) => m!.class)) }, 'Show at every zoom')));
  } else if (below.length) {
    out.push(h('p', { class: 'lg-note muted' }, `${below.map((m) => m!.label).join(', ')}: shown at every zoom. `,
      h('button', { type: 'button', class: 'link', onclick: () => toggleAllZoom(...below.map((m) => m!.class)) },
        `Only from zoom ${Math.min(...below.map((m) => m!.min_zoom))}`)));
  }
  if (s.classes.length && coloured(t) && !coloured(t)!.some((c) => s.classes.includes(c))) {
    out.push(h('p', { class: 'lg-note warn' }, `None of the coloured classes is selected. Select ${[...t.rollup, ...t.classes].map(classLabel).join(' or ')} in the class list.`));
  }
  return out;
}

function renderLegend() {
  const s = store.get();
  legend.hidden = !s.colorBy;
  if (!s.colorBy) { legend.replaceChildren(); return; }
  const t = s.theme;
  const title = t?.label ?? facetFor(s, s.colorBy)?.label ?? s.colorBy;
  const ownerLabel = t ? (t.classes.length ? t.classes.map(classLabel).join(', ') : 'Every class') : '';
  const toggle = h('button', {
    type: 'button', class: 'lg-toggle', 'aria-expanded': String(!collapsed), 'aria-controls': 'legend-body',
    title: collapsed ? 'Show the legend' : 'Fold the legend',
    onclick: () => { collapsed = !collapsed; renderLegend(); },
  });
  toggle.innerHTML = PALETTE_ICON;
  toggle.append(h('span', { class: 'lg-title' }, h('span', { class: 'muted' }, 'Colour: '), title),
    loading ? h('span', { class: 'spinner', 'aria-label': 'Loading' }) : '', h('span', { class: 'chev', 'aria-hidden': 'true' }));
  const reset = h('button', { type: 'button', class: 'icon-btn lg-reset', title: 'Back to class colours', 'aria-label': 'Back to class colours', onclick: () => setColorBy(null) }, '×');
  const head = h('div', { class: 'lg-head' }, toggle, reset);
  legend.classList.toggle('collapsed', collapsed);
  const body = h('div', { class: 'lg-body', id: 'legend-body', hidden: collapsed });
  if (error) {
    body.append(h('p', { class: 'error small' }, `Could not colour the map: ${error}`));
  } else if (!t) {
    body.append(h('p', { class: 'muted small loading-text' }, 'Working out the colours…'));
  } else {
    body.append(h('p', { class: 'muted small lg-sub' }, `${ownerLabel}${t.unit ? ` · ${unitLabel(t.unit)}` : ''}`));
    body.append(...notes(t, s));
    const { main, others, extra } = entries(t);
    const all = [...main, ...others, ...extra].map((e) => e.key);
    if (t.total === 0) {
      body.append(h('p', { class: 'muted small' }, 'No feature of the coloured classes matches the current filters.'));
    } else if (t.nodata === t.total) {
      body.append(h('p', { class: 'muted small' }, 'None of the matching features has a value for this property.'));
    }
    const ul = h('ul', { class: 'lg-list', 'aria-label': 'Legend entries: click to hide or show' }, ...main.map((e) => row(e, all)));
    if (others.length) {
      if (showOthers) others.forEach((e) => ul.append(row(e, all)));
      ul.append(h('li', {}, h('button', { type: 'button', class: 'link small lg-more', 'aria-expanded': String(showOthers),
        onclick: () => { showOthers = !showOthers; renderLegend(); } }, showOthers ? 'Hide the other values' : `List the ${others.length} other values`)));
    }
    if (extra.length) ul.append(h('li', { class: 'lg-sep', 'aria-hidden': 'true' }), ...extra.map((e) => row(e, all)));
    body.append(ul);
    const foot = h('div', { class: 'lg-foot' });
    if (s.colorHidden.length) foot.append(h('button', { type: 'button', class: 'link small', onclick: () => store.set({ colorHidden: [] }) }, 'Show all'));
    foot.append(h('button', { type: 'button', class: 'link small', onclick: () => openPicker() }, 'Colour by another property…'));
    body.append(foot);
  }
  // Keep keyboard focus on the same row across re-renders.
  const focused = (document.activeElement as HTMLElement | null)?.dataset?.key;
  const hadToggle = document.activeElement?.classList.contains('lg-toggle');
  legend.replaceChildren(head, body);
  if (focused) legend.querySelector<HTMLElement>(`.lg-row[data-key="${CSS.escape(focused)}"]`)?.focus();
  else if (hadToggle) toggle.focus();
}

// ---------- "Colour by" picker ----------
function facetFor(s: AppState, key: string): FacetMeta | undefined {
  const [c, k] = key.includes('/') ? key.split(/\/(.*)/s, 2) : ['*', key];
  return s.meta?.facets.find((f) => f.class === c && f.key === k);
}
const keyOf = (f: FacetMeta) => (f.class === '*' ? f.key : `${f.class}/${f.key}`);
const kindText = (f: FacetMeta) => (f.kind === 'range' ? `range${f.unit ? ` · ${unitLabel(f.unit)}` : ''}` : f.kind === 'boolean' ? 'yes / no' : 'values');

interface Option { key: string | null; label: string; group: string; hint: string }

function options(s: AppState): Option[] {
  const classes = s.meta?.classes ?? [];
  const present = new Set(classes.filter((c) => c.count > 0).map((c) => c.class));
  const order = new Map(classes.map((c, i) => [c.class, i]));
  const facets = (s.meta?.facets ?? []).filter((f) => f.class === '*' || present.has(f.class));
  const cl = (c: string) => (c === '*' ? 'Every class' : classLabel(c));
  const opt = (f: FacetMeta, group: string): Option => ({ key: keyOf(f), label: f.label, group, hint: `${cl(f.class)} · ${kindText(f)}` });
  const out: Option[] = [{ key: null, label: 'Class colours', group: '', hint: 'one colour per class (default)' }];
  const view = s.lens ? s.meta?.views?.find((v) => v.key === s.lens) : undefined;
  if (view) out.push(...viewFacets(s).map((f) => ({ key: f.key, label: f.label, group: `View: ${view.label}`, hint: kindText(f) })));
  const suggested = facets.filter((f) => f.color_by != null).sort((a, b) => a.color_by! - b.color_by!);
  out.push(...suggested.map((f) => opt(f, 'Suggested')));
  const rest = [...facets].sort((a, b) => (a.class === '*' ? -1 : order.get(a.class) ?? 999) - (b.class === '*' ? -1 : order.get(b.class) ?? 999)
    || a.position - b.position || a.label.localeCompare(b.label));
  out.push(...rest.map((f) => opt(f, cl(f.class))));
  return out;
}

const dialog = h('dialog', { class: 'datasets-dialog picker-dialog', 'aria-labelledby': 'picker-title' });
let pickerOpts: Option[] = [];
let active = 0;
let shown: Option[] = [];

function normalise(x: string) { return x.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase(); }

function renderPicker(input: HTMLInputElement, list: HTMLElement, status: HTMLElement) {
  const s = store.get();
  const toks = normalise(input.value).split(/\s+/).filter(Boolean);
  const match = (o: Option) => toks.every((t) => normalise(`${o.label} ${o.hint} ${o.key ?? ''}`).includes(t));
  // While searching, each facet is listed once (not again under Suggested).
  shown = toks.length ? pickerOpts.filter((o) => o.group !== 'Suggested' && !o.group.startsWith('View: ') && o.key !== null && match(o)) : pickerOpts;
  active = Math.min(active, Math.max(shown.length - 1, 0));
  const els: HTMLElement[] = [];
  let group: string | null = null;
  shown.forEach((o, i) => {
    if (o.group !== group) {
      group = o.group;
      if (group) els.push(h('li', { class: 'pk-group', role: 'presentation' }, group));
    }
    const current = (o.key ?? null) === (s.colorBy ?? null);
    els.push(h('li', {
      id: `pk-${i}`, role: 'option', class: 'pk-opt' + (i === active ? ' active' : ''), 'aria-selected': String(i === active),
      'data-i': i, onmousedown: (e: Event) => e.preventDefault(), onclick: () => choose(o),
      onmousemove: () => { if (active !== i) { active = i; renderPicker(input, list, status); } },
    }, h('span', { class: 'pk-label' }, o.label, current ? h('span', { class: 'pk-current' }, ' · current') : ''), h('small', { class: 'muted' }, o.hint)));
  });
  if (!shown.length) els.push(h('li', { class: 'muted pk-empty', role: 'presentation' }, 'No property matches. Try another word, e.g. “speed” or “gauge”.'));
  list.replaceChildren(...els);
  input.setAttribute('aria-activedescendant', shown.length ? `pk-${active}` : '');
  status.textContent = toks.length ? `${shown.length} matching ${shown.length === 1 ? 'property' : 'properties'}` : '';
  list.querySelector('.pk-opt.active')?.scrollIntoView({ block: 'nearest' });
}

function choose(o: Option) {
  dialog.close();
  setColorBy(o.key);
  if (o.key && !PHONE.matches) { collapsed = false; renderLegend(); } // on phones it stays a one-line pill
}

/** Opens the "Colour by" picker; `pre` puts that facet key first in focus. */
export function openPicker(pre?: string) {
  const s = store.get();
  if (!s.meta) return;
  pickerOpts = options(s);
  const input = h('input', {
    type: 'search', class: 'facet-filter pk-input', placeholder: 'Search properties, e.g. speed, voltage, gauge…',
    role: 'combobox', 'aria-expanded': 'true', 'aria-controls': 'pk-list', 'aria-autocomplete': 'list', 'aria-label': 'Search properties',
  });
  const list = h('ul', { class: 'pk-list', id: 'pk-list', role: 'listbox', 'aria-label': 'Properties' });
  const status = h('p', { class: 'sr-only', role: 'status' });
  const want = pre ?? s.colorBy;
  active = Math.max(0, pickerOpts.findIndex((o) => o.key === (want ?? null)));
  input.addEventListener('input', () => { active = 0; renderPicker(input, list, status); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      active = (active + (e.key === 'ArrowDown' ? 1 : -1) + shown.length) % Math.max(shown.length, 1);
      renderPicker(input, list, status);
    } else if (e.key === 'Home' || e.key === 'End') {
      if (!input.value) { e.preventDefault(); active = e.key === 'Home' ? 0 : shown.length - 1; renderPicker(input, list, status); }
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (shown[active]) choose(shown[active]);
    }
  });
  dialog.replaceChildren(
    h('div', { class: 'dlg-head' }, h('h2', { id: 'picker-title' }, 'Colour the map by'),
      h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Close', onclick: () => dialog.close() }, '×')),
    h('p', { class: 'muted small pad pk-help' }, 'Lines and points take a colour per value; sections of line show the values of their tracks. ↑ ↓ to move, Enter to choose.'),
    h('div', { class: 'pad pk-search' }, input, status),
    h('div', { class: 'dlg-scroll pk-scroll' }, list));
  renderPicker(input, list, status);
  dialog.showModal();
  input.focus();
}

// ---------- side panel control ----------
function renderControl() {
  const s = store.get();
  const btn = document.getElementById('colorby-btn');
  const clear = document.getElementById('colorby-clear') as HTMLButtonElement | null;
  if (!btn || !clear) return;
  const f = s.colorBy ? facetFor(s, s.colorBy) : undefined;
  const label = s.colorBy ? (f?.label ?? s.theme?.label ?? s.colorBy) : 'Class colours';
  const owner = f && f.class !== '*' ? classLabel(f.class) : f ? 'Every class' : '';
  btn.replaceChildren();
  btn.innerHTML = PALETTE_ICON;
  btn.append(h('span', { class: 'cb-text' }, h('span', { class: 'lbl' }, label), owner ? h('small', { class: 'muted' }, owner) : ''),
    h('span', { class: 'chev', 'aria-hidden': 'true' }));
  btn.title = s.colorBy ? `The map is coloured by ${label}. Click to change.` : 'Colour the map by a property (speed, electrification, gauge…)';
  btn.classList.toggle('active', !!s.colorBy);
  clear.hidden = !s.colorBy;
}

export function initThematic(mapWrap: HTMLElement) {
  mapWrap.append(legend);
  document.body.append(dialog);
  dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); }); // backdrop
  document.getElementById('colorby-btn')?.addEventListener('click', () => openPicker());
  document.getElementById('colorby-clear')?.addEventListener('click', () => setColorBy(null));
  PHONE.addEventListener('change', () => { collapsed = PHONE.matches; renderLegend(); });
  onThemeChange(() => { renderLegend(); onStyle?.(); });
  store.subscribe((s, prev) => {
    if (changed(s, prev, 'colorBy')) { focusKey = null; showOthers = false; if (!s.colorBy) { ctl?.abort(); loading = false; error = null; } }
    if (changed(s, prev, 'colorBy', 'meta', 'theme')) renderControl();
    if (changed(s, prev, 'env', 'meta', 'colorBy', 'classes', 'filters', 'graphs', 'q', 'limitToView') || (s.colorBy && s.limitToView && changed(s, prev, 'bbox'))) {
      if (s.colorBy) loadSoon(); else if (s.theme) store.set({ theme: null });
    }
    if (changed(s, prev, 'colorBy', 'theme', 'colorHidden', 'classes', 'allZoom') || (s.colorBy && s.view.zoom !== prev.view.zoom)) renderLegend();
    if (changed(s, prev, 'theme', 'colorHidden')) onStyle?.();
  });
  renderControl();
  renderLegend();
}
