// "Export" dialog: quick facet exports (CSV / Excel straight from the index) and
// full exports, which the worker prepares from its RDF mirror as a job: every RINF
// property of every matching element, as RDF (Turtle, N-Triples, JSON-LD), tables
// (CSV, Excel; columns from an export profile or one row per value) or GIS files
// (GeoJSON, GeoPackage). The dialog queues the job, shows its progress and the
// download link, and remembers this browser's recent exports.
import { api, type ExportColumn, type ExportFormat, type ExportJob, type ExportProfiles } from './api';
import { fmt, h, queryState, store } from './state';

const RECENT_KEY = 'rinf-atlas.exports';
const MAX_RECENT = 8;

interface Recent { id: string; env: string; format: string; profile: string | null; created: string }

let dlg: HTMLDialogElement | null = null;
let body: HTMLDivElement | null = null;
let profiles: ExportProfiles | null = null;
let profilesEnv = '';
let total = 0;
let polling: ReturnType<typeof setTimeout> | null = null;
/** Jobs shown in the dialog (the one just queued first), refreshed while open. */
const jobs = new Map<string, ExportJob>();

// -- options kept while the dialog is open -----------------------------------------
let format: string = 'ttl';
let columns: 'profile' | 'custom' = 'profile';
let shape: 'wide' | 'long' = 'wide';
let headers: 'label' | 'rinf' | 'path' = 'label';
let values: 'label' | 'iri' | 'both' = 'label';
/** Quick export while a view is open: only the view's parameters and checks as columns. */
let viewCols = true;
let parts = false;
let gzip = false;
/** Column keys ticked per class in the custom tree (a parent key covers its children). */
const custom = new Map<string, Set<string>>();

export function setResultTotal(n: number) { total = n; }

function recent(): Recent[] {
  try { return (JSON.parse(localStorage.getItem(RECENT_KEY) ?? '[]') as Recent[]).slice(0, MAX_RECENT); } catch { return []; }
}
function remember(job: ExportJob) {
  const list = [{ id: job.id, env: job.env, format: job.format, profile: job.profile, created: job.created_at }, ...recent().filter((r) => r.id !== job.id)];
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, MAX_RECENT))); } catch { /* private mode */ }
}
function forget(id: string) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(recent().filter((r) => r.id !== id))); } catch { /* ignore */ }
  jobs.delete(id);
}

const size = (n: number | null) => n == null ? '' : n < 1e6 ? `${fmt.format(Math.max(1, Math.round(n / 1e3)))} kB` : `${(n / 1e6).toFixed(n < 1e7 ? 1 : 0)} MB`;
const when = (iso: string | null) => iso ? new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '';

/** Quick export of the facet columns: an immediate browser download from the index. */
function quick(f: 'csv' | 'xlsx') {
  const s = store.get();
  const a = h('a', { href: api.exportUrl(queryState(s), f, viewCols && s.lens ? s.lens : null), download: '' });
  document.body.append(a);
  a.click();
  a.remove();
}

export async function openExport() {
  const s = store.get();
  if (!s.env) return;
  if (!dlg) {
    dlg = h('dialog', { class: 'datasets-dialog about-dialog export-dialog', 'aria-label': 'Export' });
    body = h('div', { class: 'about-body' });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg!.close(); });
    dlg.addEventListener('close', () => { if (polling) { clearTimeout(polling); polling = null; } });
    dlg.append(
      h('div', { class: 'dlg-head' },
        h('h2', {}, 'Export'),
        h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => dlg?.close() }, '×')),
      body);
    document.body.append(dlg);
  }
  body!.replaceChildren(h('p', { class: 'muted small' }, 'Loading export options…'));
  dlg.showModal();
  try {
    if (!profiles || profilesEnv !== s.env) { profiles = await api.exportProfiles(s.env); profilesEnv = s.env; }
  } catch (e) {
    body!.replaceChildren(h('p', { class: 'error small' }, `Could not load the export options: ${(e as Error).message}`));
    return;
  }
  render();
  refreshJobs();
}

function classesInSelection(): string[] {
  const s = store.get();
  const all = (s.meta?.classes ?? []).filter((c) => c.count > 0).map((c) => c.class);
  return s.classes.length ? all.filter((c) => s.classes.includes(c)) : all;
}

function fmtInfo(): ExportFormat | undefined { return profiles?.formats.find((f) => f.key === format); }
const isRdf = () => fmtInfo()?.kind === 'rdf';
const isTable = () => fmtInfo()?.kind === 'table';
const needsProfile = () => !isRdf() && !(isTable() && shape === 'long');

