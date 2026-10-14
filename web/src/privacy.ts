// Optional privacy statement supplied by whoever deploys this instance: a page
// mounted at site/privacy.html (see docs/privacy.md). Linked from the map
// attribution and the About dialog only when it exists.
export const PRIVACY_URL = '/site/privacy.html';

// Accept: text/plain keeps the Vite dev server from answering with index.html.
export const hasPrivacy: Promise<boolean> = fetch(PRIVACY_URL, { method: 'HEAD', cache: 'no-cache', headers: { Accept: 'text/plain' } })
  .then((r) => r.ok)
  .catch(() => false);
