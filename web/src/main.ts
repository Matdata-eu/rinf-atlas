import './style.css';
import { api, type Env } from './api';
import { openAbout } from './about';
import { initCompat } from './compat';
import { initDatasets, openDatasets } from './datasets';
import { initDetail } from './detail';
import { initProfile } from './profile';
import { initFacets } from './facets';
import { fitBbox, initMap, jumpToView, TILE_MODE } from './map';
import { initResults } from './results';
import { initThematic } from './thematic';
import { setTheme, themePref, type ThemePref } from './theme';
import { changed, debounce, h, queryState, store, type AppState, type SideTab } from './state';
import { buildHash, currentLink, fromUrl, parseHash, URL_DEFAULTS, writeUrl } from './url';

const $ = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
const envSelect = $<HTMLSelectElement>('env-select');
const qInput = $<HTMLInputElement>('q');
const statusEl = $<HTMLDivElement>('status');
const side = $<HTMLElement>('side');
const mapMsg = $<HTMLDivElement>('map-msg');

function showMsg(text: string, ms = 3500) {
  mapMsg.textContent = text;
  mapMsg.hidden = false;
  setTimeout(() => (mapMsg.hidden = true), ms);
}

// ---------- share: copy the link to this view (or the native share sheet on phones) ----------
const toastEl = $<HTMLDivElement>('toast');
let toastTimer: ReturnType<typeof setTimeout> | undefined;
function toast(text: string, ms = 3200) {
  toastEl.textContent = text;
  toastEl.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toastEl.hidden = true), ms);
}

/** What the link carries besides the map and filters, for the confirmation. */
function linkExtras(s: AppState): string[] {
  const x: string[] = [];
  if (s.selected) x.push('the selected feature');
  if (s.colorBy) x.push('map colours');
  if (s.profileOpen && s.profile) x.push('the line profile');
  if (s.vehicle) x.push(`vehicle “${s.vehicle.name}”`);
  if (s.qa) x.push('data issues');
  return x;
}

async function share() {
  const s = store.get();
  const url = currentLink(s);
  const extras = linkExtras(s);
  // Phones and tablets: the system share sheet (mail, chat apps...); elsewhere the clipboard.
  if (navigator.share && matchMedia('(pointer: coarse)').matches) {
    try { await navigator.share({ title: document.title, url }); return; } catch (e) {
      if ((e as DOMException).name === 'AbortError') return;
    }
  }
  try {
    await navigator.clipboard.writeText(url);
    toast(`Link copied${extras.length ? `, with ${extras.join(', ')}` : ''}. It opens this view.`);
  } catch {
    window.prompt('Copy this link to share the view:', url);
  }
}

// ---------- top bar ----------
function initTopBar(envs: Env[]) {
  envSelect.replaceChildren(...envs.map((e) => h('option', { value: e.key, title: e.sparql }, e.label)));
  envSelect.value = store.get().env;
  envSelect.addEventListener('change', () => store.set({ env: envSelect.value, selected: null, meta: null, graphs: [], profile: null }));

  const setQ = debounce((v: string) => store.set({ q: v }), 250);
  qInput.value = store.get().q;
  qInput.addEventListener('input', () => setQ(qInput.value));
  qInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') store.set({ q: qInput.value }); });

  statusEl.addEventListener('click', () => openDatasets());
  $('share-btn').addEventListener('click', share);
}

function renderStatus() {
  const m = store.get().meta;
  if (!m) {
    statusEl.replaceChildren(h('button', { class: 'status-btn', title: 'Click to see every dataset and its status.' },
      h('span', { class: 'dot' }), h('span', {}, store.get().env ? 'Datasets…' : 'Datasets (API not ready)')));
    return;
  }
  const d = m.datasets;
  const when = d.last_ingest ? new Date(d.last_ingest).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : 'never';
  const ok = d.loaded >= d.total;
  const failed = d.failed ? ` · ${d.failed} failed` : '';
  statusEl.replaceChildren(
    h('button', { class: 'status-btn', title: `Last dataset processed ${when}. Click to see every dataset and its status.` },
      h('span', { class: `dot ${d.failed ? 'bad' : ok ? 'ok' : 'warn'}` }),
      h('span', {}, `${d.loaded}/${d.total} datasets processed${failed}`),
      h('span', { class: 'status-when' }, ` · ${when}`)));
}

// ---------- theme switch: auto (follow the OS) -> light -> dark ----------
const THEME_ICONS: Record<ThemePref, string> = {
  auto: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="6" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M8 2a6 6 0 0 0 0 12z" fill="currentColor"/></svg>',
  light: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="3" fill="currentColor"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6 13 13M3 13l1.4-1.4M11.6 4.4 13 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  dark: '<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M13.5 10A6 6 0 0 1 6 2.5a6 6 0 1 0 7.5 7.5z" fill="currentColor"/></svg>',
};
const THEME_NEXT: Record<ThemePref, ThemePref> = { auto: 'light', light: 'dark', dark: 'auto' };
const THEME_TEXT: Record<ThemePref, string> = { auto: 'Theme: follows your system', light: 'Theme: light', dark: 'Theme: dark' };

function initTheme() {
  const btn = $<HTMLButtonElement>('theme-btn');
  const show = () => {
    const p = themePref();
    btn.innerHTML = THEME_ICONS[p];
    btn.title = `${THEME_TEXT[p]} (click for ${THEME_NEXT[p] === 'auto' ? 'system' : THEME_NEXT[p]})`;
    btn.setAttribute('aria-label', btn.title);
  };
  btn.addEventListener('click', () => { setTheme(THEME_NEXT[themePref()]); show(); });
  $('about-btn').addEventListener('click', openAbout);
  show();
}