function render() {
  if (!body || !profiles) return;
  const s = store.get();
  const p = profiles;
  const prof = p.profiles[0];
  const max = p.limits.max_entities;
  const over = max != null && total > max;
  const n = classesInSelection().length;

  const fmtSel = h('select', { class: 'export-select', 'aria-label': 'Format',
    onchange: (e: Event) => { format = (e.target as HTMLSelectElement).value; render(); } });
  for (const kind of [['rdf', 'RDF, every property resolved'], ['table', 'Tables'], ['gis', 'GIS']] as const) {
    const g = h('optgroup', { label: kind[1] });
    for (const f of p.formats.filter((x) => x.kind === kind[0])) {
      const o = h('option', { value: f.key }, f.label);
      if (f.key === format) o.selected = true;
      g.append(o);
    }
    fmtSel.append(g);
  }

  const radio = (name: string, value: string, label: string, checked: boolean, onchange: () => void, title?: string) =>
    h('label', { class: 'export-radio', title }, h('input', { type: 'radio', name, value, checked, onchange }), ' ', label);
  const check = (label: string, checked: boolean, onchange: (v: boolean) => void, title?: string) =>
    h('label', { class: 'export-radio', title }, h('input', { type: 'checkbox', checked, onchange: (e: Event) => onchange((e.target as HTMLInputElement).checked) }), ' ', label);

  const opts: HTMLElement[] = [];
  if (isTable()) {
    opts.push(h('div', { class: 'export-row' }, h('span', { class: 'export-k' }, 'Rows'),
      radio('shape', 'wide', 'One row per element', shape === 'wide', () => { shape = 'wide'; render(); }, 'Columns from the profile below; several values in a cell are joined with "; ".'),
      radio('shape', 'long', 'One row per value', shape === 'long', () => { shape = 'long'; render(); }, 'Element, property path, value: every property in the data, no profile needed.')));
  }
  if (needsProfile()) {
    if (!prof) {
      opts.push(h('p', { class: 'error small' }, 'This instance has no export profile (shapes/export/*.ttl), so tables and GIS files need the "one row per value" layout.'));
    } else {
      opts.push(h('div', { class: 'export-row' }, h('span', { class: 'export-k' }, 'Columns'),
        radio('cols', 'profile', `${prof.label}: every RINF property of each class`, columns === 'profile', () => { columns = 'profile'; render(); }, prof.comment ?? ''),
        radio('cols', 'custom', 'Choose columns…', columns === 'custom', () => { columns = 'custom'; render(); })));
      if (columns === 'custom') opts.push(tree(prof.classes));
      opts.push(h('div', { class: 'export-row' }, h('span', { class: 'export-k' }, 'Headers'),
        radio('hdr', 'label', 'Names', headers === 'label', () => { headers = 'label'; }),
        radio('hdr', 'rinf', 'RINF number and name', headers === 'rinf', () => { headers = 'rinf'; }),
        radio('hdr', 'path', 'Property path', headers === 'path', () => { headers = 'path'; }, 'Dotted local names, e.g. contactLineSystem.energySupplySystem')));
    }
  }
  if (!isRdf()) {
    opts.push(h('div', { class: 'export-row' }, h('span', { class: 'export-k' }, 'Values'),
      radio('vals', 'label', 'Labels', values === 'label', () => { values = 'label'; }),
      radio('vals', 'iri', 'IRIs', values === 'iri', () => { values = 'iri'; }),
      radio('vals', 'both', 'Both', values === 'both', () => { values = 'both'; })));
  }
  const extra: HTMLElement[] = [
    check('Include the parts of each element (tracks of a section, platforms of a track…)', parts, (v) => { parts = v; }, 'Elements reached through era:hasPart are resolved too, one level down.'),
  ];
  if (!['xlsx', 'gpkg'].includes(format)) extra.push(check('Compress (gzip)', gzip, (v) => { gzip = v; }));
  opts.push(h('div', { class: 'export-row export-checks' }, ...extra));

  const btn = h('button', { class: 'btn btn-primary', type: 'button', disabled: over || total === 0 || (needsProfile() && !prof) }, 'Prepare export');
  btn.addEventListener('click', () => void queue(btn));

  body.replaceChildren(
    h('p', { class: 'export-sum' },
      h('b', {}, `${fmt.format(total)} ${total === 1 ? 'element' : 'elements'}`),
      ` in ${n} ${n === 1 ? 'class' : 'classes'} match the current filters${s.limitToView ? ' and map view' : ''}.`),
    h('section', { class: 'export-sec' },
      h('h3', {}, 'Quick export'),
      h('p', { class: 'muted small' }, 'The columns you see as facets, straight from the index. Downloads at once.'),
      s.lens ? h('label', { class: 'export-radio small', title: 'Keep only the columns of the open view' },
        h('input', { type: 'checkbox', checked: viewCols, onchange: (e: Event) => { viewCols = (e.target as HTMLInputElement).checked; } }),
        ` Only the parameters and checks of the view “${s.meta?.views?.find((v) => v.key === s.lens)?.label ?? s.lens}”`) : null,
      h('div', { class: 'export-btns' },
        h('button', { class: 'btn btn-small', type: 'button', onclick: () => quick('csv') }, 'CSV'),
        h('button', { class: 'btn btn-small', type: 'button', onclick: () => quick('xlsx') }, 'Excel'))),
    h('section', { class: 'export-sec' },
      h('h3', {}, 'Full export'),
      h('p', { class: 'muted small' }, 'Every property RINF holds for each element, resolved from the local copy of the ERA data: components such as net references, geometries, validity periods, contact line systems or ETCS are included; other elements and concepts are referenced with their labels. Prepared in the background; the link appears here when it is ready.'),
      h('div', { class: 'export-row' }, h('span', { class: 'export-k' }, 'Format'), fmtSel,
        h('span', { class: 'muted small export-fmt-desc' }, fmtInfo()?.description ?? '')),
      ...opts,
      over ? h('p', { class: 'error small' }, `This instance limits exports to ${fmt.format(max!)} elements; narrow the selection (${fmt.format(total)} now).`) : null,
      h('div', { class: 'export-btns' }, btn, h('span', { id: 'export-queue-msg', class: 'muted small' }))),
    h('section', { class: 'export-sec', id: 'export-jobs' }, h('h3', {}, 'Your exports'), h('p', { class: 'muted small' }, 'Nothing yet.')),
  );
  renderJobs();
}

