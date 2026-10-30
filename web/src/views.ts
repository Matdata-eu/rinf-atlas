// Views (shapes/views/*.ttl): a named lens on the map. Opening one sets its classes,
// filters and map colours (plain app state, so a view link is an ordinary shared link
// plus vw=key), puts its parameters on top of the filter panel and offers its groups
// (e.g. the nine TEN-T corridors) in a picker. A view with requirement checks also has a
// scorecard: per group, the kilometres of sections that meet each requirement.
import {
  api, isAbort, type CheckResult, type Scorecard, type ScorecardCell, type TermCount, type TermValue,
  type ViewMeta,
} from './api';
import { CHECK_COLOURS, viewKeys, viewMeta } from './checks';
import { fitBbox } from './map';
import { openShapes } from './shapes';
import { changed, debounce, fmt, h, isFilterEmpty, queryState, store, type AppState } from './state';

const bar = document.getElementById('view-bar') as HTMLElement;
const section = document.getElementById('view-section') as HTMLElement;
const allFilters = document.getElementById('all-filters') as HTMLElement;
const pickerSelect = document.getElementById('view-select') as HTMLSelectElement;
const picker = pickerSelect.closest('label') as HTMLElement;
pickerSelect.addEventListener('change', () => openView(pickerSelect.value || null));

/** Every class and filter shown under the view's parameters (per visit, not in the link). */
let showAll = false;
let groups: TermCount[] | null = null;
let groupsErr: string | null = null;
/** profileRows the view set, to put back when it closes (only when the viewer had chosen none). */
let profileRowsSet: string[] | null = null;

const RESULT_TEXT: Record<CheckResult | 'not_applicable', string> = {
  meets: 'Meets', no_data: 'Data missing', fails: 'Does not meet', not_applicable: 'Does not apply',
};

// ---------- opening and closing ----------
export function openView(key: string | null) {
  const s = store.get();
  const v = key ? s.meta?.views?.find((x) => x.key === key) : null;
  if (!v) {
    const patch: Partial<AppState> = { lens: null };
    if (profileRowsSet && s.profileRows === profileRowsSet) patch.profileRows = null;
    profileRowsSet = null;
    store.set(patch);
    return;
  }
  showAll = false;
  const patch: Partial<AppState> = {
    lens: v.key, classes: [...v.classes], filters: structuredClone(v.filters), sideTab: 'filters',
    colorBy: v.color_by, colorHidden: [], theme: null,
  };
  if (s.profileRows == null || s.profileRows === profileRowsSet) {
    profileRowsSet = viewKeys(v).filter((k) => k !== v.group_by).slice(0, 10);
    patch.profileRows = profileRowsSet;
  }
  store.set(patch);
}

/** Show one group (e.g. a corridor), or every group; zooms to it. */
async function setGroup(v: ViewMeta, value: TermValue | null) {
  const s = store.get();
  const filters = { ...s.filters };
  for (const k of Object.keys(filters)) if (k === v.group_by || k.endsWith(`/${v.group_by}`)) delete filters[k];
  if (value != null) filters[v.group_by!] = [value];
  store.set({ filters });
  if (value == null) return;
  try {
    const { bbox } = await api.extent(queryState(store.get(), false));
    if (bbox) fitBbox(bbox, 11);
  } catch { /* the filter still applies */ }
}

function currentGroup(s: AppState, v: ViewMeta): TermValue | null {
  const f = s.filters[v.group_by ?? ''];
  return Array.isArray(f) && f.length === 1 ? f[0] : null;
}

