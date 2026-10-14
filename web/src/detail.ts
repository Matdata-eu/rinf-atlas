// Detail panel: feature properties, live Describe (out/in) with navigation, and graph view.
import { api, ApiError, isAbort, type DescribeResponse, type Feature, type FeatureQuality, type RdfTerm, type Triple } from './api';
import { compatBlock } from './compat';
import type { GraphHandle } from './graph';
import { fitGeometry, flyTo, setSelectionGeometry } from './map';
import { showLineProfile } from './profile';
import { literalText, ntText, pathText, prefixed, shortIri } from './rdf';
import { changed, fmt, h, store, unitLabel, type DetailTab } from './state';

const panel = document.getElementById('detail') as HTMLElement;

let feature: Feature | null = null;
let featureErr: string | null = null;
let stack: string[] = []; // describe navigation (stack[0] = selected feature)
let direction: 'out' | 'in' = 'out';
let graph: GraphHandle | null = null;
let ctl: AbortController | null = null;
/** Pending "zoom to" request: fit the map once this feature's geometry has loaded. */
let zoomWanted: { iri: string; fallback?: [number, number] } | null = null;

export const ZOOM_ICON = '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="7" cy="7" r="4.75" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10.5 10.5 14 14M7 4.8v4.4M4.8 7h4.4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>';

/** Selects a feature and fits the map to its geometry; `fallback` (lon, lat) is used when it has none. */
export function zoomToFeature(iri: string, fallback?: [number, number]) {
  if (store.get().selected === iri && feature && stack[0] === iri) { zoomNow(feature.geometry, fallback); return; }
  zoomWanted = { iri, fallback };
  store.set({ selected: iri }); // open() fits the map once the feature has loaded
}

function zoomNow(g: GeoJSON.Geometry | null, fallback?: [number, number]) {
  if (g) fitGeometry(g);
  else if (fallback) flyTo(...fallback);
}

const describeCache = new Map<string, Promise<DescribeResponse>>();
export function describe(iri: string, dir: 'out' | 'in' = 'out'): Promise<DescribeResponse> {
  const env = store.get().env;
  const k = `${env}|${dir}|${iri}`;
  let p = describeCache.get(k);
  if (!p) {
    p = api.describe(env, iri, dir);
    p.catch(() => describeCache.delete(k));
    describeCache.set(k, p);
  }
  return p;
}

const featureCache = new Map<string, Promise<Feature | null>>();
function featureOrNull(iri: string): Promise<Feature | null> {
  const env = store.get().env;
  const k = `${env}|${iri}`;
  let p = featureCache.get(k);
  if (!p) {
    p = api.feature(env, iri).catch((e) => { if (e instanceof ApiError && e.status === 404) return null; throw e; });
    featureCache.set(k, p);
  }
  return p;
}

function close() { store.set({ selected: null }); }

async function open(iri: string) {
  ctl?.abort();
  ctl = new AbortController();
  feature = null; featureErr = null; stack = [iri]; direction = 'out';
  setSelectionGeometry(null);
  panel.hidden = false;
  document.body.classList.add('detail-open');
  render();
  try {
    feature = await api.feature(store.get().env, iri, ctl.signal);
    setSelectionGeometry(feature.geometry);
  } catch (e) {
    if (isAbort(e)) return;
    if (zoomWanted?.iri === iri && zoomWanted.fallback) flyTo(...zoomWanted.fallback);
    featureErr = e instanceof ApiError && e.status === 404 ? 'Not in the index (it may still be described live).' : (e as Error).message;
  }
  render();
  if (zoomWanted?.iri === iri) {
    if (feature) zoomNow(feature.geometry, zoomWanted.fallback); // after render, so the map has its final size
    zoomWanted = null;
  }
}

function classInfo(c: string) {
  const meta = store.get().meta;
  return meta?.classes.find((m) => m.class === c || m.class_iri === c);
}

function kicker(): HTMLElement {
  const cls = feature?.classes.map(classInfo).find(Boolean);
  return h('div', { class: 'kicker' },
    h('span', { class: 'swatch', style: `background:${cls?.color ?? '#888'}` }),
    cls?.label ?? feature?.classes.map(shortIri).join(', ') ?? '');
}