/** Checkbox tree of the profile's columns per class in the selection. */
function tree(classes: ExportProfiles['profiles'][number]['classes']): HTMLElement {
  const s = store.get();
  const labels = new Map((s.meta?.classes ?? []).map((c) => [c.class, c.label]));
  const box = h('div', { class: 'export-tree' });
  for (const cls of classesInSelection()) {
    const cp = classes[cls];
    const det = h('details', { class: 'export-class' });
    const sum = h('summary', {}, h('b', {}, labels.get(cls) ?? cls));
    det.append(sum);
    if (!cp) {
      det.append(h('p', { class: 'muted small'}, 'No profile columns for this class: its rows carry the base columns only.'));
      box.append(det);
      continue;
    }
    if (!custom.has(cls)) custom.set(cls, new Set(cp.columns.map((c) => c.key)));
    const sel = custom.get(cls)!;
    sum.append(' ', h('span', { class: 'muted small' }, `${countLeaves(cp.columns, sel)} of ${leaves(cp.columns).length} columns`));
    const tools = h('div', { class: 'export-tree-tools' },
      h('button', { class: 'btn btn-small', type: 'button', onclick: () => { cp.columns.forEach((c) => sel.add(c.key)); render(); } }, 'All'),
      h('button', { class: 'btn btn-small', type: 'button', onclick: () => { sel.clear(); render(); } }, 'None'));
    det.append(tools, list(cp.columns, sel, 0));
    det.open = classesInSelection().length === 1;
    box.append(det);
  }
  return box;
}

const leaves = (cols: ExportColumn[]): ExportColumn[] => cols.flatMap((c) => c.children?.length ? leaves(c.children) : [c]);
function covered(key: string, sel: Set<string>) {
  const parts = key.split('.');
  return parts.some((_, i) => sel.has(parts.slice(0, i + 1).join('.')));
}
const countLeaves = (cols: ExportColumn[], sel: Set<string>) => leaves(cols).filter((c) => covered(c.key, sel)).length;

function list(cols: ExportColumn[], sel: Set<string>, depth: number): HTMLElement {
  const ul = h('ul', { class: 'export-cols' });
  for (const c of cols) {
    const parentOn = covered(c.key.split('.').slice(0, -1).join('.') || '\u0000', sel);
    const on = parentOn || covered(c.key, sel);
    const cb = h('input', { type: 'checkbox', checked: on, disabled: parentOn && !sel.has(c.key) });
    cb.addEventListener('change', () => {
      if (cb.checked) sel.add(c.key);
      else { sel.delete(c.key); for (const k of [...sel]) if (k.startsWith(c.key + '.')) sel.delete(k); }
      render();
    });
    const label = c.label.split(' / ').pop() ?? c.label;
    const li = h('li', {}, h('label', { title: c.path.join(' / ') }, cb, ' ', c.rinf ? h('span', { class: 'export-rinf' }, c.rinf) : null, label));
    if (c.children?.length && depth < 4) {
      const det = h('details', {}, h('summary', {}, h('span', { class: 'muted small' }, `${c.children.length} sub-columns`)), list(c.children, sel, depth + 1));
      li.append(det);
    }
    ul.append(li);
  }
  return ul;
}