// ---------- group counts ----------
let gctl: AbortController | null = null;
async function loadGroups() {
  const s = store.get();
  const v = viewMeta(s);
  gctl?.abort();
  if (!v?.group_by || !s.env) { groups = null; renderBar(); return; }
  gctl = new AbortController();
  const filters = Object.fromEntries(Object.entries(s.filters).filter(([k]) => k !== v.group_by && !k.endsWith(`/${v.group_by}`)));
  try {
    const r = await api.facets({ ...queryState(s), filters, facets: [v.group_by] }, gctl.signal);
    const f = r.facets[v.group_by];
    groups = f && f.kind !== 'range' ? (f.values as TermCount[]) : [];
    groupsErr = null;
  } catch (e) {
    if (isAbort(e)) return;
    groupsErr = (e as Error).message;
    groups = [];
  }
  renderBar();
}
const loadGroupsSoon = debounce(loadGroups, 80);

// ---------- the bar on top of the filter panel ----------
function renderBar() {
  const s = store.get();
  const views = s.meta?.views ?? [];
  const v = viewMeta(s);
  // the picker sits in the header, next to the environment
  picker.hidden = views.length === 0;
  pickerSelect.replaceChildren(
    h('option', { value: '', selected: !v }, 'Default view'),
    ...views.map((x) => h('option', { value: x.key, selected: v?.key === x.key, title: x.comment ?? '' }, x.label)));
  // the block above the filters only while a view is open
  bar.hidden = !v;
  section.hidden = !v;
  allFilters.hidden = !!v && !showAll;
  document.getElementById('view-toggle-all')?.replaceChildren(showAll ? 'Hide the other classes and filters' : 'Show every class and filter');
  document.getElementById('view-toggle-all')?.setAttribute('aria-expanded', String(showAll));
  if (!v) { bar.replaceChildren(); return; }
  const parts: (HTMLElement | null)[] = [
    h('div', { class: 'view-head' }, h('strong', { class: 'view-title', title: v.comment ?? '' }, v.label),
      h('button', { type: 'button', class: 'icon-btn', title: 'Back to the default view (filters stay)', 'aria-label': 'Close the view', onclick: () => openView(null) }, '×')),
  ];
  if (v.group_by) parts.push(groupPicker(s, v));
  const tools: HTMLElement[] = [];
  if (v.checks.length) tools.push(h('button', { type: 'button', class: 'btn btn-small', id: 'scorecard-btn', onclick: () => openScorecard() }, 'Requirements table'));
  if (v.comment) tools.push(h('details', { class: 'view-about small' }, h('summary', {}, 'About this view'), h('p', { class: 'muted' }, v.comment),
    v.file ? h('button', { type: 'button', class: 'link small', title: 'The SHACL file that defines this view', onclick: () => openShapes(v.file!) }, v.file) : null));
  if (tools.length) parts.push(h('div', { class: 'view-tools' }, ...tools));
  bar.replaceChildren(...parts.filter(Boolean) as HTMLElement[]);
}

function groupPicker(s: AppState, v: ViewMeta): HTMLElement {
  const cur = currentGroup(s, v);
  const label = v.group_label ?? 'Group';
  const list = groups ?? [];
  const known = list.some((g) => String(g.value) === String(cur));
  const sel = h('select', { id: 'view-group', 'aria-label': label, onchange: (e: Event) => {
    const i = (e.target as HTMLSelectElement).selectedIndex - 1;
    void setGroup(v, i >= 0 && i < list.length ? list[i].value : null);
  } },
  h('option', { value: '', selected: cur == null }, `Every ${label.toLowerCase()}`),
  ...list.map((g) => h('option', { value: String(g.value), selected: String(g.value) === String(cur) },
    `${g.label ?? String(g.value)} (${fmt.format(g.count)})`)),
  cur != null && !known ? h('option', { value: String(cur), selected: true }, String(cur)) : null);
  const note = groups && !groups.length && !groupsErr
    ? h('p', { class: 'muted small' }, `No ${label.toLowerCase()} is declared in RINF for the current filters.`)
    : groupsErr ? h('p', { class: 'error small' }, groupsErr) : null;
  return h('div', {}, h('div', { class: 'view-row' }, h('label', { class: 'view-label', for: 'view-group' }, label), sel), note);
}

