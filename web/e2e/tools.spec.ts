import { readFile } from 'node:fs/promises';
import { classes, expect, openApp, resultCount, test } from './fixtures';

test('line profile shows a national line with its sections', async ({ page, errors: _ }) => {
  const lines = await (await page.request.get('/api/lines?env=prod&q=Luxembourg')).json();
  const line = lines[0] as { line: string; sections: number };
  await openApp(page);
  await page.locator('#profile-btn').click();
  const panel = page.locator('#profile');
  await expect(panel).toBeVisible();
  await panel.getByLabel('National line identifier').fill(line.line);
  await panel.getByLabel('National line identifier').press('Enter');
  await expect(panel.locator('.profile-summary')).toContainText(`${line.sections} section`);
  await expect(panel).toContainText('Maximum permitted speed');
  await expect(page).toHaveURL(/[#&]lp=line,/);

  // Full screen covers the window; Escape brings the panel back.
  const vp = page.viewportSize()!;
  await panel.getByRole('button', { name: 'Full screen' }).click();
  await expect(panel).toHaveClass(/maximized/);
  expect(await panel.boundingBox()).toMatchObject({ x: 0, y: 0, width: vp.width, height: vp.height });
  await panel.getByRole('button', { name: 'Exit full screen' }).press('Escape');
  await expect(panel).not.toHaveClass(/maximized/);
  expect((await panel.boundingBox())!.height).toBeLessThan(vp.height / 2 + 1);

  // The link reopens the same profile.
  await page.reload();
  await expect(page.locator('#profile .profile-summary')).toContainText(`${line.sections} section`);
});

test('vehicle check colours sections by compatibility', async ({ page, errors: _ }) => {
  const sections = (await classes(page)).find((c) => c.class === 'section-of-line')!.count;
  await openApp(page);
  await page.locator('#vehicle-btn').click();
  const panel = page.locator('#vehicle-panel');
  await expect(panel).toBeVisible();
  await expect(page).toHaveURL(/[#&]vp=1/);
  await panel.getByText('I have read this disclaimer').click();
  await panel.getByRole('button', { name: 'Accept and continue' }).click();

  const legend = page.getByRole('region', { name: 'Vehicle compatibility legend' });
  const counts = async () => (await legend.locator('.compat-rows .count').allInnerTexts()).map(Number);

  // An Iberian-gauge train fits none of the standard-gauge sample.
  const profiles = panel.getByLabel('Vehicle profile');
  const iberian = await profiles.locator('option').filter({ hasText: 'Iberian-gauge' }).getAttribute('value');
  await profiles.selectOption(iberian!);
  await panel.getByText('Show compatibility on the map').click();
  await expect(legend).toBeVisible();
  await expect.poll(counts).toEqual([0, 0, sections]);
  await expect(page).toHaveURL(/[#&]veh=/);

  // A standard-gauge one is not red everywhere, and every section is counted once.
  await profiles.selectOption({ index: 0 });
  await expect.poll(async () => (await counts())[2]).toBeLessThan(sections);
  expect((await counts()).reduce((a, b) => a + b, 0)).toBe(sections);

  await legend.getByRole('button', { name: 'Turn the vehicle check off' }).click();
  await expect(legend).toBeHidden();
  await expect(page).not.toHaveURL(/[#&]veh=/);
});

test('a train is checked with its vehicles combined', async ({ page, errors: _ }) => {
  await openApp(page);
  await page.locator('#vehicle-btn').click();
  const panel = page.locator('#vehicle-panel');
  await panel.getByText('I have read this disclaimer').click();
  await panel.getByRole('button', { name: 'Accept and continue' }).click();

  // The example freight train: two locomotives and 18 wagons, combined by the API.
  await panel.getByRole('radio', { name: 'Train' }).click();
  await expect(panel.getByLabel('Train', { exact: true })).toHaveValue('example-train-freight');
  const checked = panel.locator('.train-checked');
  await expect(checked.locator('tr', { hasText: 'Train length' })).toContainText('392.4 m');
  await expect(checked.locator('tr', { hasText: 'Axle load' })).toContainText('set by loaded container wagon');
  await expect(checked.locator('tr', { hasText: 'Maximum current' })).toContainText('3,600 A');

  // Shown on the map, the link carries the combined values and the train's name.
  await panel.getByText('Show compatibility on the map').click();
  const legend = page.getByRole('region', { name: 'Vehicle compatibility legend' });
  await expect(legend).toContainText('Train: Example freight');
  await expect(page).toHaveURL(/[#&]veh=[^&]*trainLength:392\.4/);

  // The full-width editor: one column per vehicle group; a value set there combines again.
  await panel.getByRole('button', { name: 'Open the train editor' }).click();
  const editor = page.getByRole('dialog', { name: 'Train editor' });
  await expect(editor).toBeVisible();
  await expect(editor.locator('.train-group')).toHaveCount(3);
  await expect(editor.locator('.tv-flag', { hasText: 'Leading cab' })).toHaveCount(1);
  const count = editor.getByLabel('Number of loaded container wagon');
  await count.fill('10');
  await count.press('Enter');
  await expect(editor.locator('tr[data-rule="trainLength"] .te-train')).toHaveText('234.8 m');
  await expect(editor.locator('tr[data-rule="axleLoad"] .te-cell.sets')).toHaveCount(1);
  // a list of values opens without scrolling sideways
  await editor.locator('tr[data-rule="energySupplySystem"] .te-terms summary').first().click();
  const list = editor.locator('tr[data-rule="energySupplySystem"] .te-terms-list').first();
  await expect(list).toBeVisible();
  expect(await list.evaluate((e) => e.scrollWidth - e.clientWidth)).toBe(0);
  await editor.getByRole('button', { name: '⇄ Reverse' }).click();
  await expect(editor.locator('.train-group').first()).toContainText('loaded container wagon');
  await editor.getByRole('button', { name: 'Back to the panel' }).click();
  await expect(editor).toBeHidden();
  await expect(panel).toBeVisible();
  await expect(panel.locator('.train-list li').first()).toContainText('loaded container wagon');
});

test('quick CSV export downloads the current results', async ({ page, errors: _ }) => {
  await openApp(page, 'c=tunnel&side=results');
  await expect.poll(() => resultCount(page)).toBeGreaterThan(0);
  const n = await resultCount(page);
  await page.locator('#export-open').click();
  const dlg = page.locator('dialog.export-dialog');
  await expect(dlg.getByRole('button', { name: 'Prepare export' })).toBeVisible();
  const download = page.waitForEvent('download');
  await dlg.getByRole('button', { name: 'CSV', exact: true }).click();
  const file = await (await download).path();
  const rows = (await readFile(file, 'utf8')).replace(/^\ufeff/, '').trim().split(/\r?\n/);
  expect(rows[0].split(',')[0]).toBe('IRI');
  expect(rows.length - 1).toBe(n);
});

test('a full export is queued as a job with a recipe', async ({ page, errors: _ }) => {
  await openApp(page, 'c=tunnel&side=results');
  await expect.poll(() => resultCount(page)).toBeGreaterThan(0);
  await page.locator('#export-open').click();
  const dlg = page.locator('dialog.export-dialog');
  await dlg.getByLabel('Format').selectOption('ttl');
  await dlg.getByRole('button', { name: 'Prepare export' }).click();
  const job = dlg.locator('.export-job').first();
  await expect(job).toBeVisible();
  await expect(job.getByRole('link', { name: 'Recipe' })).toBeVisible();
  // the job is listed by the API and remembered across reloads
  await page.reload();
  await page.locator('#export-open').click();
  await expect(page.locator('dialog.export-dialog .export-job').first()).toBeVisible();
});

test('map colours by a facet show a legend', async ({ page, errors: _ }) => {
  await openApp(page, 'c=section-of-line');
  await page.locator('#colorby-btn').click();
  const option = page.getByText('Maximum permitted speed', { exact: false }).first();
  await option.click();
  await expect(page).toHaveURL(/[#&]col=/);
  await expect(page.locator('#colorby-clear')).toBeVisible();
  await page.locator('#colorby-clear').click();
  await expect(page).not.toHaveURL(/[#&]col=/);
});

test('speed colours use the fixed speed bands from the shapes', async ({ page, errors: _ }) => {
  await openApp(page, 'c=running-track&col=running-track/maximumPermittedSpeed');
  const legend = page.locator('#legend');
  await expect(legend).toBeVisible();
  if (await legend.locator('.lg-toggle[aria-expanded="false"]').count()) await legend.locator('.lg-toggle').click();
  const rows = legend.locator('.lg-list .lg-row[data-key^="bin:"] .lbl');
  // Eight bands from atlas:colorClasses, listed even when empty, so high speed always has its own colours.
  await expect(rows).toHaveText([
    '≤ 40 km/h · Very very low speed', '41 – 80 km/h · Very low speed', '81 – 120 km/h · Low speed',
    '121 – 160 km/h · Conventional main line', '161 – 200 km/h · Enhanced conventional', '201 – 250 km/h · Fast line',
    '251 – 300 km/h · High-speed rail', '> 300 km/h · Very high-speed rail',
  ]);
  // The upper bound is inclusive: the sample's 80 km/h tracks are counted with 41 – 80.
  const theme = await (await page.request.post('/api/theme', {
    data: { env: 'prod', classes: ['running-track'], filters: {}, key: 'running-track/maximumPermittedSpeed' },
  })).json();
  const counts = legend.locator('.lg-list .lg-row[data-key^="bin:"] .count');
  await expect(counts).toHaveText((theme.bins as { count: number }[]).map((b) => b.count.toLocaleString('en')));
  const swatches = await legend.locator('.lg-list .lg-row[data-key^="bin:"] .lg-swatch').evaluateAll(
    (els) => els.map((e) => getComputedStyle(e).getPropertyValue('--sw').trim()));
  expect(new Set(swatches).size).toBe(8);
});
