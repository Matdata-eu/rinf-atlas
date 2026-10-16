import { defineConfig, devices } from '@playwright/test';

// Browser regression tests against a running stack with the Luxembourg sample loaded
// (`docker compose up -d --build db api web && bash tests/smoke.sh`, as in CI).
// ATLAS_URL points them at another instance; the default is the compose port.
export default defineConfig({
  testDir: 'e2e',
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.ATLAS_URL || `http://localhost:${process.env.ATLAS_PORT || 8080}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 } }, testIgnore: /phone/ },
    { name: 'phone', use: { ...devices['Pixel 7'] }, testMatch: /phone/ },
  ],
});
