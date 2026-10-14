// "Datasets" dialog: every provider graph the backend knows for the current
// environment, whether it has been processed, and how much it contributed.
import { api, type Dataset, type DatasetStatus } from './api';
import { fitBbox } from './map';
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
let sortKey: 'provider' | 'country' | 'status' | 'features' | 'modified' | 'ingested_at' = 'provider';
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
      'The backend checks ERA’s dataset explorer regularly and processes new or changed datasets automatically.'));
}

function sorted(list: Dataset[]): Dataset[] {
  const v = (d: Dataset): string | number => {
    switch (sortKey) {
      case 'features': return d.features ?? 0;
      case 'status': return STATUS_ORDER.indexOf(d.status);
      case 'modified': return d.modified ?? '';
      case 'ingested_at': return d.ingested_at ?? '';
      case 'country': return d.country ?? '';
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
  const body = list.map((d) => h('tr', { class: d.status === 'removed' ? 'removed' : '' },
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
    h('td', {}, when(d.ingested_at)),
    h('td', {}, d.status === 'loaded' && d.features
      ? h('button', { class: 'btn small', onclick: () => showOnMap(d.graph_iri) }, active.has(d.graph_iri) ? 'Shown' : 'Show on map')
      : null)));

  const filter = h('input', { type: 'search', class: 'facet-filter', placeholder: 'Filter by manager, country or file…', value: needle, 'aria-label': 'Filter datasets' });
  filter.addEventListener('input', () => { needle = filter.value; const pos = filter.selectionStart; render(); const f = dlg!.querySelector<HTMLInputElement>('.dlg-tools input'); f?.focus(); f?.setSelectionRange(pos, pos); });
  const rem = h('label', { class: 'toggle small' }, h('input', { type: 'checkbox', checked: showRemoved, onchange: () => { showRemoved = !showRemoved; render(); } }), ` Show removed (${removed})`);

  dlg.replaceChildren(head(), summary(rows),
    h('div', { class: 'dlg-tools' }, filter, removed ? rem : null),
    h('div', { class: 'dlg-scroll' }, h('table', { class: 'ds-table' },
      h('thead', {}, h('tr', {}, th('provider', 'Infrastructure manager'), th('country', 'Country'),
        h('th', {}, 'File'), th('status', 'Status'), th('features', 'Features', 'num'), th('ingested_at', 'Processed'), h('th', {}))),
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
    if (changed(s, prev, 'env') && s.env) fetchRows(s.env).catch(() => undefined); // labels for chips
    if (dlg?.open && changed(s, prev, 'meta')) render();
  });
}
