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

test('class names give way to the zoom hint, and the side panel resizes', async ({ page, errors: _ }) => {
  await page.setViewportSize({ width: 1100, height: 800 });
  await openApp(page, 'map=6/49.7/6.1');
  // Long names are cut with an ellipsis, but "zoom in to see" stays whole (#55).
  for (const row of await page.locator('#class-list .class-lbl').all()) {
    const hint = row.locator('.zoom-hint');
    if (!(await hint.isVisible())) continue;
    const [r, hr] = [await row.boundingBox(), await hint.boundingBox()];
    expect(hr!.x + hr!.width).toBeLessThanOrEqual(r!.x + r!.width + 1);
    expect(await hint.evaluate((e) => e.scrollWidth <= e.clientWidth)).toBe(true);
  }
  const side = page.locator('#side');
  const handle = page.getByRole('separator', { name: 'Resize the side panel' });
  const w0 = (await side.boundingBox())!.width;
  const hb = (await handle.boundingBox())!;
  await page.mouse.move(hb.x + hb.width / 2, hb.y + 200);
  await page.mouse.down();
  await page.mouse.move(hb.x + 150, hb.y + 200, { steps: 5 });
  await page.mouse.up();
  await expect.poll(async () => (await side.boundingBox())!.width).toBeGreaterThan(w0 + 100);
  // Remembered in this browser, and a double click goes back to the default width.
  await page.reload();
  await expect.poll(async () => (await side.boundingBox())!.width).toBeGreaterThan(w0 + 100);
  await handle.dblclick();
  await expect.poll(async () => (await side.boundingBox())!.width).toBe(w0);
});

test('a class without locations says so in a tooltip on its count, on one line', async ({ page, errors: _ }) => {
  // Say half the operational points have no location. The sample's own kilometric post
  // without geometry is gone once tests/smoke.sh has loaded the sample again without it.
  await page.route('**/api/facets', async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    const n = body.by_class['operational-point'] ?? 0;
    body.unlocated_by_class = { ...body.unlocated_by_class, 'operational-point': Math.ceil(n / 2) };
    await route.fulfill({ response: res, json: body });
  });
  await openApp(page);
  const row = page.locator('#class-list li.class-row').filter({ has: page.locator('input[data-class="operational-point"]') });
  const mark = row.locator('.nomap-hint');
  await expect(mark).toBeVisible();
  await expect(mark).toHaveAttribute('aria-label', /^\d+ without location, not on the map/);
  const line = (await row.locator('.class-line').boundingBox())!;
  expect((await row.boundingBox())!.height).toBeLessThanOrEqual(line.height + 1);
  const tip = () => mark.evaluate((e) => getComputedStyle(e, '::after').visibility);
  expect(await tip()).toBe('hidden');
  await mark.hover();
  await expect.poll(tip).toBe('visible');
  // A click on it does not tick the class.
  await mark.click();
  await expect(row.locator('input[type=checkbox]')).not.toBeChecked();
});