// ---------- scorecard ----------
const dlg = h('dialog', { class: 'datasets-dialog scorecard-dialog', 'aria-labelledby': 'scorecard-title' });
dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
let card: Scorecard | null = null;
let unit: 'km' | 'count' = 'km';
let sctl: AbortController | null = null;

async function openScorecard() {
  const s = store.get();
  const v = viewMeta(s);
  if (!v) return;
  if (!dlg.isConnected) document.body.append(dlg);
  card = null;
  renderScorecard(v, 'Loading…');
  dlg.showModal();
  sctl?.abort();
  sctl = new AbortController();
  try {
    card = await api.scorecard(queryState(s, false), v.key, sctl.signal);
    renderScorecard(v);
  } catch (e) {
    if (!isAbort(e)) renderScorecard(v, `Could not load the table: ${(e as Error).message}`);
  }
}

const pct = (n: number, d: number) => (d > 0 ? `${Math.round((100 * n) / d)} %` : '–');
const amount = (c: ScorecardCell) => (unit === 'km' ? c.km : c.count);
const amountText = (n: number) => (unit === 'km' ? `${fmt.format(Math.round(n))} km` : fmt.format(n));

function cell(r: Record<CheckResult | 'not_applicable', ScorecardCell>): HTMLElement {
  const res = ['meets', 'no_data', 'fails'] as const;
  const applicable = res.reduce((n, k) => n + amount(r[k]), 0);
  const title = [...res, 'not_applicable' as const].map((k) => `${RESULT_TEXT[k]}: ${amountText(amount(r[k]))}`).join('\n');
  if (applicable === 0) return h('td', { class: 'sc-cell muted', title }, 'n/a');
  return h('td', { class: 'sc-cell', title },
    h('span', { class: 'sc-pct' }, pct(amount(r.meets), applicable)),
    h('span', { class: 'sc-bar', 'aria-hidden': 'true' }, ...res.map((k) => h('span', {
      style: `width:${(100 * amount(r[k])) / applicable}%;background:${CHECK_COLOURS[k]}` }))));
}

