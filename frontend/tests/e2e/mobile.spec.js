// @ts-check
import { test, expect } from '@playwright/test';

/**
 * Mobile viewport tests for Tasche — iPhone 15 Pro Max (430×932).
 *
 * Verifies layout, element placement, and responsive behaviour at mobile
 * breakpoints that desktop-only tests miss entirely.
 *
 * Requires DISABLE_AUTH=true on the target backend.
 * Run: E2E_BASE_URL=http://localhost:8787 npx playwright test tests/e2e/mobile.spec.js
 */

const VIEWPORT = { width: 430, height: 932 };

/** @type {string[]} */
const createdArticleIds = [];
/** @type {string[]} */
const createdTagIds = [];

test.use({ viewport: VIEWPORT, hasTouch: true });

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
  for (const id of createdTagIds) {
    try {
      await request.delete(`/api/tags/${id}`);
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

// ---------------------------------------------------------------------------
// Header layout
// ---------------------------------------------------------------------------
test.describe('Header layout at mobile viewport', () => {
  test('header renders within viewport bounds', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.header')).toBeVisible({ timeout: 10000 });

    const header = await page.locator('.header').boundingBox();
    expect(header.x).toBeGreaterThanOrEqual(0);
    expect(header.width).toBeLessThanOrEqual(VIEWPORT.width);
  });

  test('header-inner does not overflow viewport', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.header-inner')).toBeVisible({ timeout: 10000 });

    const inner = await page.locator('.header-inner').boundingBox();
    expect(inner.x).toBeGreaterThanOrEqual(0);
    expect(inner.x + inner.width).toBeLessThanOrEqual(VIEWPORT.width);
  });

  test('logo is visible and left-aligned', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.header-logo')).toBeVisible({ timeout: 10000 });

    const logo = await page.locator('.header-logo').boundingBox();
    // Logo should be near the left edge (within padding)
    expect(logo.x).toBeLessThan(VIEWPORT.width / 4);
  });

  test('header actions are right-aligned', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.header-actions')).toBeVisible({ timeout: 10000 });

    const actions = await page.locator('.header-actions').boundingBox();
    // Actions should be in the right half of the viewport
    expect(actions.x + actions.width / 2).toBeGreaterThan(VIEWPORT.width / 2);
    // And not overflow the right edge
    expect(actions.x + actions.width).toBeLessThanOrEqual(VIEWPORT.width);
  });

  test('hamburger menu button is visible and in header-actions', async ({ page }) => {
    await page.goto('/');
    const hamburger = page.locator('.hamburger-menu button');
    await expect(hamburger).toBeVisible({ timeout: 10000 });

    const actions = await page.locator('.header-actions').boundingBox();
    const btn = await hamburger.boundingBox();

    // Hamburger should be within the header-actions area (± tolerance for borders)
    expect(btn.x).toBeGreaterThanOrEqual(actions.x - 2);
    expect(btn.x + btn.width).toBeLessThanOrEqual(actions.x + actions.width + 2);
  });

  test('search input is hidden at mobile width', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.header-logo')).toBeVisible({ timeout: 10000 });

    // At < 640px the search container should have display: none
    await expect(page.locator('.header-search')).toBeHidden();
  });
});

// ---------------------------------------------------------------------------
// Search behaviour on mobile
// ---------------------------------------------------------------------------
test.describe('Mobile search behaviour', () => {
  test('opening search hides logo and expands input', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.header-logo')).toBeVisible({ timeout: 10000 });

    // Click search button
    await page.locator('.header-actions button[title="Search"]').click();

    // Search should now be visible
    await expect(page.locator('.header-search')).toBeVisible();

    // Logo should be hidden when search is open on mobile
    await expect(page.locator('.header-logo')).toBeHidden();

    // Search input should be focused
    const input = page.locator('.header-search-input');
    await expect(input).toHaveCSS('visibility', 'visible');
    await expect(input).toBeFocused();
  });

  test('search input spans most of the header width', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.header-logo')).toBeVisible({ timeout: 10000 });

    await page.locator('.header-actions button[title="Search"]').click();
    await expect(page.locator('.header-search')).toBeVisible();

    const search = await page.locator('.header-search').boundingBox();
    // Should use at least 50% of viewport width on mobile
    expect(search.width).toBeGreaterThan(VIEWPORT.width * 0.5);
    // Should not overflow
    expect(search.x + search.width).toBeLessThanOrEqual(VIEWPORT.width);
  });
});

// ---------------------------------------------------------------------------
// Navigation via hamburger
// ---------------------------------------------------------------------------
test.describe('Mobile hamburger navigation', () => {
  test('hamburger opens dropdown with nav links', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.header-logo')).toBeVisible({ timeout: 10000 });

    await page.locator('.hamburger-menu button').click();
    const dropdown = page.locator('.hamburger-dropdown');
    await expect(dropdown).toBeVisible();

    // Dropdown should be within viewport
    const box = await dropdown.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(VIEWPORT.width);
  });

  test('can navigate to Tags via hamburger', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.header-logo')).toBeVisible({ timeout: 10000 });

    await page.locator('.hamburger-menu button').click();
    await page.locator('a[href="#/tags"]').click();
    await expect(page.locator('input[placeholder="New tag name..."]')).toBeVisible({
      timeout: 5000,
    });
  });

  test('can navigate to Settings via hamburger', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.header-logo')).toBeVisible({ timeout: 10000 });

    await page.locator('.hamburger-menu button').click();
    await page.locator('a[href="#/settings"]').click();
    await expect(page.locator('h2.section-title').first()).toHaveText('Settings', {
      timeout: 5000,
    });
  });
});