function render() {
  const s = store.get();
  if (!s.selected) return;
  const title = feature?.label || (s.selected ? shortIri(s.selected) : '');
  const tabs: [DetailTab, string][] = [['properties', 'Properties'], ['describe', 'Describe'], ['graph', 'Graph']];
  const body = h('div', { class: 'detail-body' });

  panel.replaceChildren(
    h('div', { class: 'detail-head' },
      h('div', { class: 'detail-title' },
        kicker(),
        h('h2', {}, title),
        h('a', { class: 'iri', href: s.selected, target: '_blank', rel: 'noopener', title: 'Open IRI' }, s.selected)),
      zoomButton(),
      h('button', { class: 'icon-btn', 'aria-label': 'Close details', onclick: close }, '×')),
    h('div', { class: 'tabs detail-tabs', role: 'tablist' }, ...tabs.map(([k, label]) =>
      h('button', { class: 'tab', role: 'tab', 'aria-selected': String(s.detailTab === k), 'data-dtab': k,
        onclick: () => store.set({ detailTab: k }) }, label))),
    body,
  );
  graph?.destroy(); graph = null;
  if (s.detailTab === 'properties') renderProperties(body);
  else if (s.detailTab === 'describe') renderDescribe(body);
  else renderGraph(body);
}

function zoomButton(): HTMLElement {
  const g = feature?.geometry;
  const title = g ? 'Zoom to this feature' : feature ? 'This feature has no geometry' : featureErr ? 'Not in the index' : 'Loading…';
  const b = h('button', { class: 'icon-btn zoom-btn', title, 'aria-label': title, disabled: !g, onclick: () => g && fitGeometry(g) });
  b.innerHTML = ZOOM_ICON;
  return b;
}

function renderProperties(body: HTMLElement) {
  if (featureErr) { body.append(h('p', { class: 'muted' }, featureErr)); return; }
  if (!feature) { body.append(h('p', { class: 'muted loading-text' }, 'Loading…')); return; }
  const f = feature;
  const facetMeta = new Map((store.get().meta?.facets ?? []).map((m) => [m.key, m]));
  const rows = Object.entries(f.facets)
    .sort(([a], [b]) => (facetMeta.get(a)?.position ?? 999) - (facetMeta.get(b)?.position ?? 999))
    .map(([k, vals]) => h('tr', {},
      h('th', {}, facetMeta.get(k)?.label ?? f.facet_labels?.[k] ?? k),
      h('td', {}, ...vals.map((v) => {
        const unit = facetMeta.get(k)?.unit;
        const text = v.label ?? (typeof v.value === 'boolean' ? (v.value ? 'Yes' : 'No')
          : typeof v.value === 'number' && unit ? `${fmt.format(v.value)} ${unitLabel(unit)}` : String(v.value));
        // link facets point at other indexed features: open them here
        if (f.facet_kinds?.[k] === 'link' && typeof v.value === 'string') {
          const iri = v.value;
          return h('div', {}, h('button', { class: 'link', title: iri, onclick: () => store.set({ selected: iri, detailTab: 'properties' }) }, text));
        }
        return typeof v.value === 'string' && /^https?:\/\//.test(v.value)
          ? h('div', {}, h('a', { href: v.value, target: '_blank', rel: 'noopener', title: v.value }, text))
          : h('div', {}, text);
      }))));
  body.append(
    compatBlock(f) ?? '',
    h('table', { class: 'kv' }, h('tbody', {},
      ...rows,
      h('tr', {}, h('th', {}, 'Graph'), h('td', {}, h('code', { class: 'small' }, f.graph_iri))))),
    h('div', { class: 'row-actions' },
      f.geometry ? h('button', { class: 'btn', onclick: () => fitGeometry(f.geometry!) }, 'Zoom to feature') : null,
      ...(f.classes.includes('section-of-line') ? (f.facets['nationalLine.lineId'] ?? []).map((v) => h('button', {
        class: 'btn', title: 'Parameters along this national line', onclick: () => showLineProfile(String(v.value), f.graph_iri),
      }, `Line profile ${v.label ?? v.value}`)) : []),
      h('button', { class: 'btn', onclick: () => store.set({ detailTab: 'describe' }) }, 'Describe live')),
  );
  if (f.quality && store.get().meta?.quality?.enabled) body.append(qualitySection(f, f.quality));
}

const SEV_TEXT = { violation: 'Violation', warning: 'Warning', info: 'Info' } as const;

