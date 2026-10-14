// Left panel: class list, facets, active filter chips.
import { api, isAbort, type FacetMeta, type FacetResult, type FacetsResponse, type FilterValue, type RangeFilter, type TermCount, type TermValue } from './api';
import { datasetLabel } from './datasets';
import { changed, debounce, fmt, h, isFilterEmpty, queryState, store, unitLabel, type AppState } from './state';

const TOP_N = 10;
const classList = document.getElementById('class-list') as HTMLUListElement;
const facetsEl = document.getElementById('facets') as HTMLDivElement;
const chipsEl = document.getElementById('chips') as HTMLDivElement;
const limitView = document.getElementById('limit-view') as HTMLInputElement;

let data: FacetsResponse | null = null;
/** facet key -> value -> label, so chips can show labels for values not in the current response. */
const labelCache = new Map<string, Map<string, string>>();
/** Block ids (`class|key`) whose term list is fully expanded. */
const expanded = new Set<string>();
/** Block id -> open, once the user has opened or folded it. */
const openState = new Map<string, boolean>();
/** Classes whose own facets are open under them in the class list. */
const openClasses = new Set<string>();

interface Block { root: HTMLElement; body: HTMLElement; filter?: HTMLInputElement; min?: HTMLInputElement; max?: HTMLInputElement }
const blocks = new Map<string, Block>();

const key = (v: TermValue) => String(v);

const byPosition = (a: FacetMeta, b: FacetMeta) => a.position - b.position || a.label.localeCompare(b.label);
const blockId = (f: FacetMeta) => `${f.class}|${f.key}`;
/** Filter/facet key sent to the API: a class's own facets are scoped to that class ("class/key"). */
export const filterKey = (f: FacetMeta) => (f.class === '*' ? f.key : `${f.class}/${f.key}`);

/** Facets every infrastructure element has (country, IM...): shown under the class list. */
export function sharedFacets(s: AppState): FacetMeta[] {
  return (s.meta?.facets ?? []).filter((f) => f.class === '*').sort(byPosition);
}

/** One class's own facets: shown behind the filter icon next to that class. */
export function classFacets(s: AppState, c: string): FacetMeta[] {
  return (s.meta?.facets ?? []).filter((f) => f.class === c).sort(byPosition);
}

/** Facet keys to ask the API for: shared ones, open classes' ones and any with a filter. */
function wantedFacetKeys(s: AppState): string[] {
  const keys = new Set(sharedFacets(s).map((f) => f.key));
  for (const c of openClasses) for (const f of classFacets(s, c)) keys.add(filterKey(f));
  for (const [k, v] of Object.entries(s.filters)) if (!isFilterEmpty(v)) keys.add(k);
  return [...keys];
}

/** Number of active filters on a class's own facets (badge on its filter icon). */
function activeOn(s: AppState, c: string): number {
  return classFacets(s, c).filter((f) => !isFilterEmpty(s.filters[filterKey(f)])).length;
}

function toggleOpen(c: string) {
  openClasses.has(c) ? openClasses.delete(c) : openClasses.add(c);
  render();
  loadSoon();
}

function setFilter(k: string, v: FilterValue | undefined) {
  const filters = { ...store.get().filters };
  if (v === undefined || isFilterEmpty(v)) delete filters[k];
  else filters[k] = v;
  store.set({ filters });
}

function toggleTerm(k: string, v: TermValue) {
  const cur = (store.get().filters[k] as TermValue[] | undefined) ?? [];
  const exists = cur.some((x) => key(x) === key(v));
  setFilter(k, exists ? cur.filter((x) => key(x) !== key(v)) : [...cur, v]);
}

function toggleClass(c: string) {
  const s = store.get();
  // A class's facet filters only ever constrain that class, so they can stay.
  store.set({ classes: s.classes.includes(c) ? s.classes.filter((x) => x !== c) : [...s.classes, c] });
}

// ---------- fetching ----------
let ctl: AbortController | null = null;
async function load() {
  const s = store.get();
  if (!s.env || !s.meta) return;
  ctl?.abort();
  ctl = new AbortController();
  document.getElementById('side')!.classList.add('loading');
  try {
    data = await api.facets({ ...queryState(s), facets: wantedFacetKeys(s) }, ctl.signal);
    for (const [k, r] of Object.entries(data.facets)) {
      if (r.kind === 'range') continue;
      const m = labelCache.get(k) ?? new Map<string, string>();
      for (const v of normaliseValues(r)) if (v.label) m.set(key(v.value), v.label);
      labelCache.set(k, m);
    }
    render();
  } catch (e) {
    if (!isAbort(e)) { console.error(e); facetsEl.replaceChildren(h('p', { class: 'error' }, `Could not load facets: ${(e as Error).message}`)); }
  } finally {
    document.getElementById('side')!.classList.remove('loading');
  }
}
const loadSoon = debounce(load, 50);

