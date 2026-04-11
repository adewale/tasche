// @ts-check
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

/**
 * Accessibility tests for Tasche — scans key views for WCAG violations.
 *
 * Uses axe-core to run automated accessibility audits against live views.
 * Initially filters for 'critical' impact only to avoid blocking on minor
 * issues (color contrast, etc.). Tighten to include 'serious' in follow-up.
 *
 * Requires DISABLE_AUTH=true on the target backend.
 * Run: E2E_BASE_URL=https://tasche-staging.adewale-883.workers.dev npx playwright test tests/e2e/accessibility.spec.js
 */

/** @type {string[]} */
const createdArticleIds = [];

test.beforeAll(async ({ request }) => {
  await request.get('/api/health');
});

test.afterAll(async ({ request }) => {
  for (const id of createdArticleIds) {
    try {
      await request.delete(`/api/articles/${id}`);
    } catch {
      /* best effort */
    }
  }
});

async function createArticle(request, url, title) {
  const data = { url };
  if (title) data.title = title;
  const resp = await request.post('/api/articles', { data });
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();
  createdArticleIds.push(body.id);
  return body;
}

/**
 * Run axe-core scan and return only critical violations.
 * @param {import('@playwright/test').Page} page
 */
async function scanForCritical(page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();

  return results.violations.filter((v) => v.impact === 'critical');
}

// ---------------------------------------------------------------------------
// Library view
// ---------------------------------------------------------------------------
test('Library view has no critical a11y violations', async ({ page }) => {
  await page.goto('/#/');
  await page.waitForSelector('.save-form', { timeout: 15000 });

  const violations = await scanForCritical(page);
  expect(violations).toEqual([]);
});

// ---------------------------------------------------------------------------
// Reader view
// ---------------------------------------------------------------------------
test('Reader view has no critical a11y violations', async ({ page, request }) => {
  const article = await createArticle(
    request,
    'https://example.com/a11y-reader-' + Date.now(),
    'A11y Reader Test',
  );
  await request.post(`/api/articles/${article.id}/process-now`);

  await page.goto(`/#/article/${article.id}`);
  await page.waitForSelector('.reader-title', { timeout: 15000 });
  await page.waitForSelector('.reader-content, .reader-status-message', { timeout: 15000 });

  const violations = await scanForCritical(page);
  expect(violations).toEqual([]);
});

// ---------------------------------------------------------------------------
// Tags view
// ---------------------------------------------------------------------------
test('Tags view has no critical a11y violations', async ({ page }) => {
  await page.goto('/#/tags');
  await page.waitForSelector('input[placeholder="New tag name..."]', { timeout: 15000 });

  const violations = await scanForCritical(page);
  expect(violations).toEqual([]);
});

// ---------------------------------------------------------------------------
// Search view
// ---------------------------------------------------------------------------
test('Search view has no critical a11y violations', async ({ page }) => {
  await page.goto('/#/search');
  await page.waitForSelector('.save-form', { timeout: 15000 });

  const violations = await scanForCritical(page);
  expect(violations).toEqual([]);
});

// ---------------------------------------------------------------------------
// Settings view
// ---------------------------------------------------------------------------
test('Settings view has no critical a11y violations', async ({ page }) => {
  await page.goto('/#/settings');
  await page.waitForSelector('h2.section-title', { timeout: 15000 });

  const violations = await scanForCritical(page);
  expect(violations).toEqual([]);
});

// ---------------------------------------------------------------------------
// Disabled buttons (loading states) a11y
// ---------------------------------------------------------------------------
test('Disabled Save button is accessible during loading', async ({ page }) => {
  await page.goto('/#/');
  await page.waitForSelector('input[placeholder="Paste a URL to save..."]');

  // Fill in a URL and click save
  const input = page.locator('input[placeholder="Paste a URL to save..."]');
  await input.fill('https://example.com/a11y-disabled-' + Date.now());

  const saveBtn = page.locator('button:has-text("Save")').first();
  await saveBtn.click();

  // Run a11y scan immediately (button may be disabled)
  const violations = await scanForCritical(page);
  expect(violations).toEqual([]);
});
