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

test('CSV export downloads the current results', async ({ page, errors: _ }) => {
  await openApp(page, 'c=tunnel&side=results');
  await expect.poll(() => resultCount(page)).toBeGreaterThan(0);
  const n = await resultCount(page);
  const download = page.waitForEvent('download');
  await page.locator('#export-csv').click();
  const file = await (await download).path();
  const rows = (await readFile(file, 'utf8')).replace(/^﻿/, '').trim().split(/\r?\n/);
  expect(rows[0].split(',')[0]).toBe('IRI');
  expect(rows.length - 1).toBe(n);
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
