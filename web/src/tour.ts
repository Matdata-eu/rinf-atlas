// "How RINF Atlas works": a short tour that opens on the first visit. Four pages of three
// items, each with a small drawing. The Help dialog links to it so it can be reopened.
import { h } from './state';

// Seen is kept in this browser per version of the tour: bump the version when the pages
// change enough that everyone should see them again.
const SEEN_KEY = 'atlas-tour';
const VERSION = '2026-10-28';

type Item = { title: string; text: string; art: string };
type Page = { title: string; items: Item[] };

// Drawings on a 64×48 canvas. The classes pick theme colours (style.css, .tour-art).
const svg = (body: string) =>
  `<svg viewBox="0 0 64 48" width="64" height="48" aria-hidden="true">${body}</svg>`;
const TRACK = '<path class="ta-rail" d="M4 40C18 36 22 18 34 16S54 10 60 6"/>';

const ART = {
  env: svg('<rect class="ta-soft" x="6" y="12" width="52" height="24" rx="12"/>'
    + '<rect class="ta-fill" x="8" y="14" width="26" height="20" rx="10"/>'
    + '<text class="ta-on" x="21" y="27.5" text-anchor="middle">PROD</text>'
    + '<text class="ta-txt" x="45" y="27.5" text-anchor="middle">UAT</text>'),
  search: svg('<rect class="ta-soft" x="4" y="8" width="44" height="14" rx="7"/>'
    + '<path class="ta-muted" d="M11 15h22"/><path class="ta-muted" d="M11 30h28M11 37h20"/>'
    + '<circle class="ta-line" cx="46" cy="28" r="8"/><path class="ta-line ta-thick" d="m52 34 7 7"/>'),
  click: svg('<rect class="ta-soft" x="2" y="4" width="60" height="40" rx="4"/>' + TRACK
    + '<circle class="ta-dot" cx="34" cy="16" r="3.5"/>'
    + '<path class="ta-ptr" d="M38 22v15l4-4 3 7 3-1.5-3-6.5h6z"/>'),
  classes: svg('<rect class="ta-box" x="6" y="7" width="9" height="9" rx="2"/><path class="ta-tick" d="m8 11.5 2 2 3.5-4"/>'
    + '<circle class="ta-c1" cx="22" cy="11.5" r="3"/><path class="ta-muted" d="M29 11.5h22"/>'
    + '<rect class="ta-box" x="6" y="20" width="9" height="9" rx="2"/><path class="ta-tick" d="m8 24.5 2 2 3.5-4"/>'
    + '<circle class="ta-c2" cx="22" cy="24.5" r="3"/><path class="ta-muted" d="M29 24.5h16"/>'
    + '<rect class="ta-box" x="6" y="33" width="9" height="9" rx="2"/>'
    + '<circle class="ta-c3" cx="22" cy="37.5" r="3"/><path class="ta-muted" d="M29 37.5h26"/>'),
  filters: svg('<path class="ta-fill-soft" d="M8 6h32L28 21v13l-8 5V21z"/>'
    + '<rect class="ta-soft" x="34" y="28" width="26" height="9" rx="4.5"/><path class="ta-muted" d="M38 32.5h12"/>'
    + '<path class="ta-line" d="m54 30.5 3 4m0-4-3 4"/>'
    + '<rect class="ta-soft" x="34" y="10" width="20" height="9" rx="4.5"/><path class="ta-muted" d="M38 14.5h8"/>'
    + '<path class="ta-line" d="m48 12.5 3 4m0-4-3 4"/>'),
  colours: svg('<rect class="ta-soft" x="2" y="4" width="60" height="40" rx="4"/>'
    + '<path class="ta-band ta-b1" d="M6 38c6-2 10-6 14-11"/><path class="ta-band ta-b2" d="M20 27c4-6 8-10 14-11"/>'
    + '<path class="ta-band ta-b3" d="M34 16c8-1 16-5 24-10"/>'
    + '<rect class="ta-b1f" x="42" y="26" width="5" height="4" rx="1"/><rect class="ta-b2f" x="42" y="32" width="5" height="4" rx="1"/>'
    + '<rect class="ta-b3f" x="42" y="38" width="5" height="4" rx="1"/>'
    + '<path class="ta-muted" d="M50 28h8M50 34h6M50 40h8"/>'),
  details: svg('<rect class="ta-card" x="10" y="3" width="44" height="42" rx="4"/>'
    + '<path class="ta-line" d="M16 11h18"/><path class="ta-tab" d="M16 18h10"/><path class="ta-muted" d="M30 18h8M42 18h6"/>'
    + '<path class="ta-muted" d="M16 26h10M30 26h18M16 32h10M30 32h14M16 38h10M30 38h16"/>'),
  results: svg('<rect class="ta-card" x="4" y="4" width="36" height="40" rx="4"/>'
    + '<circle class="ta-c1" cx="11" cy="13" r="2.5"/><path class="ta-muted" d="M16 13h18"/>'
    + '<circle class="ta-c2" cx="11" cy="22" r="2.5"/><path class="ta-muted" d="M16 22h14"/>'
    + '<circle class="ta-c3" cx="11" cy="31" r="2.5"/><path class="ta-muted" d="M16 31h18"/>'
    + '<circle class="ta-fill" cx="50" cy="30" r="11"/><path class="ta-on-line" d="M50 23v12m-5-5 5 5 5-5"/>'),
  share: svg('<rect class="ta-soft" x="4" y="16" width="56" height="16" rx="8"/>'
    + '<path class="ta-line" d="M14 26.5a3.5 3.5 0 0 1 0-5h4m2 0h4a3.5 3.5 0 0 1 0 5h-4"/><path class="ta-line" d="M17 24h6"/>'
    + '<path class="ta-muted" d="M32 24h20"/>'),
  profile: svg('<path class="ta-muted" d="M4 42h56"/>'
    + '<path class="ta-band ta-b3" d="M6 9h20"/><path class="ta-band ta-b2" d="M27 9h14"/><path class="ta-band ta-b3" d="M42 9h16"/>'
    + '<path class="ta-band ta-c1s" d="M6 20h30"/><path class="ta-band ta-c3s" d="M37 20h21"/>'
    + '<path class="ta-band ta-b1" d="M6 31h12"/><path class="ta-band ta-b2" d="M19 31h39"/>'
    + '<path class="ta-ptr-line" d="M30 4v40"/>'),
  vehicle: svg('<path class="ta-band ta-ok" d="M4 42h18"/><path class="ta-band ta-warn" d="M23 42h18"/><path class="ta-band ta-bad" d="M42 42h18"/>'
    + '<path class="ta-fill-soft" d="M12 14h30c6 0 12 6 12 14v4H12z"/>'
    + '<rect class="ta-win" x="16" y="18" width="8" height="6" rx="1"/><rect class="ta-win" x="28" y="18" width="8" height="6" rx="1"/>'
    + '<path class="ta-win" d="M40 18h3c3 0 6 3 7 6h-10z"/>'
    + '<circle class="ta-wheel" cx="20" cy="35" r="3"/><circle class="ta-wheel" cx="46" cy="35" r="3"/>'),
  datasets: svg('<ellipse class="ta-fill-soft" cx="24" cy="10" rx="16" ry="5"/>'
    + '<path class="ta-fill-soft" d="M8 10v26c0 3 7 5 16 5s16-2 16-5V10c0 3-7 5-16 5S8 13 8 10z"/>'
    + '<path class="ta-line" d="M8 19c0 3 7 5 16 5s16-2 16-5M8 28c0 3 7 5 16 5s16-2 16-5"/>'
    + '<circle class="ta-okf" cx="48" cy="32" r="10"/><path class="ta-on-line" d="m43.5 32 3 3 6-6"/>'),
};

