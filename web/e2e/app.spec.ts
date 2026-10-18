import { classes, expect, openApp, resultCount, test } from './fixtures';

test('loads with every indexed class and its count', async ({ page, errors: _ }) => {
  await openApp(page);
  const all = await classes(page);
  expect(all.length).toBeGreaterThan(0);
  for (const c of all) {
    const row = page.locator('#class-list li.class-row').filter({ has: page.locator(`input[data-class="${c.class}"]`) });
    await expect(row.locator('.lbl')).toContainText(c.label);
    await expect(row.locator('.count')).toHaveText(String(c.count));
  }
  await expect.poll(() => resultCount(page)).toBe(all.reduce((n, c) => n + c.count, 0));
  await expect(page.locator('#status')).toContainText(/\d+\/\d+ datasets processed/);
});

test('draws data tiles for the environment', async ({ page, errors: _ }) => {
  // Tiles of the selected environment (its key is in the base64url state) answer, and
  // the ones over Luxembourg carry data. The map is made before the environment is known
  // and asks for a few tiles without one (the API answers 404); those are left out here.
  const state = (url: string) => Buffer.from(new URL(url).searchParams.get('state') ?? '', 'base64url').toString();
  const sizes: number[] = [];
  const bad: string[] = [];
  page.on('response', async (r) => {
    if (!/\/api\/tiles\/\d+\/\d+\/\d+\.pbf/.test(r.url()) || state(r.url()).includes('"env":""')) return;
    if (!state(r.url()).includes('"env":"prod"') || r.status() >= 300) bad.push(`${r.status()} ${state(r.url())}`);
    else sizes.push((await r.body().catch(() => Buffer.alloc(0))).length);
  });
  await openApp(page, 'map=9/49.6/6.1');
  await expect(page.locator('#map canvas')).toBeVisible();
  await expect.poll(() => Math.max(0, ...sizes)).toBeGreaterThan(0);
  expect(bad).toEqual([]);
});

test('theme button cycles light, dark and system', async ({ page, errors: _ }) => {
  await openApp(page);
  const html = page.locator('html');
  await expect(html).not.toHaveAttribute('data-theme');
  await page.locator('#theme-btn').click();
  await expect(html).toHaveAttribute('data-theme', 'light');
  await page.locator('#theme-btn').click();
  await expect(html).toHaveAttribute('data-theme', 'dark');
  await page.reload();
  await expect(html).toHaveAttribute('data-theme', 'dark');
  await page.locator('#theme-btn').click();
  await expect(html).not.toHaveAttribute('data-theme');
});

test('help, about and datasets dialogs open and close', async ({ page, errors: _ }) => {
  await openApp(page);
  for (const [open, name] of [['#help-btn', 'Help'], ['#about-btn', 'About RINF Atlas'], ['#status button', 'Datasets']]) {
    await page.locator(open).click();
    const dlg = page.getByRole('dialog', { name });
    await expect(dlg).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(dlg).toBeHidden();
  }
});

test('datasets dialog lists the loaded sample', async ({ page, errors: _ }) => {
  await openApp(page);
  await page.locator('#status button').click();
  const dlg = page.getByRole('dialog', { name: 'Datasets' });
  await expect(dlg).toContainText('0082');
  await expect(dlg).toContainText(/processed/i);
});
