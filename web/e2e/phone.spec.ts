import { expect, openApp, test } from './fixtures';

// Pixel 7 viewport: the side panel is a bottom sheet over the map.
test('side panel is a sheet that expands and collapses', async ({ page, errors: _ }) => {
  await openApp(page);
  const side = page.locator('#side');
  await expect(side).not.toHaveClass(/\bexpanded\b/);
  await page.locator('#sheet-toggle').click();
  await expect(side).toHaveClass(/\bexpanded\b/);
  await expect(page.locator('#class-list')).toBeVisible();
  await page.locator('#sheet-toggle').click();
  await expect(side).not.toHaveClass(/\bexpanded\b/);

  // A tab opens the sheet on that pane.
  await page.getByRole('tab', { name: /Results/ }).click();
  await expect(side).toHaveClass(/\bexpanded\b/);
  await expect(page.locator('#results .result-row').first()).toBeVisible();
});

test('no horizontal scrolling at phone width', async ({ page, errors: _ }) => {
  await openApp(page);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test('a result opens its details on a phone', async ({ page, errors: _ }) => {
  await openApp(page, 'q=Bettembourg-Voyageurs&side=results');
  await page.locator('#sheet-toggle').click();
  await page.locator('#results .result-row button').filter({ hasText: 'Bettembourg-Voyageurs' }).first().click();
  await expect(page.locator('#detail h2')).toHaveText('Bettembourg-Voyageurs');
  await expect(page.locator('#detail h2')).toBeInViewport();
});
