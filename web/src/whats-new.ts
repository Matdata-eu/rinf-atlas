// "What's new" dialog: per release, what is new, improved and fixed, from whats-new.json
// (newest release first; add an entry with every release). It never opens by itself: a
// returning visitor who has not seen the latest release gets a dot on the About button, and
// About and Help link here. A first visit just records the latest release as seen.
import releases from './whats-new.json';
import { h } from './state';

type Release = { version: string; date: string; new?: string[]; improved?: string[]; fixed?: string[] };

const RELEASES = releases as Release[];
export const LATEST = RELEASES[0]?.version ?? '';
const SEEN_KEY = 'atlas-whats-new-seen';
const GROUPS = [['new', 'New'], ['improved', 'Improved'], ['fixed', 'Fixed']] as const;

let dlg: HTMLDialogElement | null = null;

function seen(): string | null {
  try { return localStorage.getItem(SEEN_KEY); } catch { return LATEST; }
}

function markSeen() {
  try { localStorage.setItem(SEEN_KEY, LATEST); } catch { /* private mode */ }
  showDot(false);
}

/** Whether `a` is a later version than `b` (dotted numbers). */
function newer(a: string, b: string) {
  const pa = a.split('.').map(Number), pb = b.split('.').map(Number);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    if ((pa[i] ?? 0) !== (pb[i] ?? 0)) return (pa[i] ?? 0) > (pb[i] ?? 0);
  }
  return false;
}

function showDot(on: boolean) {
  const btn = document.getElementById('about-btn');
  if (!btn) return;
  btn.classList.toggle('has-news', on);
  const label = on ? `About, credits and feedback. New in version ${LATEST}` : 'About, credits and feedback';
  btn.title = label;
  btn.setAttribute('aria-label', label);
}

/** Whether the latest release is still unseen in this browser. */
export const hasNews = () => !!LATEST && seen() !== LATEST;

/** On start: a browser that has used the app before (it keeps other settings) and has not seen
 *  the latest release gets the dot; a first visit only remembers the latest release. */
export function initWhatsNew() {
  if (!LATEST) return;
  if (seen() == null) {
    let returning = false;
    try {
      for (let i = 0; i < localStorage.length; i++) {
        // The first-visit tour's key is left out: it ships with this dialog, so a browser
        // that has it already went through this check on that visit.
        const key = localStorage.key(i) ?? '';
        if (/^(atlas-|rinf-atlas\.)/.test(key) && key !== 'atlas-tour') returning = true;
      }
    } catch { /* private mode */ }
    if (!returning) { markSeen(); return; }
  }
  showDot(hasNews());
}

const dateFmt = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });

function release(r: Release, unseen: boolean) {
  return h('section', { class: 'wn-release' },
    h('h3', {}, `Version ${r.version}`, ' ',
      h('span', { class: 'muted wn-date' }, dateFmt.format(new Date(`${r.date}T00:00:00Z`))),
      unseen ? h('span', { class: 'wn-unseen' }, 'New for you') : null),
    ...GROUPS.filter(([key]) => r[key]?.length).map(([key, title]) =>
      h('div', { class: `wn-group wn-${key}` },
        h('span', { class: 'wn-tag' }, title),
        h('ul', {}, ...r[key]!.map((item) => h('li', {}, item))))));
}

export function openWhatsNew() {
  const before = seen();
  dlg?.remove();
  dlg = h('dialog', { class: 'datasets-dialog about-dialog wn-dialog', 'aria-label': "What's new" });
  dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg!.close(); });
  dlg.append(
    h('div', { class: 'dlg-head' },
      h('h2', {}, "What's new in RINF Atlas"),
      h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => dlg?.close() }, '×')),
    h('div', { class: 'about-body wn-body' },
      ...RELEASES.map((r) => release(r, before != null && newer(r.version, before)))));
  document.body.append(dlg);
  dlg.showModal();
  markSeen();
}

/** A "What's new in <latest>" link for the About and Help dialogs; it closes `from` first. */
export function whatsNewLink(from: () => HTMLDialogElement | null) {
  return h('button', {
    class: 'link wn-link', type: 'button',
    onclick: () => { from()?.close(); openWhatsNew(); },
  }, `What's new in version ${LATEST}`);
}