// ---------------------------------------------------------------------------
// Article cards
// ---------------------------------------------------------------------------
test.describe('Article cards at mobile viewport', () => {
  test('article card fits within viewport width', async ({ page, request }) => {
    await createArticle(request, 'https://example.com/mobile-card-test', 'Mobile Card Test');
    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    const card = await page.locator('.article-card').first().boundingBox();
    expect(card.x).toBeGreaterThanOrEqual(0);
    expect(card.x + card.width).toBeLessThanOrEqual(VIEWPORT.width);
  });

  test('article card thumbnail is 72px at mobile width', async ({ page, request }) => {
    await createArticle(request, 'https://example.com/mobile-thumb-test', 'Thumb Test');
    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    const thumbnail = page.locator('.article-card-thumbnail').first();
    await expect(thumbnail).toBeVisible({ timeout: 5000 });
    const box = await thumbnail.boundingBox();
    expect(box.width).toBe(72);
    expect(box.height).toBe(72);
  });

  test('article card actions are tappable size (44px min)', async ({ page, request }) => {
    await createArticle(request, 'https://example.com/mobile-tap-test', 'Tap Target Test');
    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    const actionButtons = page.locator('.article-card-actions button');
    const count = await actionButtons.count();
    for (let i = 0; i < count; i++) {
      const box = await actionButtons.nth(i).boundingBox();
      if (box) {
        // Touch targets should be at least 44px for pointer:coarse
        expect(box.width).toBeGreaterThanOrEqual(44);
        expect(box.height).toBeGreaterThanOrEqual(44);
      }
    }
  });
});

// ---------------------------------------------------------------------------
// Save form
// ---------------------------------------------------------------------------
test.describe('Save form at mobile viewport', () => {
  test('save form fits within viewport', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.save-form')).toBeVisible({ timeout: 10000 });

    const form = await page.locator('.save-form').boundingBox();
    expect(form.x).toBeGreaterThanOrEqual(0);
    expect(form.x + form.width).toBeLessThanOrEqual(VIEWPORT.width);
  });

  test('save URL input is usable on mobile', async ({ page }) => {
    await page.goto('/');
    const input = page.locator('input[placeholder="Paste a URL to save..."]');
    await expect(input).toBeVisible({ timeout: 10000 });

    await input.tap();
    await expect(input).toBeFocused();
  });
});

// ---------------------------------------------------------------------------
// Reader view
// ---------------------------------------------------------------------------
test.describe('Reader view at mobile viewport', () => {
  test('reader view loads and fits viewport', async ({ page, request }) => {
    const article = await createArticle(
      request,
      'https://example.com/mobile-reader-test',
      'Mobile Reader Test',
    );
    await page.goto(`/#/article/${article.id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // Reader title should use mobile font size (1.5rem = 24px at default 16px base)
    const title = page.locator('.reader-title');
    await expect(title).toBeVisible();
    const fontSize = await title.evaluate((el) => getComputedStyle(el).fontSize);
    const sizeNum = parseFloat(fontSize);
    // Should be around 24px (1.5rem) on mobile, not 36px (2.25rem desktop)
    expect(sizeNum).toBeLessThanOrEqual(28);
  });

  test('reader back button is visible and tappable', async ({ page, request }) => {
    const article = await createArticle(
      request,
      'https://example.com/mobile-back-test',
      'Back Button Test',
    );
    await page.goto(`/#/article/${article.id}`);
    await expect(page.locator('a.reader-back')).toBeVisible({ timeout: 10000 });

    const back = await page.locator('a.reader-back').boundingBox();
    expect(back.x).toBeGreaterThanOrEqual(0);
    expect(back.x + back.width).toBeLessThanOrEqual(VIEWPORT.width);
  });
});

// ---------------------------------------------------------------------------
// No horizontal overflow
// ---------------------------------------------------------------------------
test.describe('No horizontal overflow on mobile', () => {
  test('library view has no horizontal scrollbar', async ({ page, request }) => {
    await createArticle(request, 'https://example.com/mobile-overflow-test', 'Overflow Test');
    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    const hasOverflow = await page.evaluate(() => {
      return document.documentElement.scrollWidth > document.documentElement.clientWidth;
    });
    expect(hasOverflow).toBe(false);
  });

  test('tags view has no horizontal scrollbar', async ({ page }) => {
    await page.goto('/#/tags');
    await expect(page.locator('h2.section-title').first()).toBeVisible({ timeout: 10000 });

    const hasOverflow = await page.evaluate(() => {
      return document.documentElement.scrollWidth > document.documentElement.clientWidth;
    });
    expect(hasOverflow).toBe(false);
  });

  test('settings view has no horizontal scrollbar', async ({ page }) => {
    await page.goto('/#/settings');
    await expect(page.locator('h2.section-title')).toBeVisible({ timeout: 10000 });

    const hasOverflow = await page.evaluate(() => {
      return document.documentElement.scrollWidth > document.documentElement.clientWidth;
    });
    expect(hasOverflow).toBe(false);
  });
});