async function queue(btn: HTMLButtonElement) {
  const s = store.get();
  const msg = document.getElementById('export-queue-msg');
  btn.disabled = true;
  if (msg) msg.textContent = 'Queuing…';
  try {
    const prof = profiles?.profiles[0];
    const include = columns === 'custom' && prof && needsProfile()
      ? classesInSelection().flatMap((cls) => [...(custom.get(cls) ?? [])])
      : null;
    const job = await api.createExport({
      state: queryState(s), format, profile: needsProfile() ? prof?.key ?? null : null,
      include: include ? [...new Set(include)] : null, values, headers, shape: isTable() ? shape : 'wide', parts, gzip,
    });
    remember(job);
    jobs.set(job.id, job);
    if (msg) msg.textContent = '';
    renderJobs();
    poll();
  } catch (e) {
    if (msg) { msg.textContent = (e as Error).message; msg.className = 'error small'; }
  } finally {
    btn.disabled = false;
  }
}

async function refreshJobs() {
  const ids = recent().map((r) => r.id);
  await Promise.all(ids.map(async (id) => {
    try { jobs.set(id, await api.exportStatus(id)); } catch (e) { if ((e as { status?: number }).status === 404) forget(id); }
  }));
  renderJobs();
  poll();
}

function poll() {
  if (polling) clearTimeout(polling);
  const active = [...jobs.values()].some((j) => j.status === 'queued' || j.status === 'running');
  if (!active || !dlg?.open) return;
  polling = setTimeout(async () => {
    for (const j of [...jobs.values()]) {
      if (j.status === 'queued' || j.status === 'running') {
        try { jobs.set(j.id, await api.exportStatus(j.id)); } catch { /* next tick */ }
      }
    }
    renderJobs();
    poll();
  }, 1500);
}

const STATUS: Record<string, string> = { queued: 'Waiting', running: 'Preparing', done: 'Ready', failed: 'Failed', cancelled: 'Cancelled', expired: 'Expired' };

function renderJobs() {
  const sec = document.getElementById('export-jobs');
  if (!sec) return;
  const list = [...jobs.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const rows = list.map((j) => {
    const pct = j.total ? Math.min(100, Math.round(100 * (j.done ?? 0) / j.total)) : 0;
    const fmtLabel = profiles?.formats.find((f) => f.key === j.format)?.label ?? j.format.toUpperCase();
    const title = `${fmtLabel}${j.profile ? `, ${j.profile}` : ''}${j.shape === 'long' ? ', one row per value' : ''}${j.parts ? ', with parts' : ''} · ${j.env.toUpperCase()} · ${fmt.format(j.total ?? 0)} elements`;
    const actions: (Node | null)[] = [];
    if (j.status === 'done' && j.download) {
      actions.push(h('a', { class: 'btn btn-small btn-primary', href: j.download, download: j.filename ?? '' }, `Download ${size(j.bytes)}`));
    }
    if (j.status === 'queued' || j.status === 'running') {
      actions.push(h('button', { class: 'btn btn-small', type: 'button', onclick: () => void api.cancelExport(j.id).then(() => refreshJobs()) }, 'Cancel'));
    }
    actions.push(h('a', { class: 'small', href: j.recipe, target: '_blank', rel: 'noopener', title: 'How this file was made, so it can be checked against ERA' }, 'Recipe'));
    if (j.status !== 'queued' && j.status !== 'running') {
      actions.push(h('button', { class: 'icon-btn small', type: 'button', 'aria-label': 'Remove from this list', onclick: () => { forget(j.id); if (j.status === 'done') void api.cancelExport(j.id).catch(() => undefined); renderJobs(); } }, '×'));
    }
    return h('div', { class: `export-job export-${j.status}` },
      h('div', { class: 'export-job-head' },
        h('span', { class: 'export-job-title' }, title),
        h('span', { class: 'muted small' }, `${STATUS[j.status] ?? j.status}${j.status === 'running' && j.total ? ` ${pct}%` : ''} · ${when(j.created_at)}`)),
      j.status === 'running' || j.status === 'queued'
        ? h('progress', { class: 'export-progress', max: 100, value: j.status === 'running' ? pct : 0 })
        : null,
      j.warning ? h('p', { class: 'export-warn small' }, j.warning) : null,
      j.error ? h('p', { class: 'error small' }, j.error) : null,
      j.status === 'done' && j.expires_at ? h('p', { class: 'muted small' }, `Available until ${when(j.expires_at)}.`) : null,
      h('div', { class: 'export-btns' }, ...actions));
  });
  sec.replaceChildren(h('h3', {}, 'Your exports'), ...(rows.length ? rows : [h('p', { class: 'muted small' }, 'Nothing yet. Exports prepared in this browser are listed here while their files are kept.')]));
}
