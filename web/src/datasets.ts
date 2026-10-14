// "Datasets" dialog: every provider graph the backend knows for the current
// environment, whether it has been processed, and how much it contributed.
import { api, QUALITY_VALUES, type Dataset, type DatasetQuality, type DatasetStatus, type QualityRule, type ShapesSource } from './api';
import { fitBbox } from './map';
import { shortIri } from './rdf';
import { changed, fmt, h, queryState, store } from './state';

const STATUS_TEXT: Record<DatasetStatus, string> = {
  loaded: 'Processed', loading: 'Processing', pending: 'Waiting', failed: 'Failed', removed: 'Removed',
};
const STATUS_ORDER: DatasetStatus[] = ['failed', 'loading', 'pending', 'loaded', 'removed'];

let rows: Dataset[] = [];
let rowsEnv = '';
const labels = new Map<string, string>();

/** Short label for a provider graph, for chips (falls back to the IM code). */
export function datasetLabel(graph: string): string {
  return labels.get(graph) ?? imCode(graph);
}

const imCode = (graph: string) => graph.replace(/\/$/, '').split('/').pop() ?? graph;

const when = (iso: string | null, time = true) => iso
  ? new Date(iso).toLocaleString(undefined, time ? { dateStyle: 'medium', timeStyle: 'short' } : { dateStyle: 'medium' })
  : '–';

async function fetchRows(env: string): Promise<Dataset[]> {
  rows = await api.datasets(env);
  rowsEnv = env;
  for (const d of rows) labels.set(d.graph_iri, d.provider ?? datasetLabel(d.graph_iri));
  return rows;
}

let dlg: HTMLDialogElement | null = null;
let sortKey: 'provider' | 'country' | 'status' | 'features' | 'modified' | 'ingested_at' | 'quality' = 'provider';
/** Datasets whose most frequent data quality rules are unfolded, and those rules once loaded. */
const openRules = new Set<string>();
const rulesCache = new Map<string, QualityRule[] | 'loading' | string>();
let sortDesc = false;
let needle = '';
let showRemoved = false;

export async function openDatasets() {
  const env = store.get().env;
  if (!dlg) {
    dlg = h('dialog', { class: 'datasets-dialog', 'aria-label': 'Datasets' });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg!.close(); }); // backdrop
    document.body.append(dlg);
  }
  dlg.replaceChildren(head(), h('p', { class: 'muted small pad' }, 'Loading datasets…'));
  dlg.showModal();
  try {
    await fetchRows(env);
    render();
  } catch (e) {
    dlg.replaceChildren(head(), h('p', { class: 'error small pad' }, `Could not load datasets: ${(e as Error).message}`));
  }
}

function head(): HTMLElement {
  const env = store.get().env.toUpperCase();
  return h('div', { class: 'dlg-head' },
    h('h2', {}, `Datasets · ${env}`),
    h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => dlg?.close() }, '×'));
}

function summary(list: Dataset[]): HTMLElement {
  const by = (st: DatasetStatus) => list.filter((d) => d.status === st).length;
  const live = list.filter((d) => d.status !== 'removed');
  const feats = live.reduce((n, d) => n + (d.features ?? 0), 0);
  const m = store.get().meta?.datasets;
  const parts: (string | HTMLElement)[] = [
    h('strong', {}, `${by('loaded')} of ${live.length}`), ' datasets processed',
    by('failed') ? h('span', { class: 'pill failed' }, `${by('failed')} failed`) : '',
    by('loading') + by('pending') ? h('span', { class: 'pill pending' }, `${by('loading') + by('pending')} in progress`) : '',
    ` · ${fmt.format(feats)} features indexed`,
  ];
  return h('div', { class: 'dlg-summary' },
    h('p', {}, ...parts),
    h('p', { class: 'muted small' },
      `Last dataset processed ${when(m?.last_ingest ?? null)} · last check for new datasets ${when(m?.last_check ?? null)}. `,
      'The backend checks ERA’s dataset explorer regularly and processes new or changed datasets automatically.'),
    qualitySummary(list));
}

const issueCount = (q?: DatasetQuality) => (q?.violations ?? 0) + (q?.warnings ?? 0) + (q?.infos ?? 0);
const qualityOn = () => !!store.get().meta?.quality?.enabled;

