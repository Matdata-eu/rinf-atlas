import { test as base, expect, type Page } from '@playwright/test';

export type MetaClass = { class: string; label: string; count: number };

/** Uncaught exceptions in the page fail the test that caused them. The first-visit tour is
 * marked as seen so it does not cover the app; `test.use({ tour: true })` lets it open. */
export const test = base.extend<{ errors: string[]; tour: boolean }>({
  tour: [false, { option: true }],
  page: async ({ page, tour }, use) => {
    if (!tour) await page.addInitScript(() => { try { localStorage.setItem('atlas-tour', '9999-12-31'); } catch { /* */ } });
    await use(page);
  },
  errors: async ({ page }, use) => {
    const errors: string[] = [];
    page.on('pageerror', (e) => errors.push(e.message));
    await use(errors);
    expect(errors, 'uncaught errors in the page').toEqual([]);
  },
});
export { expect };

/** Open the app (optionally with a link hash) and wait until the classes and the first results are in. */
export async function openApp(page: Page, hash = '') {
  const results = page.waitForResponse((r) => r.url().includes('/api/search') && r.ok());
  await page.goto(`/${hash ? `#${hash}` : ''}`);
  await results;
  await expect(page.locator('#class-list input[type=checkbox]').first()).toBeAttached();
  await expect(page.locator('#result-count')).toHaveText(/\d/);
}

/** The classes of the prod environment that have features, as the API counts them. */
export async function classes(page: Page): Promise<MetaClass[]> {
  const r = await page.request.get('/api/meta?env=prod');
  expect(r.ok()).toBeTruthy();
  return ((await r.json()).classes as MetaClass[]).filter((c) => c.count > 0);
}

export const resultCount = async (page: Page) => Number((await page.locator('#result-count').innerText()).trim());

/** The class's checkbox in the side panel. */
export const classBox = (page: Page, cls: string) => page.locator(`#class-list input[data-class="${cls}"]`);