function renderScorecard(v: ViewMeta, msg?: string) {
  const s = store.get();
  const label = v.group_label ?? 'Group';
  const head = h('div', { class: 'dlg-head' }, h('h2', { id: 'scorecard-title' }, `${v.label}: requirements`),
    h('button', { type: 'button', class: 'icon-btn', 'aria-label': 'Close', onclick: () => dlg.close() }, '×'));
  if (!card || msg) { dlg.replaceChildren(head, h('p', { class: msg?.startsWith('Could') ? 'error small pad' : 'muted small pad' }, msg ?? 'Loading…')); return; }
  const c = card;
  const checks = c.keys.map((k) => k === v.summary_key
    ? { key: k, label: v.summary_label ?? 'Every requirement', deadline: null as string | null, comment: 'Worst result of the requirements that apply' }
    : v.checks.find((x) => x.key === k)!);
  const classLabel = (k: string) => s.meta?.classes.find((x) => x.class === k)?.label ?? k;
  const units = h('div', { class: 'seg', role: 'group', 'aria-label': 'Measure' },
    ...(['km', 'count'] as const).map((u) => h('button', { type: 'button', 'aria-pressed': String(unit === u),
      class: 'seg-btn', onclick: () => { unit = u; renderScorecard(v); } }, u === 'km' ? 'Kilometres' : 'Number')));
  const row = (g: { value: TermValue | null; label: string | null; count: number; km: number; checks: Scorecard['total']['checks'] }, total = false) => {
    const name = total ? 'All' : g.value == null ? `No ${label.toLowerCase()} declared` : g.label ?? String(g.value);
    const th = h('th', { scope: 'row' }, total || g.value == null ? name : h('button', { type: 'button', class: 'link', title: `Show only ${name} on the map`,
      onclick: () => { dlg.close(); void setGroup(v, g.value); } }, name), h('small', { class: 'muted' }, ` ${amountText(unit === 'km' ? g.km : g.count)}`));
    return h('tr', { class: total ? 'sc-total' : '' }, th, ...c.keys.map((k) => cell(g.checks[k])));
  };
  const table = h('table', { class: 'ds-table sc-table' },
    h('thead', {}, h('tr', {}, h('th', {}, label), ...checks.map((x) => h('th', { title: x.comment ?? '' }, x.label,
      x.deadline ? h('small', { class: 'muted sc-deadline' }, x.deadline) : '')))),
    h('tbody', {}, ...c.groups.map((g) => row(g)), row({ ...c.total, value: null, label: null }, true)));
  const legend = h('p', { class: 'muted small sc-legend' }, 'Share of the ', unit === 'km' ? 'length' : 'number',
    ` of ${c.classes.map(classLabel).join(', ').toLowerCase()} the requirement applies to that meets it. Bar: `,
    ...(['meets', 'no_data', 'fails'] as const).flatMap((k) => [h('span', { class: 'compat-swatch', style: `background:${CHECK_COLOURS[k]}` }), ` ${RESULT_TEXT[k].toLowerCase()} `]),
    v.section_class ? `. A section of line meets a requirement when one of its tracks does.` : '.');
  const notes = h('details', { class: 'sc-notes' }, h('summary', {}, 'How each requirement is checked'),
    h('ul', {}, ...v.checks.map((x) => h('li', {}, h('b', {}, x.label), x.deadline ? ` (${x.deadline})` : '', x.comment ? `: ${x.comment}` : ''))));
  const filtered = Object.entries(s.filters).some(([k, f]) => k !== v.group_by && !isFilterEmpty(f)) || s.q || s.graphs.length;
  dlg.replaceChildren(head,
    h('div', { class: 'dlg-tools' }, units,
      h('button', { type: 'button', class: 'btn btn-small', onclick: () => downloadCsv(v, checks) }, 'Download CSV'),
      filtered ? h('span', { class: 'muted small' }, 'For the current filters.') : null),
    h('div', { class: 'dlg-scroll' }, legend, table, notes));
}

function downloadCsv(v: ViewMeta, checks: { key: string; label: string }[]) {
  if (!card) return;
  const q = (x: string | number) => (typeof x === 'number' ? String(x) : `"${x.replace(/"/g, '""')}"`);
  const cols = ['meets', 'no_data', 'fails', 'not_applicable'] as const;
  const head = [v.group_label ?? 'Group', 'IRI', ...checks.flatMap((c) => cols.flatMap((r) => [`${c.label}: ${RESULT_TEXT[r]} (km)`, `${c.label}: ${RESULT_TEXT[r]} (number)`]))];
  const line = (name: string, iri: string, g: Scorecard['total']) => [q(name), q(iri), ...checks.flatMap((c) => cols.flatMap((r) => [g.checks[c.key][r].km, g.checks[c.key][r].count]))].join(',');
  const rows = [head.map(q).join(','), ...card.groups.map((g) => line(g.value == null ? '(none)' : g.label ?? String(g.value), g.value == null ? '' : String(g.value), g)),
    line('All', '', card.total)];
  const a = h('a', { href: URL.createObjectURL(new Blob(['﻿' + rows.join('\r\n')], { type: 'text/csv' })), download: `rinf-atlas-${v.key}-requirements.csv` });
  document.body.append(a);
  a.click();
  a.remove();
}

export function initViews() {
  document.getElementById('view-toggle-all')?.addEventListener('click', () => { showAll = !showAll; renderBar(); });
  store.subscribe((s, prev) => {
    // a link to a view the instance does not have: no view
    if (s.lens && s.meta && !viewMeta(s)) queueMicrotask(() => store.set({ lens: null }));
    if (changed(s, prev, 'meta', 'lens')) { groups = null; renderBar(); }
    else if (changed(s, prev, 'filters')) renderBar();
    if (changed(s, prev, 'meta', 'lens', 'classes', 'filters', 'graphs', 'q')) loadGroupsSoon();
  });
  renderBar();
}