function normaliseValues(r: Exclude<FacetResult, { kind: 'range' }>): TermCount[] {
  return (r.values as (TermCount | TermValue)[]).map((v) =>
    v !== null && typeof v === 'object' ? v : { value: v, count: NaN });
}

// ---------- rendering ----------
function render() {
  renderClasses();
  renderFacets();
  renderChips();
}

interface ClassRow { li: HTMLLIElement; label: HTMLLabelElement; box: HTMLInputElement; hint: HTMLElement; count: HTMLElement;
  btn: HTMLButtonElement | null; badge: HTMLElement; panel: HTMLElement }
const rows = new Map<string, ClassRow>();

const FILTER_ICON = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2 3h12l-4.6 5.6V13l-2.8 1.4V8.6z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>';

function rowFor(c: NonNullable<AppState['meta']>['classes'][number]): ClassRow {
  let r = rows.get(c.class);
  if (r) return r;
  const box = h('input', { type: 'checkbox', 'data-class': c.class, onchange: () => toggleClass(c.class) });
  const hint = h('small', { class: 'muted zoom-hint', title: `Drawn on the map from zoom ${c.min_zoom}` }, ' zoom in to see');
  const count = h('span', { class: 'count' });
  const label = h('label', { class: 'check' }, box, h('span', { class: 'swatch', style: `background:${c.color}` }),
    h('span', { class: 'lbl' }, c.label, hint), count);
  const badge = h('span', { class: 'filter-badge' });
  const hasFacets = classFacets(store.get(), c.class).length > 0;
  const btn = hasFacets ? h('button', {
    class: 'icon-btn filter-btn', 'data-filter-class': c.class, 'aria-expanded': 'false',
    title: `Filter ${c.label.toLowerCase()} by its own properties`, 'aria-label': `Filters for ${c.label}`,
    onclick: () => toggleOpen(c.class),
  }) : null;
  if (btn) { btn.innerHTML = FILTER_ICON; btn.append(badge); }
  const panel = h('div', { class: 'class-facets', hidden: true });
  const li = h('li', { class: 'class-row' }, h('div', { class: 'class-line' }, label, btn ?? h('span', { class: 'filter-btn-spacer' })), panel);
  r = { li, label, box, hint, count, btn, badge, panel };
  rows.set(c.class, r);
  return r;
}

function renderClasses() {
  const s = store.get();
  const counts = data?.by_class ?? {};
  // Classes with nothing indexed in this environment are hidden; ones the current
  // filters empty stay (greyed) so the list does not jump around.
  const shown = (s.meta?.classes ?? []).filter((c) => c.count > 0 || s.classes.includes(c.class));
  const els = shown.map((c) => {
    const r = rowFor(c);
    const n = counts[c.class] ?? c.count;
    r.label.classList.toggle('zero', n === 0);
    r.box.checked = s.classes.includes(c.class);
    r.hint.hidden = s.view.zoom >= (c.min_zoom ?? 0);
    r.count.textContent = fmt.format(n);
    const open = openClasses.has(c.class);
    const active = activeOn(s, c.class);
    r.li.classList.toggle('open', open);
    r.panel.hidden = !open;
    if (r.btn) {
      r.btn.setAttribute('aria-expanded', String(open));
      r.btn.classList.toggle('active', active > 0);
      r.badge.textContent = active ? String(active) : '';
    }
    return r.li;
  });
  const current = [...classList.children];
  if (current.length !== els.length || current.some((c, i) => c !== els[i])) classList.replaceChildren(...els);
}

function blockFor(f: FacetMeta): Block {
  const id = blockId(f);
  let b = blocks.get(id);
  if (b) return b;
  const body = h('div', { class: 'facet-body' });
  const head = h('button', { class: 'facet-head', 'aria-expanded': 'true', onclick: () => {
    openState.set(id, b!.root.classList.contains('collapsed'));
    renderFacets();
  } }, h('span', {}, f.label, f.unit ? h('small', { class: 'muted' }, ` (${unitLabel(f.unit)})`) : null),
    h('span', { class: 'chev', 'aria-hidden': 'true' }));
  const root = h('section', { class: `facet facet-${f.kind}`, 'data-facet': filterKey(f) }, head, body);
  b = { root, body };
  if (f.kind === 'terms') {
    b.filter = h('input', { type: 'search', class: 'facet-filter', placeholder: `Filter ${f.label.toLowerCase()}…`, 'aria-label': `Filter ${f.label}` });
    b.filter.addEventListener('input', () => renderFacets());
  }
  if (f.kind === 'range') {
    const commit = () => {
      const num = (i: HTMLInputElement) => (i.value.trim() === '' ? null : Number(i.value));
      const r: RangeFilter = { min: num(b!.min!), max: num(b!.max!) };
      setFilter(filterKey(f), r.min == null && r.max == null ? undefined : r);
    };
    b.min = h('input', { type: 'number', inputmode: 'decimal', 'aria-label': `${f.label} minimum`, onchange: commit });
    b.max = h('input', { type: 'number', inputmode: 'decimal', 'aria-label': `${f.label} maximum`, onchange: commit });
  }
  blocks.set(id, b);
  return b;
}

