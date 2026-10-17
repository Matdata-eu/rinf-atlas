// "Facet shapes" dialog: the SHACL shapes in this instance's shapes/ folder, as written by
// whoever runs it. They decide the classes, the filters and how each element gets its
// position on the map, so the app shows them as they are.
import { api, type ShapeFile } from './api';
import { h } from './state';

let dlg: HTMLDialogElement | null = null;
let files: Promise<ShapeFile[]> | null = null;
const texts = new Map<string, Promise<string>>();

function text(name: string): Promise<string> {
  let p = texts.get(name);
  if (!p) {
    p = fetch(api.shapeFileUrl(name)).then((r) => { if (!r.ok) throw new Error(`${r.status} ${r.statusText}`); return r.text(); });
    p.catch(() => texts.delete(name));
    texts.set(name, p);
  }
  return p;
}

const fileLabel = (f: ShapeFile) =>
  f.classes.length ? `${f.classes.map((c) => c.label).join(', ')} (${f.name})`
    : f.name === 'atlas.ttl' ? `Shape vocabulary (${f.name})`
      : f.name === 'shared.ttl' ? `Filters for every class (${f.name})`
        : f.name === 'compatibility.ttl' ? `Vehicle check rules (${f.name})` : f.name;

/** Opens the dialog on one shape file (default: the shape vocabulary, which documents the rest). */
export function openShapes(name?: string) {
  if (!dlg) {
    dlg = h('dialog', { class: 'datasets-dialog about-dialog shapes-dialog', 'aria-label': 'Facet shapes' });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg!.close(); });
    document.body.append(dlg);
  }
  const select = h('select', { class: 'compat-select', 'aria-label': 'Shape file' });
  const download = h('a', { class: 'btn btn-small', download: '' }, 'Download');
  const out = h('pre', { class: 'shape-ttl' }, 'Loading…');
  const show = (n: string) => {
    download.href = api.shapeFileUrl(n, true);
    out.textContent = 'Loading…';
    text(n).then((t) => { if (select.value === n) out.textContent = t; })
      .catch((e) => { if (select.value === n) out.textContent = `Could not load ${n}: ${(e as Error).message}`; });
  };
  select.addEventListener('change', () => show(select.value));
  dlg.replaceChildren(
    h('div', { class: 'dlg-head' },
      h('h2', {}, 'Facet shapes'),
      h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => dlg?.close() }, '×')),
    h('div', { class: 'about-body shapes-body' },
      h('p', { class: 'muted small' }, 'Whoever runs this site defines which classes are shown, which filters they have and ',
        'how each element gets its position on the map, in SHACL shapes with a few extra annotations. ',
        'These are the shapes this site uses; ', h('code', {}, 'atlas.ttl'), ' explains the annotations.'),
      h('div', { class: 'shapes-pick' }, select, download),
      out));
  files ??= api.shapes();
  files.then((fs) => {
    if (!fs.length) files = null; // ask again next time
    if (!fs.length) { out.textContent = 'No shapes have been published yet.'; download.hidden = true; return; }
    const sorted = [...fs].sort((a, b) => Number(!a.classes.length) - Number(!b.classes.length) || fileLabel(a).localeCompare(fileLabel(b)));
    select.replaceChildren(...sorted.map((f) => h('option', { value: f.name }, fileLabel(f))));
    const want = name && fs.some((f) => f.name === name) ? name : fs.some((f) => f.name === 'atlas.ttl') ? 'atlas.ttl' : sorted[0].name;
    select.value = want;
    show(want);
  }).catch((e) => { files = null; out.textContent = `Could not load the shapes: ${(e as Error).message}`; });
  if (!dlg.open) dlg.showModal();
}