function qualitySummary(list: Dataset[]): HTMLElement | null {
  const qm = store.get().meta?.quality;
  if (!qm?.enabled) return null;
  const env = store.get().env;
  const live = list.filter((d) => d.status !== 'removed');
  const sum = (k: 'violations' | 'warnings' | 'infos') => live.reduce((n, d) => n + (d.quality?.[k] ?? 0), 0);
  const validated = live.filter((d) => d.quality?.status === 'ok' || d.quality?.status === 'partial').length;
  return h('div', { class: 'qa-summary small' },
    h('p', {},
      h('strong', {}, 'Data quality: '),
      h('span', { class: 'pill sev-violation' }, `${fmt.format(sum('violations'))} violations`), ' ',
      h('span', { class: 'pill sev-warning' }, `${fmt.format(sum('warnings'))} warnings`), ' ',
      sum('infos') ? h('span', { class: 'pill sev-info' }, `${fmt.format(sum('infos'))} info`) : '',
      ` in ${validated} of ${live.length} datasets, checked against `,
      qm.shapes ? shapesLink(qm.shapes) : 'the SHACL shapes ERA publishes',
      qm.shapes_loaded_at ? ` (loaded ${when(qm.shapes_loaded_at)})` : '', '.',
      qm.too_large ? ` ${qm.too_large} dataset${qm.too_large === 1 ? ' is' : 's are'} too large to validate` : '',
      qm.too_large && qm.max_triples ? ` (limit ${fmt.format(qm.max_triples)} triples).` : qm.too_large ? '.' : ''),
    qm.shapes_error ? h('p', { class: 'error' }, `The configured shapes could not be loaded: ${qm.shapes_error}`) : null,
    h('p', { class: 'muted' },
      'Each report is a standard SHACL validation report. ',
      qm.shapes_graph ? h('a', { href: api.qualityShapesUrl(env), download: '' }, 'Download the shapes') : null,
      qm.shacl_report_url ? h('span', {}, ' and explore a report together with them in ',
        h('a', { href: qm.shacl_report_url, target: '_blank', rel: 'noopener' }, 'shacl-report ↗'),
        ' (drop both files on the page), or open the built-in viewer with “Report”.') : '.'));
}

/** The shapes a validation used, linked to their source (GitLab file at a tag, or the shapes graph). */
function shapesLink(s: ShapesSource): HTMLElement {
  const text = s.kind === 'tag' ? `ERA ontology ${s.tag}` : `ERA's shapes graph${s.version ? ` v${s.version}` : ''}`;
  const title = [s.label, s.url ?? s.graph, s.version ? `owl:versionInfo ${s.version}` : '', s.modified ? `modified ${s.modified}` : '']
    .filter(Boolean).join('\n');
  return s.url?.startsWith('http')
    ? h('a', { href: s.url, target: '_blank', rel: 'noopener', title }, `${text} shapes`)
    : h('span', { title }, `${text} shapes`);
}

const shapesKey = (s?: ShapesSource | null) => (s ? `${s.kind}|${s.tag ?? ''}|${s.version ?? ''}|${s.graph}` : '');