// ---------- side panel tabs + mobile sheet ----------
function initSide() {
  const tabs = side.querySelectorAll<HTMLButtonElement>('.side-head .tab');
  const apply = () => {
    const t = store.get().sideTab;
    tabs.forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === t)));
    $('pane-filters').hidden = t !== 'filters';
    $('pane-results').hidden = t !== 'results';
  };
  tabs.forEach((b) => b.addEventListener('click', () => {
    store.set({ sideTab: b.dataset.tab as SideTab });
    side.classList.add('expanded');
  }));
  $('sheet-toggle').addEventListener('click', () => side.classList.toggle('expanded'));
  store.subscribe((s, prev) => { if (changed(s, prev, 'sideTab')) apply(); });
  apply();
}

// ---------- map tools ----------
function initMapTools() {
  // Panels under the map buttons start below them, also when the buttons wrap to a second row.
  const tools = document.querySelector<HTMLElement>('.map-tools');
  const wrap = document.querySelector<HTMLElement>('.map-wrap');
  if (tools && wrap && 'ResizeObserver' in window) {
    new ResizeObserver(() => wrap.style.setProperty('--map-tools-bottom', `${tools.offsetTop + tools.offsetHeight}px`)).observe(tools);
  }
  const orm = $<HTMLInputElement>('orm-toggle');
  orm.checked = store.get().orm;
  orm.addEventListener('change', () => store.set({ orm: orm.checked }));
  store.subscribe((s, prev) => { if (changed(s, prev, 'orm')) orm.checked = s.orm; });
  // Data quality highlight, offered once the backend reports that it validates datasets.
  const qa = $<HTMLInputElement>('qa-toggle');
  const qaTool = $('qa-tool'), qaLegend = $('qa-legend');
  const showQa = () => {
    const s = store.get();
    qaTool.hidden = !s.meta?.quality?.enabled;
    qa.checked = s.qa;
    qaLegend.hidden = qaTool.hidden || !s.qa;
  };
  qa.addEventListener('change', () => store.set({ qa: qa.checked }));
  store.subscribe((s, prev) => { if (changed(s, prev, 'qa', 'meta')) showQa(); });
  showQa();
  $('zoom-results').addEventListener('click', async () => {
    try {
      const { bbox } = await api.extent(queryState(store.get(), false));
      if (bbox) fitBbox(bbox); else showMsg('No results to zoom to.');
    } catch (e) { showMsg(`Extent failed: ${(e as Error).message}`); }
  });
}

async function loadMeta(env: string) {
  try {
    const meta = await api.meta(env);
    if (store.get().env === env) store.set({ meta });
  } catch (e) {
    statusEl.replaceChildren(h('button', { class: 'status-btn', title: (e as Error).message },
      h('span', { class: 'dot bad' }), h('span', {}, 'Datasets (API unavailable)')));
    showMsg(`Could not load metadata: ${(e as Error).message}`, 8000);
  }
}

async function main() {
  // A link (any state in the hash) is self-contained: it also decides the vehicle check and the
  // line profile rows, which otherwise come from this browser's own settings.
  const fromLink = location.hash.length > 1;
  const fromHash = parseHash(location.hash);
  store.set({ ...fromHash, env: '' });
  initMap($('map'));
  initFacets();
  initThematic(document.querySelector('.map-wrap') as HTMLElement);
  initResults();
  initDetail();
  initCompat(fromLink);
  initProfile(fromLink);
  initDatasets();
  initSide();
  initMapTools();
  initTheme();

  let envs: Env[] = [];
  try { envs = await api.envs(); } catch (e) { showMsg(`API unavailable: ${(e as Error).message}`, 10000); }
  const env = envs.find((e) => e.key === fromHash.env) ?? envs.find((e) => e.default) ?? envs[0];
  initTopBar(envs);
  renderStatus();
  if (!envs.length) showMsg('The API lists no environments: check ATLAS_ENVS in .env (prod, uat).', 15000);

  const persist = debounce(() => writeUrl(store.get()), 150);
  store.subscribe((s, prev) => {
    persist();
    if (changed(s, prev, 'env') && s.env) { envSelect.value = s.env; renderStatus(); loadMeta(s.env); }
    if (changed(s, prev, 'meta')) renderStatus();
    if (changed(s, prev, 'q') && document.activeElement !== qInput) qInput.value = s.q;
  });
  if (env) store.set({ env: env.key, selected: fromHash.selected ?? null });

  // Back / Forward between entries, or a link pasted over the current one: show that state.
  const onUrl = () => {
    if (location.hash === buildHash(store.get())) return; // already showing it (popstate and hashchange both fire)
    const next = parseHash(location.hash);
    const cur = store.get();
    const envKey = next.env && envs.some((e) => e.key === next.env) ? next.env : cur.env;
    fromUrl(() => store.set({ ...URL_DEFAULTS, ...next, env: envKey, ...(envKey !== cur.env ? { meta: null } : {}) }), store.get.bind(store));
    if (next.view) jumpToView(next.view);
  };
  window.addEventListener('popstate', onUrl);
  window.addEventListener('hashchange', onUrl);
  if (TILE_MODE === 'geojson') document.body.classList.add('geojson-mode');
}

main();