function renderBlock(s: AppState, f: FacetMeta): HTMLElement {
  const b = blockFor(f);
  const active = !isFilterEmpty(s.filters[filterKey(f)]);
  // Shared facets start open; a class's own facets start folded (there can be many) unless filtered.
  const isCollapsed = !(openState.get(blockId(f)) ?? (f.class === '*' || active));
  b.root.classList.toggle('collapsed', isCollapsed);
  b.root.querySelector('.facet-head')!.setAttribute('aria-expanded', String(!isCollapsed));
  b.root.classList.toggle('active', active);
  const r = data?.facets[filterKey(f)];
  if (f.kind === 'range') renderRange(f, b, r);
  else renderTerms(f, b, r);
  return b.root;
}

/** Put els into container in order; existing nodes are moved, which keeps focus inside inputs. */
function place(container: HTMLElement, els: HTMLElement[]) {
  const current = [...container.children];
  if (current.length !== els.length || current.some((c, i) => c !== els[i])) container.replaceChildren(...els);
}

function renderFacets() {
  const s = store.get();
  const shared = sharedFacets(s);
  place(facetsEl, shared.map((f) => renderBlock(s, f)));
  if (!shared.length) facetsEl.replaceChildren(h('p', { class: 'muted' }, 'No shared facets.'));
  for (const c of openClasses) {
    const r = rows.get(c);
    if (r) place(r.panel, classFacets(s, c).map((f) => renderBlock(s, f)));
  }
}

function renderRange(f: FacetMeta, b: Block, r: FacetResult | undefined) {
  const cur = (store.get().filters[filterKey(f)] as RangeFilter | undefined) ?? {};
  const dom = r?.kind === 'range' ? r : null;
  if (document.activeElement !== b.min) b.min!.value = cur.min != null ? String(cur.min) : '';
  if (document.activeElement !== b.max) b.max!.value = cur.max != null ? String(cur.max) : '';
  b.min!.placeholder = dom?.min != null ? `min ${fmt.format(dom.min)}` : 'min';
  b.max!.placeholder = dom?.max != null ? `max ${fmt.format(dom.max)}` : 'max';
  if (!b.body.childElementCount) {
    b.body.append(h('div', { class: 'range' }, b.min!, h('span', { 'aria-hidden': 'true' }, '–'), b.max!));
  }
}

function renderTerms(f: FacetMeta, b: Block, r: FacetResult | undefined) {
  const fk = filterKey(f);
  const filt = store.get().filters[fk];
  const selected = new Set(Array.isArray(filt) ? filt.map(key) : []);
  let values: TermCount[] = r && r.kind !== 'range' ? normaliseValues(r) : [];
  if (f.kind === 'boolean') {
    // Always offer yes/no; fill counts from the response when present.
    const by = new Map(values.map((v) => [key(v.value), v]));
    values = [true, false].map((v) => ({ value: v, label: v ? 'Yes' : 'No', count: by.get(String(v))?.count ?? 0 }));
  }
  // Selected values that the server no longer returns stay visible (with 0).
  for (const sv of selected) if (!values.some((v) => key(v.value) === sv)) {
    const orig = (filt as TermValue[]).find((x) => key(x) === sv)!;
    values.unshift({ value: orig, label: labelCache.get(fk)?.get(sv), count: 0 });
  }
  const needle = b.filter?.value.trim().toLowerCase() ?? '';
  const shown = needle ? values.filter((v) => (v.label ?? key(v.value)).toLowerCase().includes(needle)) : values;
  const isExpanded = expanded.has(blockId(f));
  const list = isExpanded || needle ? shown : shown.slice(0, TOP_N);

  const ul = h('ul', { class: 'terms' }, ...list.map((v) => h('li', {},
    h('label', { class: 'check' + (v.count === 0 && !selected.has(key(v.value)) ? ' zero' : ''), title: key(v.value) },
      h('input', { type: 'checkbox', checked: selected.has(key(v.value)), onchange: () => toggleTerm(fk, v.value) }),
      h('span', { class: 'lbl' }, v.label ?? key(v.value)),
      h('span', { class: 'count' }, Number.isNaN(v.count) ? '' : fmt.format(v.count))))));
  const parts: Node[] = [];
  if (b.filter && values.length > TOP_N) parts.push(b.filter);
  parts.push(ul);
  if (!values.length) parts.push(h('p', { class: 'muted small' }, 'No values'));
  if (r?.kind === 'terms' && r.more && (isExpanded || needle || shown.length <= TOP_N)) {
    parts.push(h('p', { class: 'muted small' }, 'Only the most frequent values are listed; type to narrow down.'));
  }
  if (!needle && shown.length > TOP_N) {
    parts.push(h('button', { class: 'link small', onclick: () => {
      isExpanded ? expanded.delete(blockId(f)) : expanded.add(blockId(f));
      renderFacets();
    } }, isExpanded ? 'Show less' : `Show ${shown.length - TOP_N} more`));
  }
  b.body.replaceChildren(...parts);
}

