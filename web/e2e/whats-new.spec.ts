import releases from '../src/whats-new.json' with { type: 'json' };
import { expect, openApp, test } from './fixtures';

const [latest, previous] = releases.map((r) => r.version);
const about = (page: import('@playwright/test').Page) => page.locator('#about-btn');

test('a first visit gets no news dot, and About links to what is new', async ({ page, errors: _ }) => {
  await openApp(page);
  await expect(about(page)).not.toHaveClass(/\bhas-news\b/);
  await about(page).click();
  await page.getByRole('button', { name: `What's new in version ${latest}` }).click();
  await expect(page.getByRole('dialog', { name: 'About RINF Atlas' })).toBeHidden();
  const dlg = page.getByRole('dialog', { name: "What's new" });
  await expect(dlg).toBeVisible();
  await expect(dlg.getByRole('heading', { name: `Version ${latest}` })).toBeVisible();
  await expect(dlg.locator('.wn-tag').first()).toHaveText(/New|Improved|Fixed/);
  // Nothing was unseen on a first visit.
  await expect(dlg.locator('.wn-unseen')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(dlg).toBeHidden();
});

test('a returning visitor sees a dot until they open what is new', async ({ page, errors: _ }) => {
  await openApp(page);
  // A browser that used the app before this feature: other settings, no release seen yet.
  await page.evaluate(() => { localStorage.removeItem('atlas-whats-new-seen'); localStorage.setItem('atlas-theme', 'light'); });
  await page.reload();
  await expect(about(page)).toHaveClass(/\bhas-news\b/);

  // One that saw the previous release: the latest is marked as new, older ones are not.
  await page.evaluate((v) => localStorage.setItem('atlas-whats-new-seen', v), previous);
  await page.reload();
  await expect(about(page)).toHaveClass(/\bhas-news\b/);
  await expect(about(page)).toHaveAttribute('aria-label', new RegExp(`New in version ${latest.replace(/\./g, '\\.')}`));
  await page.locator('#help-btn').click();
  await page.getByRole('dialog', { name: 'Help' }).getByRole('button', { name: `What's new in version ${latest}` }).click();
  const dlg = page.getByRole('dialog', { name: "What's new" });
  await expect(dlg.locator('.wn-release').first().locator('.wn-unseen')).toHaveText('New for you');
  await expect(dlg.locator('.wn-unseen')).toHaveCount(1);
  await expect(about(page)).not.toHaveClass(/\bhas-news\b/);
  await page.keyboard.press('Escape');
  await page.reload();
  await expect(about(page)).not.toHaveClass(/\bhas-news\b/);
});
