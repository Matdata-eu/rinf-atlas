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
  // The class counts drop to zero too, not back to the totals (#54).
  await expect(page.locator('#class-list .class-row .count').first()).toHaveText('0');
  for (const n of await page.locator('#class-list .class-row .count').allInnerTexts()) expect(n).toBe('0');
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

test('the data quality facet lists the severities the data issues layer shows', async ({ page, errors: _ }) => {
  // The unfiltered facet comes from the worker's precomputed counts; it once stayed empty
  // ("No values") while the layer, which reads the features themselves, showed them.
  await openApp(page);
  const block = page.locator('#facets [data-facet="quality"]');
  await expect(block.locator('.terms label').first()).toBeVisible();
  await expect(block).not.toContainText('No values');
  const shown = Object.fromEntries(await block.locator('.terms label').evaluateAll((ls) =>
    ls.map((l) => [l.getAttribute('title'), Number(l.querySelector('.count')!.textContent!.replace(/\D/g, ''))])));
  expect(Object.keys(shown)).toContain('http://www.w3.org/ns/shacl#Warning');
  // The same counts as a query that reads the features (a dataset selection is never precomputed).
  const ds = await (await page.request.get('/api/datasets?env=prod')).json() as { graph_iri: string; status: string }[];
  const graphs = ds.filter((d) => d.status === 'loaded').map((d) => d.graph_iri);
  const live = await (await page.request.post('/api/facets',
    { data: { env: 'prod', classes: [], filters: {}, graphs, facets: ['quality'] } })).json();
  const counted = Object.fromEntries((live.facets.quality.values as { value: string; count: number }[])
    .map((v) => [v.value, v.count]));
  expect(shown).toEqual(counted);
});

test('picking a data quality value turns on the data issues layer', async ({ page, errors: _ }) => {
  await openApp(page);
  const toggle = page.locator('#qa-toggle');
  await expect(toggle).not.toBeChecked();
  const block = page.locator('#facets [data-facet="quality"]');
  await block.locator('.terms label').first().click();
  await expect(page).toHaveURL(/[#&]qa=1/);
  await expect(toggle).toBeChecked();
  // It can still be switched off while the filter stays.
  await page.locator('#layers-btn').click();
  await toggle.uncheck();
  await expect(page).not.toHaveURL(/[#&]qa=1/);
  await expect(block.locator('.terms input:checked')).toHaveCount(1);
});

test('Full describe links show only when the deployment sets a template', async ({ page, errors: _ }) => {
  // Without ATLAS_DESCRIBE_URL (the test stack) there is no link.
  await openApp(page, 'q=Bettembourg-Voyageurs&side=results');
  await page.locator('#results .result-row button').filter({ hasText: 'Bettembourg-Voyageurs' }).first().click();
  const detail = page.locator('#detail');
  await expect(detail.locator('h2')).toHaveText('Bettembourg-Voyageurs');
  await expect(detail.getByRole('button', { name: 'Describe live' })).toBeVisible();
  await expect(detail.getByRole('link', { name: /Full describe/ })).toHaveCount(0);
});

test('Full describe links follow the selected feature and the Describe breadcrumb', async ({ page, errors: _ }) => {
  await page.route('**/api/envs', async (route) => {
    const res = await route.fetch();
    const body = (await res.json()) as Record<string, unknown>[];
    await route.fulfill({ response: res, json: body.map((e) => ({ ...e, describe_url: 'https://describe.example/describe?uri={iri}&env={env}' })) });
  });
  // Describe goes live to ERA: answer it here, with a link to an IRI that needs encoding.
  const odd = 'http://example.org/x?a=1&b=2#frag';
  await page.route('**/api/describe?*', (route) => {
    const iri = new URL(route.request().url()).searchParams.get('iri')!;
    return route.fulfill({ json: { iri, labels: {}, triples: iri === odd ? [] : [{ s: iri, p: 'http://www.w3.org/2000/01/rdf-schema#seeAlso', o: { type: 'uri', value: odd } }] } });
  });
  await openApp(page, 'q=Bettembourg-Voyageurs&side=results');
  await page.locator('#results .result-row button').filter({ hasText: 'Bettembourg-Voyageurs' }).first().click();
  const detail = page.locator('#detail');
  await expect(detail.locator('h2')).toHaveText('Bettembourg-Voyageurs');
  const iri = await detail.locator('a.iri').getAttribute('href');
  const link = detail.getByRole('link', { name: /Full describe/ });
  await expect(link).toHaveAttribute('href', `https://describe.example/describe?uri=${encodeURIComponent(iri!)}&env=prod`);
  await expect(link).toHaveAttribute('target', '_blank');
  await expect(link).toHaveAttribute('rel', 'noopener');

  // The Describe tab links the IRI at the end of the breadcrumb.
  await detail.getByRole('button', { name: 'Describe live' }).click();
  await expect(link).toHaveAttribute('href', `https://describe.example/describe?uri=${encodeURIComponent(iri!)}&env=prod`);
  await detail.locator('.describe-table .iri-btn').first().click();
  await expect(detail.locator('.crumb.current')).toHaveAttribute('title', odd);
  await expect(link).toHaveAttribute('href', 'https://describe.example/describe?uri=http%3A%2F%2Fexample.org%2Fx%3Fa%3D1%26b%3D2%23frag&env=prod');
});