const PAGES: Page[] = [
  { title: 'Find railway infrastructure', items: [
    { art: ART.env, title: 'Choose the data',
      text: 'RINF Atlas shows the EU Register of Infrastructure. Pick PROD for the published data or UAT for the test register next to the logo.' },
    { art: ART.search, title: 'Search',
      text: 'Type a station or operational point name, a line number or an ID in the search box at the top.' },
    { art: ART.click, title: 'Click the map',
      text: 'Click anything on the map to see what it is. Small elements such as signals and switches appear as you zoom in.' },
  ] },
  { title: 'Narrow it down', items: [
    { art: ART.classes, title: 'Pick classes',
      text: 'Tick the kinds of infrastructure to show: sections of line, tunnels, platforms, signals and more. Each shows how many match.' },
    { art: ART.filters, title: 'Filter',
      text: 'Filter by country, infrastructure manager or any parameter of a class. Active filters show as chips you can remove.' },
    { art: ART.colours, title: 'Colour the map',
      text: 'Under Map colours, colour the network by a property such as maximum speed or energy supply.' },
  ] },
  { title: 'Inspect and share', items: [
    { art: ART.details, title: 'Element details',
      text: 'See the indexed properties of an element, every triple about it live from ERA, or the same data as a graph.' },
    { art: ART.results, title: 'Results and export',
      text: 'The Results tab lists what matches. Export it as CSV, Excel or Turtle, or open it as layers in your GIS.' },
    { art: ART.share, title: 'Share a view',
      text: 'Share copies a link to exactly what you see: map position, filters, the selected element and open tools.' },
  ] },
  { title: 'Go further', items: [
    { art: ART.profile, title: 'Line profile',
      text: 'Follow track parameters along a national line, a set of sections or a route between two operational points.' },
    { art: ART.vehicle, title: 'Vehicle check',
      text: 'Describe a vehicle and see on the map where it fits. An indication only, see the disclaimer in the panel.' },
    { art: ART.datasets, title: 'Datasets and data quality',
      text: 'See when each infrastructure manager last published, and show where ERA’s validation found data issues.' },
  ] },
];

