import { expect, openApp, test } from './fixtures';

test.describe('first visit', () => {
  test.use({ tour: true });

  test('opens the tour once and pages through it', async ({ page, errors: _ }) => {
    await openApp(page);
    const tour = page.getByRole('dialog', { name: 'How RINF Atlas works' });
    await expect(tour).toBeVisible();
    await expect(tour.locator('.tour-item')).toHaveCount(3);
    await expect(tour.getByRole('button', { name: 'Back' })).toBeHidden();

    const titles: string[] = [];
    for (let i = 0; i < 4; i++) {
      await expect(tour.locator('.tour-step')).toHaveText(`${i + 1} of 4`);
      await expect(tour.locator('.tour-item')).toHaveCount(3);
      await expect(tour.locator('.tour-item svg')).toHaveCount(3);
      titles.push(await tour.locator('h2').innerText());
      if (i < 3) await tour.getByRole('button', { name: 'Next' }).click();
    }
    expect(new Set(titles).size).toBe(4);
    await tour.getByRole('button', { name: 'Back' }).click();
    await expect(tour.locator('.tour-step')).toHaveText('3 of 4');
    await tour.getByRole('button', { name: /Page 4/ }).click();
    await tour.getByRole('button', { name: 'Start exploring' }).click();
    await expect(tour).toBeHidden();

    // Remembered: not shown again on the next visit.
    await openApp(page);
    await expect(page.locator('#class-list input[type=checkbox]').first()).toBeAttached();
    await expect(tour).toBeHidden();
  });

  test('Escape closes it for good', async ({ page, errors: _ }) => {
    await openApp(page);
    const tour = page.getByRole('dialog', { name: 'How RINF Atlas works' });
    await expect(tour).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(tour).toBeHidden();
    await page.reload();
    await expect(page.locator('#class-list input[type=checkbox]').first()).toBeAttached();
    await expect(tour).toBeHidden();
  });
});

test('the help dialog reopens the tour', async ({ page, errors: _ }) => {
  await openApp(page);
  const tour = page.getByRole('dialog', { name: 'How RINF Atlas works' });
  await expect(tour).toBeHidden();
  await page.locator('#help-btn').click();
  await page.getByRole('dialog', { name: 'Help' }).getByRole('button', { name: 'How RINF Atlas works' }).click();
  await expect(page.getByRole('dialog', { name: 'Help' })).toBeHidden();
  await expect(tour).toBeVisible();
  await expect(tour.locator('.tour-step')).toHaveText('1 of 4');
  await tour.getByRole('button', { name: 'Skip the tour' }).click();
  await expect(tour).toBeHidden();
});
