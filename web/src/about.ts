// "About" dialog: what the app is, where the data comes from, credits and the
// project repository (bugs, feature requests, questions).
import { hasPrivacy, PRIVACY_URL } from './privacy';
import { h } from './state';

const REPO = ((import.meta.env.VITE_REPO_URL as string | undefined) ?? 'https://github.com/Matdata-eu/rinf-atlas').replace(/\/$/, '');

let dlg: HTMLDialogElement | null = null;

const link = (href: string, text: string) => h('a', { href, target: '_blank', rel: 'noopener' }, text);

export function openAbout() {
  if (!dlg) {
    dlg = h('dialog', { class: 'datasets-dialog about-dialog', 'aria-label': 'About RINF Atlas' });
    const privacy = h('div', { hidden: true },
      h('h3', {}, 'Privacy'),
      h('p', {}, 'Read the ', link(PRIVACY_URL, 'privacy statement'), ' of this instance.'));
    hasPrivacy.then((ok) => { privacy.hidden = !ok; });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg!.close(); });
    dlg.append(
      h('div', { class: 'dlg-head' },
        h('h2', {}, 'About RINF Atlas'),
        h('button', { class: 'icon-btn', 'aria-label': 'Close', onclick: () => dlg?.close() }, '×')),
      h('div', { class: 'about-body' },
        h('p', {}, 'A map and faceted search over the EU Register of Infrastructure (RINF): every infrastructure element ',
          'published by the infrastructure managers, for PROD and UAT. Search, facets and the map use a local index ',
          'that is refreshed automatically when ERA publishes new datasets; only "Describe live" queries ERA directly.'),
        h('h3', {}, 'Feedback'),
        h('p', {}, 'Found a bug, missing a feature or have a question? Open an issue on ',
          link(`${REPO}/issues/new/choose`, 'GitHub'), '. Source code and documentation: ', link(REPO, REPO.replace(/^https:\/\//, '')), '.'),
        h('p', { class: 'muted small' }, 'Wrong or missing infrastructure data comes from the infrastructure managers through ERA, ',
          'not from this application.'),
        privacy,
        h('h3', {}, 'Credits'),
        h('ul', { class: 'credits' },
          h('li', {}, 'Data: ', link('https://www.era.europa.eu/domains/registers/rinf_en', 'European Union Agency for Railways (ERA), RINF'),
            '. This application is not affiliated with ERA.'),
          h('li', {}, 'Basemap: ', link('https://www.openstreetmap.org/copyright', '© OpenStreetMap contributors'),
            '; railway overlay: ', link('https://www.openrailwaymap.org/', 'OpenRailwayMap'), ' (CC-BY-SA)'),
          h('li', {}, 'Built with ', link('https://maplibre.org/', 'MapLibre GL JS'), ', ', link('https://visjs.github.io/vis-network/', 'vis-network'),
            ', ', link('https://github.com/oxigraph/oxigraph', 'Oxigraph'), ', ', link('https://postgis.net/', 'PostGIS'),
            ' and ', link('https://fastapi.tiangolo.com/', 'FastAPI')),
          h('li', {}, 'Made by ', link('https://matdata.eu', 'Matdata'), ' · MIT licence')),
        h('p', { class: 'muted small' }, `Version ${__APP_VERSION__}`)));
    document.body.append(dlg);
  }
  dlg.showModal();
}