function qualityCell(d: Dataset): HTMLElement {
  const q = d.quality;
  const td = h('td', { class: 'qa-cell' });
  if (d.status === 'removed' || !q) return td;
  if (q.status === null) { td.append(h('span', { class: 'muted small' }, d.status === 'loaded' ? 'Waiting' : '–')); return td; }
  if (q.reason === 'too_large') {
    // Not "no issues": a dataset above the size limit has no counts and no quality filter values.
    const size = d.triples && q.limit ? `${fmt.format(d.triples)} triples > limit ${fmt.format(q.limit)}` : (q.note ?? '');
    td.append(h('span', { class: 'pill', title: q.note ?? '' }, 'Not validated: too large'),
      h('small', { class: 'muted' }, `(${size})`));
    return td;
  }
  if (q.status === 'skipped' || q.status === 'failed') {
    td.append(h('span', { class: `pill ${q.status === 'failed' ? 'failed' : ''}`, title: q.note ?? '' }, q.status === 'failed' ? 'Check failed' : 'Not validated'),
      q.note ? h('small', { class: 'muted err', title: q.note }, q.note) : '');
    return td;
  }
  if (!issueCount(q)) td.append(h('span', { class: 'pill sev-ok' }, 'No issues'));
  else {
    const open = openRules.has(d.graph_iri);
    td.append(h('button', { class: 'link qa-counts', 'aria-expanded': String(open), title: 'Show the most frequent problems',
      onclick: () => { open ? openRules.delete(d.graph_iri) : openRules.add(d.graph_iri); loadRules(d.graph_iri); render(); } },
    q.violations ? h('span', { class: 'pill sev-violation' }, `${fmt.format(q.violations)} violation${q.violations === 1 ? '' : 's'}`) : null,
    q.warnings ? h('span', { class: 'pill sev-warning' }, `${fmt.format(q.warnings)} warning${q.warnings === 1 ? '' : 's'}`) : null,
    q.infos ? h('span', { class: 'pill sev-info' }, `${fmt.format(q.infos)} info`) : null,
    h('span', { class: 'chev', 'aria-hidden': 'true' })));
  }
  // Shapes other than the environment's current ones (e.g. checked before the tag was changed).
  const current = store.get().meta?.quality?.shapes;
  const older = q.shapes && current && shapesKey(q.shapes) !== shapesKey(current)
    ? `with ${q.shapes.tag ?? (q.shapes.version ? `v${q.shapes.version}` : 'other shapes')}` : '';
  const notes = [q.status === 'partial' ? 'partial check' : '', q.focus_nodes ? `${fmt.format(q.focus_nodes)} resources affected` : '', older].filter(Boolean);
  if (notes.length) td.append(h('small', { class: 'muted', title: [q.note, q.shapes?.label].filter(Boolean).join('\n') }, notes.join(' · ')));
  const links = h('div', { class: 'qa-links' });
  if (q.html) links.append(h('a', { href: api.qualityReportUrl(store.get().env, d.graph_iri, 'html'), target: '_blank', rel: 'noopener', title: 'Open the report in shacl-report’s viewer' }, 'Report'));
  if (q.report) links.append(h('a', { href: api.qualityReportUrl(store.get().env, d.graph_iri, 'ttl'), download: '', title: 'sh:ValidationReport as Turtle' }, '.ttl'));
  if (issueCount(q) && d.features) links.append(h('button', { class: 'link', onclick: () => showIssues(d.graph_iri), title: 'Show this dataset’s features with violations or warnings on the map' }, 'Issues on map'));
  if (links.childElementCount) td.append(links);
  return td;
}

function loadRules(graph: string) {
  if (rulesCache.has(graph) && rulesCache.get(graph) !== 'loading' && typeof rulesCache.get(graph) !== 'string') return;
  rulesCache.set(graph, 'loading');
  api.qualitySummary(store.get().env, graph)
    .then((r) => rulesCache.set(graph, r))
    .catch((e) => rulesCache.set(graph, `Could not load: ${(e as Error).message}`))
    .finally(() => render());
}

function rulesRow(d: Dataset, cols: number): HTMLElement {
  const r = rulesCache.get(d.graph_iri);
  const body = r === undefined || r === 'loading' ? h('p', { class: 'muted small' }, 'Loading…')
    : typeof r === 'string' ? h('p', { class: 'error small' }, r)
      : h('table', { class: 'qa-rules' }, h('tbody', {}, ...r.map((x) => h('tr', {},
        h('td', { class: 'num' }, fmt.format(x.count)),
        h('td', {}, h('span', { class: `pill sev-${x.severity}` }, x.severity)),
        h('td', {}, h('div', {}, x.message ?? shortIri(x.component)),
          h('small', { class: 'muted' }, h('a', { href: x.shape.startsWith('http') ? x.shape : undefined, target: '_blank', rel: 'noopener', title: x.shape }, shortIri(x.shape)),
            ` · ${shortIri(x.component).replace('ConstraintComponent', '')}`))))));
  return h('tr', { class: 'qa-rules-row' }, h('td', { colspan: cols }, body));
}

async function showIssues(graph: string) {
  const filters = { ...store.get().filters, quality: [QUALITY_VALUES.violation, QUALITY_VALUES.warning] };
  store.set({ filters, qa: true });
  await showOnMap(graph);
}

function sorted(list: Dataset[]): Dataset[] {
  const v = (d: Dataset): string | number => {
    switch (sortKey) {
      case 'features': return d.features ?? 0;
      case 'status': return STATUS_ORDER.indexOf(d.status);
      case 'modified': return d.modified ?? '';
      case 'ingested_at': return d.ingested_at ?? '';
      case 'country': return d.country ?? '';
      case 'quality': return issueCount(d.quality) * 1e3 + (d.quality?.status ? 1 : 0);
      default: return (d.provider ?? d.graph_iri).toLowerCase();
    }
  };
  const out = [...list].sort((a, b) => (v(a) < v(b) ? -1 : v(a) > v(b) ? 1 : 0));
  return sortDesc ? out.reverse() : out;
}

