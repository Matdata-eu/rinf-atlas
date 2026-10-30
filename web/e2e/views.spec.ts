import { expect, openApp, test } from './fixtures';

// Views (shapes/views/*.ttl) and their requirement checks, on the Luxembourg sample:
// RINF declares TEN-T levels and a corridor on its tracks there.

type View = { key: string; label: string; classes: string[]; checks: { key: string }[]; summary_key: string | null };

async function views(page: import('@playwright/test').Page): Promise<View[]> {
  const r = await page.request.get('/api/meta?env=prod');
  expect(r.ok()).toBeTruthy();
  return (await r.json()).views as View[];
}

test('meta lists the views with their checks', async ({ page, errors: _ }) => {
  const all = await views(page);
  expect(all.map((v) => v.key)).toEqual(expect.arrayContaining(['corridors', 'freight-corridors', 'ertms', 'electrification']));
  const tent = all.find((v) => v.key === 'corridors')!;
  expect(tent.summary_key).toBe('tent.all');
  expect(tent.checks.map((c) => c.key)).toContain('tent.electrification');
});

test('opening a view sets its classes, filters and colours, and the link keeps it', async ({ page, errors: _ }) => {
  await openApp(page);
  // the picker sits in the header; the block above the filters shows only with a view open
  await expect(page.locator('header #view-select')).toHaveValue('');
  await expect(page.locator('#view-bar')).toBeHidden();
  const search = page.waitForResponse((r) => r.url().includes('/api/search') && r.ok());
  await page.locator('#view-select').selectOption('corridors');
  await search;
  await expect(page).toHaveURL(/vw=corridors/);
  await expect(page).toHaveURL(/col=tent\.all/);
  await expect(page.locator('#view-section')).toBeVisible();
  await expect(page.locator('#all-filters')).toBeHidden();
  // the summary check comes first, with its fixed result colours
  const first = page.locator('#view-facets > *').first();
  await expect(first).toContainText('Meets every TEN-T requirement');
  await expect(first.locator('.lbl .swatch').first()).toBeVisible();
  await expect(page.locator('#result-count')).toHaveText(/^[1-9]/);

  await page.reload();
  await expect(page.locator('#view-select')).toHaveValue('corridors');
  await page.locator('#view-toggle-all').click();
  await expect(page.locator('#all-filters')).toBeVisible();

  await page.locator('#view-bar button[aria-label="Close the view"]').click();
  await expect(page.locator('#view-section')).toBeHidden();
  await expect(page.locator('#view-bar')).toBeHidden();
  await expect(page.locator('#view-select')).toHaveValue('');
  await expect(page).not.toHaveURL(/vw=/);
  // the view's classes, filters and colours are cleared with it
  await expect(page).not.toHaveURL(/[#&](c|f|col)=/);
});

test('the requirements table counts every check per corridor', async ({ page, errors: _ }) => {
  await openApp(page, 'vw=corridors');
  const card = page.waitForResponse((r) => r.url().includes('/api/scorecard') && r.ok());
  await page.locator('#scorecard-btn').click();
  const body = await (await card).json();
  expect(body.keys[0]).toBe('tent.all');
  const dlg = page.getByRole('dialog', { name: /requirements/ });
  await expect(dlg).toBeVisible();
  await expect(dlg.locator('tbody tr').last()).toContainText('All');
  await expect(dlg.locator('.sc-pct').first()).toHaveText(/\d+ %/);
  await dlg.getByRole('button', { name: 'Number' }).click();
  await expect(dlg.getByRole('button', { name: 'Number' })).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('Escape');
  await expect(dlg).toBeHidden();
});

test('picking a corridor filters the map to it', async ({ page, errors: _ }) => {
  await openApp(page, 'vw=corridors');
  const group = page.locator('#view-group');
  await expect(group.locator('option')).not.toHaveCount(1);
  const search = page.waitForResponse((r) => r.url().includes('/api/search') && r.ok());
  await group.selectOption({ index: 1 });
  await search;
  await expect(page).toHaveURL(/corridor/);
});