function renderChips() {
  const s = store.get();
  const meta = new Map((s.meta?.facets ?? []).flatMap((f) => [[f.key, f], [filterKey(f), f]] as [string, FacetMeta][]));
  const classLabel = new Map((s.meta?.classes ?? []).map((c) => [c.class, c.label]));
  const chip = (text: string, title: string, onRemove: () => void) =>
    h('span', { class: 'chip', title }, h('span', {}, text),
      h('button', { class: 'chip-x', 'aria-label': `Remove ${title}`, onclick: onRemove }, '×'));
  const chips: HTMLElement[] = [];
  if (s.q) chips.push(chip(`“${s.q}”`, 'search text', () => store.set({ q: '' })));
  for (const g of s.graphs) {
    chips.push(chip(`Dataset: ${datasetLabel(g)}`, g, () => store.set({ graphs: store.get().graphs.filter((x) => x !== g) })));
  }
  for (const c of s.classes) {
    const cm = s.meta?.classes.find((x) => x.class === c);
    chips.push(chip(cm?.label ?? c, 'class', () => toggleClass(c)));
  }
  for (const [k, v] of Object.entries(s.filters)) {
    if (isFilterEmpty(v)) continue;
    const fm = meta.get(k);
    const owner = fm && fm.class !== '*' && k.includes('/') ? classLabel.get(fm.class) : undefined;
    const label = owner ? `${owner} · ${fm!.label}` : fm?.label ?? k;
    if (Array.isArray(v)) {
      for (const x of v) {
        const vl = typeof x === 'boolean' ? (x ? 'Yes' : 'No') : labelCache.get(k)?.get(key(x)) ?? key(x);
        chips.push(chip(`${label}: ${vl}`, `${label} = ${key(x)}`, () => toggleTerm(k, x)));
      }
    } else {
      const lo = v.min != null ? fmt.format(v.min) : '…', hi = v.max != null ? fmt.format(v.max) : '…';
      const unit = meta.get(k)?.unit;
      chips.push(chip(`${label}: ${lo}–${hi}${unit ? ` ${unitLabel(unit)}` : ''}`, label, () => setFilter(k, undefined)));
    }
  }
  if (chips.length > 1) {
    chips.push(h('button', { class: 'link small clear-all', onclick: () => store.set({ classes: [], filters: {}, graphs: [], q: '' }) }, 'Clear all'));
  }
  chipsEl.replaceChildren(...chips);
  chipsEl.hidden = chips.length === 0;
}

export function initFacets() {
  limitView.addEventListener('change', () => store.set({ limitToView: limitView.checked }));
  store.subscribe((s, prev) => {
    limitView.checked = s.limitToView;
    if (changed(s, prev, 'env')) { data = null; blocks.clear(); rows.clear(); labelCache.clear(); openClasses.clear(); }
    if (changed(s, prev, 'meta')) rows.clear(); // class colours, labels and facet lists may differ
    if (changed(s, prev, 'meta', 'classes', 'filters', 'graphs', 'q')) render(); // instant feedback, counts follow
    else if (s.view.zoom !== prev.view.zoom) renderClasses(); // min_zoom hints
    if (changed(s, prev, 'meta', 'classes', 'filters', 'graphs', 'q', 'limitToView') || (s.limitToView && changed(s, prev, 'bbox'))) loadSoon();
  });
  limitView.checked = store.get().limitToView;
}

export function totalCount(): number | null { return data?.total ?? null; }