function render() {
  if (!dlg || rowsEnv !== store.get().env) return;
  const removed = rows.filter((d) => d.status === 'removed').length;
  const n = needle.toLowerCase();
  const list = sorted(rows.filter((d) => (showRemoved || d.status !== 'removed') &&
    (!n || [d.provider, d.country, d.file_name, d.title, d.graph_iri].some((x) => x?.toLowerCase().includes(n)))));

  const th = (k: typeof sortKey, label: string, cls = '') => h('th', {
    class: cls, 'aria-sort': sortKey === k ? (sortDesc ? 'descending' : 'ascending') : 'none',
  }, h('button', { class: 'sort', onclick: () => { sortDesc = sortKey === k ? !sortDesc : k === 'features' || k === 'ingested_at' || k === 'modified'; sortKey = k; render(); } },
    label, sortKey === k ? (sortDesc ? ' ↓' : ' ↑') : ''));

  const active = new Set(store.get().graphs);
  const qa = qualityOn();
  const cols = qa ? 8 : 7;
  const body = list.flatMap((d) => [h('tr', { class: d.status === 'removed' ? 'removed' : '' },
    h('td', {}, h('div', {}, d.provider ?? '–'), h('small', { class: 'muted mono', title: d.graph_iri }, imCode(d.graph_iri))),
    h('td', {}, d.country ?? '–'),
    h('td', { class: 'file' },
      h('div', { title: d.file_name ?? d.title ?? '' }, d.file_name ?? d.title ?? '–'),
      h('small', { class: 'muted' }, `Published ${when(d.modified, false)}`)),
    h('td', {},
      h('span', { class: `pill ${d.status}` }, STATUS_TEXT[d.status] ?? d.status),
      d.error ? h('div', { class: 'error small err', title: d.error }, d.error.split('\n')[0]) : null),
    h('td', { class: 'num' }, fmt.format(d.features ?? 0),
      d.triples ? h('small', { class: 'muted' }, `${fmt.format(d.triples)} triples`) : null),
    qa ? qualityCell(d) : null,
    h('td', {}, when(d.ingested_at)),
    h('td', {}, d.status === 'loaded' && d.features
      ? h('button', { class: 'btn small', onclick: () => showOnMap(d.graph_iri) }, active.has(d.graph_iri) ? 'Shown' : 'Show on map')
      : null)),
  qa && openRules.has(d.graph_iri) ? rulesRow(d, cols) : null].filter((x): x is HTMLTableRowElement => x !== null));

  const filter = h('input', { type: 'search', class: 'facet-filter', placeholder: 'Filter by manager, country or file…', value: needle, 'aria-label': 'Filter datasets' });
  filter.addEventListener('input', () => { needle = filter.value; const pos = filter.selectionStart; render(); const f = dlg!.querySelector<HTMLInputElement>('.dlg-tools input'); f?.focus(); f?.setSelectionRange(pos, pos); });
  const rem = h('label', { class: 'toggle small' }, h('input', { type: 'checkbox', checked: showRemoved, onchange: () => { showRemoved = !showRemoved; render(); } }), ` Show removed (${removed})`);

  dlg.replaceChildren(head(), summary(rows),
    h('div', { class: 'dlg-tools' }, filter, removed ? rem : null),
    h('div', { class: 'dlg-scroll' }, h('table', { class: 'ds-table' },
      h('thead', {}, h('tr', {}, th('provider', 'Infrastructure manager'), th('country', 'Country'),
        h('th', {}, 'File'), th('status', 'Status'), th('features', 'Features', 'num'),
        qa ? th('quality', 'Data quality') : null, th('ingested_at', 'Processed'), h('th', {}))),
      h('tbody', {}, ...body))));
  if (!list.length) dlg.append(h('p', { class: 'muted small pad' }, 'No datasets match.'));
}

async function showOnMap(graph: string) {
  store.set({ graphs: [graph], sideTab: 'filters' });
  dlg?.close();
  try {
    const { bbox } = await api.extent(queryState(store.get(), false));
    if (bbox) fitBbox(bbox, 12);
  } catch { /* the filter still applies */ }
}

export function initDatasets() {
  store.subscribe((s, prev) => {
    if (changed(s, prev, 'env') && s.env) { openRules.clear(); rulesCache.clear(); fetchRows(s.env).catch(() => undefined); } // labels for chips
    if (dlg?.open && changed(s, prev, 'meta')) render();
  });
}
