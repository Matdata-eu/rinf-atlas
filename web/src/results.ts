// Results list: POST /api/search, paged by 50.
import { api, isAbort, type SearchItem } from './api';
import { ZOOM_ICON, zoomToFeature } from './detail';
import { openExport, setResultTotal } from './export';
import { openGis } from './gis';
import { flyTo } from './map';
import { changed, debounce, fmt, h, queryState, store } from './state';

const PAGE = 50;
const list = document.getElementById('results') as HTMLOListElement;
const more = document.getElementById('results-more') as HTMLButtonElement;
const badge = document.getElementById('result-count') as HTMLSpanElement;
const unlocatedMsg = document.getElementById('results-unlocated') as HTMLParagraphElement;

let items: SearchItem[] = [];
let total = 0;
let unlocated = 0;
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
    setResultTotal(total);
    unlocated = r.unlocated ?? 0;
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
  unlocatedMsg.hidden = !unlocated;
  unlocatedMsg.textContent = total === 1 && unlocated === 1
    ? 'This feature has no location in RINF, so it is listed here but not drawn on the map.'
    : unlocated === total
    ? `None of these ${fmt.format(total)} features has a location in RINF, so they are listed here but not drawn on the map.`
    : `${fmt.format(unlocated)} of these ${fmt.format(total)} features have no location in RINF and are not drawn on the map.`;
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

export function initResults() {
  more.addEventListener('click', () => load(false));
  document.getElementById('export-open')!.addEventListener('click', () => void openExport());
  document.getElementById('export-gis')!.addEventListener('click', openGis);
  store.subscribe((s, prev) => {
    if (changed(s, prev, 'meta', 'classes', 'filters', 'graphs', 'q', 'limitToView') || (s.limitToView && changed(s, prev, 'bbox'))) {
      reload();
    }
    else if (changed(s, prev, 'selected')) {
      for (const b of list.querySelectorAll<HTMLButtonElement>('.result')) b.classList.toggle('selected', b.dataset.iri === s.selected);
    }
  });
}
