import { classBox, classes, expect, openApp, resultCount, test } from './fixtures';

test('ticking a class filters the results and goes in the link', async ({ page, errors: _ }) => {
  await openApp(page);
  const tunnel = (await classes(page)).find((c) => c.class === 'tunnel')!;
  await classBox(page, 'tunnel').check();
  await expect.poll(() => resultCount(page)).toBe(tunnel.count);
  await expect(page).toHaveURL(/[#&]c=tunnel(&|$)/);

  // The link opens the same view.
  await page.reload();
  await expect(classBox(page, 'tunnel')).toBeChecked();
  await expect.poll(() => resultCount(page)).toBe(tunnel.count);

  await classBox(page, 'tunnel').uncheck();
  await expect(page).not.toHaveURL(/[#&]c=/);
});

test('a facet value narrows the results and shows a chip', async ({ page, errors: _ }) => {
  await openApp(page);
  const total = await resultCount(page);
  const facet = page.locator('#facets .terms label').filter({ hasText: 'Warning' });
  const expected = Number((await facet.locator('.count').innerText()).replace(/\D/g, ''));
  expect(expected).toBeGreaterThan(0);
  expect(expected).toBeLessThan(total);
  await facet.locator('input').check();
  await expect.poll(() => resultCount(page)).toBe(expected);
  await expect(page).toHaveURL(/[#&]f=/);
  await expect(page.locator('#chips')).toContainText('Warning');
  await facet.locator('input').uncheck();
  await expect.poll(() => resultCount(page)).toBe(total);
});

test('search finds a station by name', async ({ page, errors: _ }) => {
  await openApp(page);
  await page.locator('#q').fill('Bettembourg');
  await expect(page).toHaveURL(/[#&]q=Bettembourg/);
  await page.getByRole('tab', { name: /Results/ }).click();
  await expect(page.locator('#results .result-row').first()).toContainText('Bettembourg');
  const n = await resultCount(page);
  expect(n).toBeGreaterThan(0);
  expect(n).toBeLessThan(10);
});

test('a result opens its details, which close again', async ({ page, errors: _ }) => {
  await openApp(page, 'q=Bettembourg-Voyageurs&side=results');
  await page.locator('#results .result-row button').filter({ hasText: 'Bettembourg-Voyageurs' }).first().click();
  const detail = page.locator('#detail');
  await expect(detail.locator('h2')).toHaveText('Bettembourg-Voyageurs');
  await expect(detail.locator('.kicker')).toContainText('Operational Point');
  await expect(detail.locator('a.iri')).toHaveAttribute('href', /^http/);
  await expect(page).toHaveURL(/[#&]sel=/);
  // The properties tab lists the feature's values.
  await expect(detail.locator('.detail-body')).not.toBeEmpty();

  await detail.getByRole('button', { name: 'Close details' }).click();
  await expect(detail).toBeHidden();
  await expect(page).not.toHaveURL(/[#&]sel=/);
});

test('limit to map view counts only what is in view', async ({ page, errors: _ }) => {
  // Far from Luxembourg: nothing in view.
  await openApp(page, 'map=8/40.4/-3.7');
  const total = await resultCount(page);
  expect(total).toBeGreaterThan(0);
  await page.locator('#limit-view').check();
  await expect.poll(() => resultCount(page)).toBe(0);
  await expect(page).toHaveURL(/[#&]v=1/);
});

test('typing in a facet filter keeps the focus', async ({ page, errors: _ }) => {
  // The sample has one country; pad the list so the facet shows its filter box.
  await page.route('**/api/facets', async (route) => {
    const res = await route.fetch();
    const body = await res.json();
    const extra = ['AUT', 'BEL', 'CHE', 'CZE', 'DEU', 'DNK', 'ESP', 'FRA', 'ITA', 'NLD', 'POL']
      .map((c) => ({ value: `http://publications.europa.eu/resource/authority/country/${c}`, label: c, count: 1 }));
    body.facets.country.values.push(...extra);
    await route.fulfill({ response: res, json: body });
  });
  await openApp(page);
  const filter = page.getByRole('searchbox', { name: 'Filter Country' });
  await filter.click();
  await page.keyboard.type('bel', { delay: 50 });
  await expect(filter).toBeFocused();
  await expect(filter).toHaveValue('bel');
  await expect(page.locator('#facets [data-facet="country"] .terms label')).toHaveText([/BEL/]);
});