let dlg: HTMLDialogElement | null = null;
let page = 0;
let go: (i: number) => void = () => {};
let seenNow = false; // when storage is blocked, the tour stays closed for this visit

export function tourSeen(): boolean {
  if (seenNow) return true;
  try { return (localStorage.getItem(SEEN_KEY) ?? '') >= VERSION; } catch { return false; }
}

function markSeen() {
  seenNow = true;
  try { localStorage.setItem(SEEN_KEY, VERSION); } catch { /* not persisted; closed for this visit */ }
}

function build(): HTMLDialogElement {
  const d = h('dialog', { class: 'datasets-dialog tour-dialog', 'aria-label': 'How RINF Atlas works' });
  const heading = h('h2', {}, '');
  const step = h('span', { class: 'tour-step muted' });
  const body = h('div', { class: 'tour-body', 'aria-live': 'polite' });
  const dots = h('div', { class: 'tour-dots' });
  const back = h('button', { class: 'btn', type: 'button', onclick: () => go(page - 1) }, 'Back');
  const next = h('button', { class: 'btn tour-next', type: 'button',
    onclick: () => (page < PAGES.length - 1 ? go(page + 1) : d.close()) }, 'Next');
  const skip = h('button', { class: 'link tour-skip', type: 'button', onclick: () => d.close() }, 'Skip the tour');

  go = (i: number) => {
    page = Math.max(0, Math.min(PAGES.length - 1, i));
    const p = PAGES[page];
    heading.textContent = p.title;
    step.textContent = `${page + 1} of ${PAGES.length}`;
    body.replaceChildren(...p.items.map((it) => {
      const art = h('div', { class: 'tour-art' });
      art.innerHTML = it.art;
      return h('div', { class: 'tour-item' }, art, h('div', {}, h('h3', {}, it.title), h('p', {}, it.text)));
    }));
    dots.replaceChildren(...PAGES.map((pg, j) => h('button', {
      class: 'tour-dot', type: 'button', 'aria-current': j === page ? 'step' : undefined,
      'aria-label': `Page ${j + 1}: ${pg.title}`, onclick: () => go(j),
    })));
    back.hidden = page === 0;
    skip.hidden = page === PAGES.length - 1;
    next.textContent = page < PAGES.length - 1 ? 'Next' : 'Start exploring';
  };

  d.addEventListener('click', (e) => { if (e.target === d) d.close(); });
  d.addEventListener('close', markSeen);
  d.addEventListener('keydown', (e) => {
    if ((e.target as HTMLElement).closest('input, select, textarea')) return;
    if (e.key === 'ArrowRight') { go(page + 1); e.preventDefault(); }
    if (e.key === 'ArrowLeft') { go(page - 1); e.preventDefault(); }
  });
  d.append(
    h('div', { class: 'dlg-head' },
      h('div', { class: 'tour-title' }, h('span', { class: 'tour-kicker' }, 'How RINF Atlas works'), heading),
      h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => d.close() }, '×')),
    body,
    h('div', { class: 'tour-foot' }, h('div', { class: 'tour-nav-left' }, dots, step),
      h('div', { class: 'tour-nav' }, skip, back, next)));
  return d;
}

/** Open the tour at its first page. */
export function openTour() {
  if (!dlg) { dlg = build(); document.body.append(dlg); }
  go(0);
  dlg.showModal();
  dlg.querySelector<HTMLButtonElement>('.tour-next')?.focus();
}

/** Open the tour unless this browser has seen it. */
export function maybeOpenTour() {
  if (!tourSeen()) openTour();
}