/** Results of the validation against ERA's SHACL shapes whose focus node is this feature. */
function qualitySection(f: Feature, q: FeatureQuality): HTMLElement {
  const env = store.get().env;
  const sec = h('section', { class: 'qa-section', 'aria-label': 'Data quality' },
    h('h3', {}, 'Data quality ', h('small', { class: 'muted', title: q.shapes ? `${q.shapes.label}\n${q.shapes.url ?? q.shapes.graph}` : '' },
      q.shapes?.kind === 'tag' ? `ERA SHACL shapes ${q.shapes.tag}` : `ERA SHACL shapes${q.shapes?.version ? ` v${q.shapes.version}` : ''}`)));
  if (q.status === null || q.status === 'skipped' || q.status === 'failed') {
    sec.append(h('p', { class: 'muted small' }, q.status === null ? 'This dataset has not been validated yet.'
      : q.reason === 'too_large' ? `Its dataset was not validated: it is too large${q.note ? ` ${q.note.replace(/^not validated: too large /, '')}` : ''}.`
        : `This dataset was not validated${q.note ? `: ${q.note}` : '.'}`));
    return sec;
  }
  const partial = q.status === 'partial' ? ' (the check of this dataset stopped early, so this list may be incomplete)' : '';
  if (!q.results.length) {
    sec.append(h('p', { class: 'qa-ok small' }, `No issues found on this ${f.label ? 'feature' : 'resource'}${partial}.`));
  } else {
    const counts = (['violation', 'warning', 'info'] as const).map((s) => [s, q.results.filter((r) => r.severity === s).length] as const).filter(([, n]) => n);
    sec.append(h('p', { class: 'small' }, ...counts.flatMap(([s, n]) => [h('span', { class: `pill sev-${s}` }, `${n} ${SEV_TEXT[s].toLowerCase()}${n === 1 ? '' : 's'}`), ' ']),
      partial ? h('span', { class: 'muted' }, partial) : null));
    sec.append(h('ul', { class: 'qa-results' }, ...q.results.map((r) => h('li', { class: `sev-${r.severity}` },
      h('div', { class: 'qa-msg' }, h('span', { class: `pill sev-${r.severity}` }, SEV_TEXT[r.severity]), ' ', r.message ?? shortIri(r.component)),
      h('div', { class: 'qa-meta muted small' },
        r.path ? h('span', {}, 'Path ', h('code', { title: r.path }, pathText(r.path))) : null,
        r.value ? h('span', {}, 'Value ', h('code', { title: r.value }, r.value_label ?? ntText(r.value))) : null,
        h('span', {}, 'Rule ', h('a', { href: r.shape.startsWith('http') ? r.shape : undefined, target: '_blank', rel: 'noopener', title: `${r.shape}\n${r.component}` }, shortIri(r.shape))))))));
    if (q.truncated) sec.append(h('p', { class: 'muted small' }, `Showing the first ${q.results.length} results; the dataset report has them all.`));
  }
  const links = h('div', { class: 'row-actions' });
  if (q.html) links.append(h('a', { class: 'btn btn-small', href: api.qualityReportUrl(env, f.graph_iri, 'html'), target: '_blank', rel: 'noopener', title: 'The whole dataset report in shacl-report' }, 'Dataset report'));
  if (q.report) links.append(h('a', { class: 'btn btn-small', href: api.qualityReportUrl(env, f.graph_iri, 'ttl'), download: '', title: 'sh:ValidationReport (Turtle) of the whole dataset' }, 'Report .ttl'));
  links.append(h('button', { class: 'btn btn-small', onclick: () => store.set({ qa: true }), title: 'Ring features with issues on the map' }, 'Show issues on map'));
  sec.append(links);
  return sec;
}

function termCell(t: RdfTerm, labels: Record<string, string>): HTMLElement {
  if (t.type === 'uri') {
    const lbl = labels[t.value];
    return h('td', {},
      h('button', { class: 'link iri-btn', title: t.value, onclick: () => { stack.push(t.value); render(); } },
        lbl ? h('span', {}, lbl) : null, h('code', {}, prefixed(t.value))));
  }
  if (t.type === 'bnode') return h('td', {}, h('code', { class: 'muted' }, `_:${t.value}`));
  return h('td', { class: 'literal' }, t.value,
    t.lang ? h('span', { class: 'dt' }, ` @${t.lang}`) : t.datatype && !t.datatype.endsWith('#string') ? h('span', { class: 'dt' }, ` ${prefixed(t.datatype)}`) : null);
}

