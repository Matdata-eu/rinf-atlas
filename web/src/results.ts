// Results list: POST /api/search, paged by 50.
import { api, isAbort, type SearchItem } from './api';
import { ZOOM_ICON, zoomToFeature } from './detail';
import { openGis } from './gis';
import { flyTo } from './map';
import { changed, debounce, fmt, h, queryState, store } from './state';

const PAGE = 50;
const list = document.getElementById('results') as HTMLOListElement;
const more = document.getElementById('results-more') as HTMLButtonElement;
const badge = document.getElementById('result-count') as HTMLSpanElement;

let items: SearchItem[] = [];
let total = 0;
let ctl: AbortController | null = null;

async function load(reset: boolean) {
  const s = store.get();
  if (!s.env || !s.meta) return;
  ctl?.abort();
  ctl = new AbortController();
  more.disabled = true;
  try {
    const r = await api.search(queryState(s), PAGE, reset ? 0 : items.length, ctl.signal);
    items = reset ? r.items : items.concat(r.items);
    total = r.total;
    render(reset);
  } catch (e) {
    if (!isAbort(e)) { console.error(e); list.replaceChildren(h('li', { class: 'error' }, `Search failed: ${(e as Error).message}`)); }
  } finally {
    more.disabled = false;
  }
}
const reload = debounce(() => load(true), 50);

function render(reset: boolean) {
  const s = store.get();
  const colors = new Map((s.meta?.classes ?? []).map((c) => [c.class, c]));
  badge.textContent = fmt.format(total);
  const rows = items.map((it) => {
    const c = colors.get(it.class);
    const located = it.lon != null && it.lat != null;
    const zoomTitle = located ? 'Zoom to this feature' : 'This feature has no location on the map';
    const zoom = h('button', {
      class: 'icon-btn zoom-btn', title: zoomTitle, 'aria-label': located ? `Zoom to ${it.label || it.id}` : zoomTitle,
      disabled: !located, onclick: () => zoomToFeature(it.iri, [it.lon!, it.lat!]),
    });
    zoom.innerHTML = ZOOM_ICON;
    return h('li', { class: 'result-row' },
      h('button', {
        class: 'result' + (it.iri === s.selected ? ' selected' : ''), 'data-iri': it.iri,
        onclick: (ev: Event) => {
          const describe = (ev as MouseEvent).ctrlKey || (ev as MouseEvent).metaKey;
          store.set({ selected: it.iri, detailTab: describe ? 'describe' : 'properties' });
          if (located) flyTo(it.lon!, it.lat!); // after the detail panel opened, so the map has its final size
        },
      },
      h('span', { class: 'swatch', style: `background:${c?.color ?? '#888'}` }),
      h('span', { class: 'result-text' }, h('span', { class: 'lbl' }, it.label || it.id), h('small', {}, `${c?.label ?? it.class} · ${it.id}`))),
      zoom);
  });
  if (!items.length) rows.push(h('li', { class: 'muted empty' }, 'No matching features.'));
  list.replaceChildren(...rows);
  if (reset) list.parentElement!.scrollTop = 0;
  more.hidden = items.length >= total;
  more.textContent = `Load more (${fmt.format(total - items.length)} left)`;
}

/** Starts a browser download of the current results; the browser streams it to disk. */
function download(format: 'csv' | 'xlsx' | 'ttl') {
  const s = store.get();
  if (!s.env) return;
  const msg = document.getElementById('export-msg') as HTMLParagraphElement;
  const max = s.meta?.limits?.ttl_export;
  msg.hidden = true;
  if (format === 'ttl' && max != null && total > max) {
    msg.textContent = `Turtle export fetches every entity live from ERA and is limited to ${fmt.format(max)} entities; narrow the selection (${fmt.format(total)} now).`;
    msg.hidden = false;
    return;
  }
  const a = h('a', { href: api.exportUrl(queryState(s), format), download: '' });
  document.body.append(a);
  a.click();
  a.remove();
}

export function initResults() {
  more.addEventListener('click', () => load(false));
  document.getElementById('export-csv')!.addEventListener('click', () => download('csv'));
  document.getElementById('export-xlsx')!.addEventListener('click', () => download('xlsx'));
  document.getElementById('export-gis')!.addEventListener('click', openGis);
  document.getElementById('export-ttl')!.addEventListener('click', () => download('ttl'));
  store.subscribe((s, prev) => {
    if (changed(s, prev, 'meta', 'classes', 'filters', 'graphs', 'q', 'limitToView') || (s.limitToView && changed(s, prev, 'bbox'))) {
      reload();
      (document.getElementById('export-msg') as HTMLParagraphElement).hidden = true;
    }
    else if (changed(s, prev, 'selected')) {
      for (const b of list.querySelectorAll<HTMLButtonElement>('.result')) b.classList.toggle('selected', b.dataset.iri === s.selected);
    }
  });
}
