// "Open in GIS": copies the OGC API - Features URL of the current classes and
// filters, and shows the other URLs a desktop GIS (QGIS, ArcGIS Pro) can use.
import { api } from './api';
import { h, isFilterEmpty, queryState, store } from './state';

let dlg: HTMLDialogElement | null = null;
let body: HTMLDivElement | null = null;

function copy(text: string, btn: HTMLButtonElement) {
  const done = () => { btn.textContent = 'Copied'; setTimeout(() => { btn.textContent = 'Copy'; }, 1500); };
  navigator.clipboard?.writeText(text).then(done, () => { /* the URL is selectable in the dialog */ });
}

function urlRow(label: string, url: string, hint: string, autoCopy = false) {
  const input = h('input', { type: 'text', readonly: true, value: url, class: 'gis-url', 'aria-label': label,
    onfocus: (e: Event) => (e.target as HTMLInputElement).select() });
  const btn = h('button', { class: 'btn btn-small', type: 'button' }, 'Copy');
  btn.addEventListener('click', () => copy(url, btn));
  if (autoCopy) copy(url, btn);
  return h('div', { class: 'gis-row' },
    h('label', {}, label), h('div', { class: 'gis-input' }, input, btn), h('p', { class: 'muted small' }, hint));
}

interface OgcCollection { id: string; title: string; links: { rel: string; href: string }[] }

/** GeoJSON links of the selected class: one, or one per geometry type when it mixes them. */
async function fillItems(box: HTMLElement, ogc: string, collection: string) {
  try {
    const res = await fetch(`${ogc}/collections`);
    if (!res.ok) return;
    const { collections } = (await res.json()) as { collections: OgcCollection[] };
    const mine = collections.filter((c) => c.id === collection || c.id.startsWith(`${collection}.`));
    box.replaceChildren(...mine.map((c) => {
      const href = c.links.find((l) => l.rel === 'items')?.href ?? `${ogc}/collections/${c.id}/items`;
      return urlRow(mine.length > 1 ? `GeoJSON: ${c.title}` : 'GeoJSON of this class', href,
        'Paged GeoJSON (add ?limit=1000; follow the "next" links). Opens in any tool that reads GeoJSON.'
        + (mine.length > 1 ? ' This class mixes geometry types, so each type is its own layer.' : ''));
    }));
  } catch { /* the OGC API URL above still works */ }
}

export function openGis() {
  const s = store.get();
  if (!s.env) return;
  const st = queryState(s, false);
  st.filters = Object.fromEntries(Object.entries(st.filters).filter(([, v]) => !isFilterEmpty(v)));
  const u = api.gisUrls(st);
  if (!dlg) {
    dlg = h('dialog', { class: 'datasets-dialog about-dialog gis-dialog', 'aria-label': 'Open in GIS' });
    body = h('div', { class: 'about-body' });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg!.close(); });
    dlg.append(
      h('div', { class: 'dlg-head' },
        h('h2', {}, 'Open in GIS'),
        h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => dlg?.close() }, '×')),
      body);
    document.body.append(dlg);
  }
  const filtered = u.ogc.includes('/ogc/filtered/');
  const items = h('div', {});
  if (u.collection) void fillItems(items, u.ogc, u.collection);
  body!.replaceChildren(
    h('p', {}, 'Load Atlas layers straight into QGIS, ArcGIS Pro or GDAL. ',
      filtered ? 'These URLs keep the current classes, filters, search and datasets (not the map view).'
        : 'Nothing is filtered, so this is the whole index.'),
    urlRow('OGC API – Features', u.ogc,
      'QGIS: Layer › Add Layer › Add WFS / OGC API – Features Layer › New, paste as URL, Connect. '
      + 'ArcGIS Pro: Insert › Connections › Server › New OGC API Server. One layer per class '
      + '(per geometry type for a class that mixes them).', true),
    items,
    urlRow('Vector tiles', api.tilesUrl(st),
      'QGIS: Layer › Add Layer › Add Vector Tile Layer › New Generic Connection, paste as URL, max zoom 14.'),
    urlRow('Vector tile style', u.style,
      'Optional Style URL for that connection: draws the classes in the colours of this map. '
      + 'Change style.json to tiles.json for the TileJSON.'),
  );
  dlg.showModal();
}