function renderDescribe(body: HTMLElement) {
  const iri = stack[stack.length - 1];
  const crumbs = h('nav', { class: 'crumbs', 'aria-label': 'Describe history' },
    stack.length > 1 ? h('button', { class: 'btn btn-small', onclick: () => { stack.pop(); render(); } }, '← Back') : null,
    ...stack.map((x, i) => i === stack.length - 1
      ? h('span', { class: 'crumb current', title: x }, shortIri(x))
      : h('button', { class: 'link crumb', title: x, onclick: () => { stack = stack.slice(0, i + 1); render(); } }, shortIri(x))));
  const dirToggle = h('div', { class: 'seg', role: 'group', 'aria-label': 'Direction' },
    ...(['out', 'in'] as const).map((d) => h('button', { class: 'seg-btn', 'aria-pressed': String(direction === d),
      onclick: () => { direction = d; render(); } }, d === 'out' ? 'Outgoing' : 'Incoming')));
  const tools = h('div', { class: 'describe-tools' }, dirToggle, h('span', { class: 'spacer' }));
  const table = h('div', { class: 'describe-table' }, h('p', { class: 'muted loading-text' }, 'Querying ERA…'));
  body.append(crumbs, tools, table);

  if (stack.length > 1) {
    featureOrNull(iri).then((f) => {
      if (f && stack[stack.length - 1] === iri) {
        tools.append(h('button', { class: 'btn btn-small', onclick: () => {
          store.set({ selected: f.iri, detailTab: 'properties' });
          if (f.geometry) fitGeometry(f.geometry);
        } }, 'Show on map'));
      }
    }).catch(() => { /* not a feature */ });
  }

  const dir = direction;
  describe(iri, dir).then((r) => {
    if (stack[stack.length - 1] !== iri || direction !== dir || !table.isConnected) return;
    if (!r.triples.length) { table.replaceChildren(h('p', { class: 'muted' }, dir === 'out' ? 'No outgoing triples.' : 'No incoming triples.')); return; }
    const sorted = [...r.triples].sort((a, b) => rank(a) - rank(b) || prefixed(a.p).localeCompare(prefixed(b.p)));
    const head = dir === 'out' ? ['Predicate', 'Object'] : ['Subject', 'Predicate'];
    table.replaceChildren(
      h('p', { class: 'muted small' }, `${r.triples.length} triple${r.triples.length === 1 ? '' : 's'}`),
      h('table', { class: 'triples' },
        h('thead', {}, h('tr', {}, ...head.map((x) => h('th', {}, x)))),
        h('tbody', {}, ...sorted.map((t) => {
          const pred = h('td', { class: 'pred' }, h('code', { title: t.p }, prefixed(t.p)));
          return dir === 'out'
            ? h('tr', {}, pred, termCell(t.o, r.labels))
            : h('tr', {}, termCell({ type: 'uri', value: t.s }, r.labels), pred);
        }))));
  }).catch((e) => {
    if (table.isConnected) table.replaceChildren(h('p', { class: 'error' }, `Describe failed: ${(e as Error).message}`));
  });
}

const rank = (t: Triple) => (t.p.endsWith('#type') ? 0 : t.p.endsWith('#label') || t.p.endsWith('#prefLabel') ? 1 : 2);

function renderGraph(body: HTMLElement) {
  const iri = stack[stack.length - 1];
  const holder = h('div', { class: 'graph' });
  body.classList.add('graph-body');
  body.append(h('p', { class: 'muted small graph-help' }, 'Double-click a blue node to expand it. Drag to rearrange.'), holder);
  // vis-network is large: load it only when the Graph tab is opened.
  import('./graph').then(({ mountGraph }) => {
    if (holder.isConnected) graph = mountGraph(holder, iri, describe, literalText);
  }).catch((e) => holder.replaceChildren(h('p', { class: 'error' }, `Graph failed to load: ${(e as Error).message}`)));
}

export function initDetail() {
  store.subscribe((s, prev) => {
    if (changed(s, prev, 'env')) { describeCache.clear(); featureCache.clear(); }
    if (changed(s, prev, 'selected', 'env')) {
      if (s.selected) open(s.selected);
      else {
        ctl?.abort(); graph?.destroy(); graph = null;
        panel.hidden = true; panel.replaceChildren();
        document.body.classList.remove('detail-open');
        setSelectionGeometry(null);
      }
    } else if (changed(s, prev, 'detailTab') && s.selected) render();
    else if (changed(s, prev, 'meta') && s.selected) {
      panel.querySelector('.kicker')?.replaceWith(kicker()); // class label/colour arrive with meta
      if (s.detailTab === 'properties') render();
    }
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && store.get().selected) close(); });
}
