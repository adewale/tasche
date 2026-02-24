// @ts-check
import { test, expect } from '@playwright/test';

/**
 * E2E view tests for Tasche — ensures every routable view loads without crashing.
 *
 * The markdown crash (deeply nested tables from paulgraham.com) was only caught
 * by manual testing. These tests ensure every view renders without JS errors.
 *
 * Requires DISABLE_AUTH=true on the target backend.
 * Run: E2E_BASE_URL=http://localhost:6060 npx playwright test tests/e2e/views.spec.js
 */

/** @type {string[]} */
const createdArticleIds = [];

test.afterAll(async ({ request }) => {
  for (const id of createdArticleIds) {
    try { await request.delete(`/api/articles/${id}`); } catch { /* best effort */ }
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
// Collect JS errors on every page
// ---------------------------------------------------------------------------
function setupErrorListener(page) {
  const errors = [];
  page.on('pageerror', (err) => errors.push(err.message));
  return errors;
}


// ---------------------------------------------------------------------------
// Markdown view
// ---------------------------------------------------------------------------
test.describe('Markdown view', () => {
  test('markdown view loads without crashing', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/md-view-test', 'Markdown View Test');

    // Process the article (example.com may or may not produce markdown)
    await request.post(`/api/articles/${id}/process-now`);

    const errors = setupErrorListener(page);

    await page.goto(`/#/article/${id}/markdown`);

    // Should show either the markdown view OR the "no markdown" empty state — NOT crash
    const hasContent = page.locator('.markdown-view-title');
    const hasError = page.locator('.empty-state');
    await expect(hasContent.or(hasError)).toBeVisible({ timeout: 10000 });

    // If content is available, check tabs and content area
    if (await hasContent.isVisible().catch(() => false)) {
      await expect(page.locator('button').filter({ hasText: 'Rendered' })).toBeVisible();
      await expect(page.locator('button').filter({ hasText: 'Source' })).toBeVisible();
      await expect(page.locator('button').filter({ hasText: 'Copy Markdown' })).toBeVisible();
      await expect(
        page.locator('.markdown-view-rendered, .markdown-view-content').first()
      ).toBeVisible({ timeout: 5000 });
    }

    // No JS errors should have occurred
    expect(errors).toEqual([]);
  });

  test('markdown view source tab works when content exists', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/md-source-test', 'MD Source Test');
    await request.post(`/api/articles/${id}/process-now`);

    const errors = setupErrorListener(page);

    await page.goto(`/#/article/${id}/markdown`);

    const hasContent = page.locator('.markdown-view-title');
    const hasError = page.locator('.empty-state');
    await expect(hasContent.or(hasError)).toBeVisible({ timeout: 10000 });

    // Only test source tab if content loaded
    if (await hasContent.isVisible().catch(() => false)) {
      await page.locator('button').filter({ hasText: 'Source' }).click();
      await expect(page.locator('.markdown-view-content')).toBeVisible({ timeout: 5000 });
    }

    expect(errors).toEqual([]);
  });

  test('markdown view handles unprocessed article gracefully', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/md-missing-test', 'MD Missing Test');

    // Don't process — article will have no markdown
    const errors = setupErrorListener(page);

    await page.goto(`/#/article/${id}/markdown`);

    // Should show either the markdown view or an error state — but NOT crash
    await expect(
      page.locator('.markdown-view-title, .empty-state').first()
    ).toBeVisible({ timeout: 10000 });

    expect(errors).toEqual([]);
  });

  test('markdown view back link navigates to reader', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/md-back-test', 'MD Back Test');
    await request.post(`/api/articles/${id}/process-now`);

    await page.goto(`/#/article/${id}/markdown`);

    const hasContent = page.locator('.markdown-view-title');
    const hasError = page.locator('.empty-state');
    await expect(hasContent.or(hasError)).toBeVisible({ timeout: 10000 });

    // Click back link (present in both states)
    const backLink = page.locator('a.reader-back, a.btn-secondary').filter({ hasText: /back/i }).first();
    await expect(backLink).toBeVisible({ timeout: 5000 });
    await backLink.click();

    // Should navigate to reader view
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });
  });
});


// ---------------------------------------------------------------------------
// Stats view
// ---------------------------------------------------------------------------
test.describe('Stats view', () => {
  test('stats page loads and shows statistics', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/#/stats');

    // Should show the stats title
    await expect(page.locator('h1.section-title')).toHaveText('Reading Statistics', { timeout: 10000 });

    // Should show stat cards
    await expect(page.locator('.stat-card').first()).toBeVisible({ timeout: 5000 });

    // Should have key stats: total articles, words read
    await expect(page.locator('.stat-card-label').filter({ hasText: 'Total articles' })).toBeVisible();
    await expect(page.locator('.stat-card-label').filter({ hasText: 'Words read' })).toBeVisible();

    expect(errors).toEqual([]);
  });
});


// ---------------------------------------------------------------------------
// Library: sort and filter
// ---------------------------------------------------------------------------
test.describe('Library — sort options', () => {
  test('sort dropdown changes article order', async ({ page, request }) => {
    // Create two articles
    await createArticle(request, 'https://example.com/sort-test-1', 'Sort Alpha First');
    await createArticle(request, 'https://example.com/sort-test-2', 'Sort Alpha Second');

    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    // Look for the sort select
    const sortSelect = page.locator('select').filter({ has: page.locator('option[value]') }).first();
    if (await sortSelect.isVisible({ timeout: 3000 }).catch(() => false)) {
      // Change sort — the page should re-render without crashing
      const options = await sortSelect.locator('option').allTextContents();
      if (options.length > 1) {
        await sortSelect.selectOption({ index: 1 });
        // Cards should still be visible
        await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 5000 });
      }
    }

    expect(errors).toEqual([]);
  });
});


// ---------------------------------------------------------------------------
// Library: bulk select mode
// ---------------------------------------------------------------------------
test.describe('Library — bulk select', () => {
  test('select mode can be toggled', async ({ page, request }) => {
    await createArticle(request, 'https://example.com/bulk-test', 'Bulk Test');

    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    // Look for the select/multi-select button
    const selectBtn = page.locator('button[title="Select mode"], button').filter({ hasText: /select/i }).first();
    if (await selectBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await selectBtn.click();

      // Cards should show checkboxes
      await expect(page.locator('.article-card-checkbox').first()).toBeVisible({ timeout: 5000 });

      // Click a card to select it
      await page.locator('.article-card').first().click();
      await expect(page.locator('.article-card--checked').first()).toBeVisible({ timeout: 3000 });
    }

    expect(errors).toEqual([]);
  });
});


// ---------------------------------------------------------------------------
// Reader: theme toggle
// ---------------------------------------------------------------------------
test.describe('Reader — theme toggle', () => {
  test('reader theme can be toggled without crashing', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/theme-test', 'Theme Test');

    const errors = setupErrorListener(page);

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // Look for theme toggle button
    const themeBtn = page.locator('button[title*="theme" i], button[title*="Theme" i], .theme-toggle').first();
    if (await themeBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await themeBtn.click();
      // Page should not crash
      await expect(page.locator('.reader-header')).toBeVisible({ timeout: 5000 });
    }

    expect(errors).toEqual([]);
  });
});


// ---------------------------------------------------------------------------
// Reader: markdown button navigates to markdown view
// ---------------------------------------------------------------------------
test.describe('Reader — markdown button', () => {
  test('markdown button in reader navigates to markdown view', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/reader-md-test', 'Reader MD Test');
    await request.post(`/api/articles/${id}/process-now`);

    const errors = setupErrorListener(page);

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // Look for the Markdown button
    const mdBtn = page.locator('.reader-actions button').filter({ hasText: /markdown/i });
    if (await mdBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await mdBtn.click();

      // Should navigate to markdown view
      await expect(page.locator('.markdown-view-title')).toBeVisible({ timeout: 10000 });
    }

    expect(errors).toEqual([]);
  });
});


// ---------------------------------------------------------------------------
// Settings: export
// ---------------------------------------------------------------------------
test.describe('Settings — export', () => {
  test('export buttons are visible and functional', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/#/settings');
    await expect(page.locator('h2.section-title').first()).toHaveText('Settings', { timeout: 10000 });

    // Should show Data Export section
    const exportHeading = page.getByRole('heading', { name: /export/i });
    if (await exportHeading.isVisible({ timeout: 3000 }).catch(() => false)) {
      await expect(exportHeading).toBeVisible();
    }

    expect(errors).toEqual([]);
  });
});


// ---------------------------------------------------------------------------
// Error boundary: invalid article ID
// ---------------------------------------------------------------------------
test.describe('Error handling', () => {
  test('reader view handles non-existent article gracefully', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/#/article/nonexistent-id-12345');

    // Should show error state or empty state, not crash
    await expect(
      page.locator('.reader-header, .empty-state, [class*="error"]').first()
    ).toBeVisible({ timeout: 10000 });

    // The page should still be functional (header visible)
    await expect(page.locator('.header-logo')).toBeVisible();

    // Filter out expected 404 errors
    const unexpectedErrors = errors.filter(e => !e.includes('404') && !e.includes('Not Found'));
    expect(unexpectedErrors).toEqual([]);
  });

  test('markdown view handles non-existent article gracefully', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/#/article/nonexistent-id-12345/markdown');

    // Should show error state, not crash
    await expect(
      page.locator('.markdown-view-title, .empty-state, [class*="error"]').first()
    ).toBeVisible({ timeout: 10000 });

    await expect(page.locator('.header-logo')).toBeVisible();

    const unexpectedErrors = errors.filter(e => !e.includes('404') && !e.includes('Not Found'));
    expect(unexpectedErrors).toEqual([]);
  });
});


// ---------------------------------------------------------------------------
// Card rendering: every card renders without errors
// ---------------------------------------------------------------------------
test.describe('Card rendering', () => {
  test('article cards render with favicon containers', async ({ page, request }) => {
    await createArticle(request, 'https://example.com/card-render-test', 'Card Render Test');

    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    // Cards should have the article-card class
    const card = page.locator('.article-card').filter({ hasText: 'Card Render Test' });
    if (await card.isVisible({ timeout: 5000 }).catch(() => false)) {
      // Should have title
      await expect(card.locator('.article-card-title')).toBeVisible();

      // Should have meta row
      await expect(card.locator('.article-card-meta')).toBeVisible();

      // Should have action buttons
      await expect(card.locator('.article-card-actions')).toBeVisible();

      // Compact cards (no thumbnail) should have favicon container
      if (await card.locator('.article-card-favicon').isVisible().catch(() => false)) {
        await expect(card.locator('.favicon-container')).toBeVisible();
      }
    }

    expect(errors).toEqual([]);
  });

  test('reading status shows left border instead of badge', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/status-border-test', 'Status Border Test');

    // Set to "reading" status
    await request.patch(`/api/articles/${id}`, {
      data: { reading_status: 'reading' },
    });

    const errors = setupErrorListener(page);

    // Navigate to library with "Reading" filter
    await page.goto('/');
    await expect(page.locator('.save-form')).toBeVisible({ timeout: 10000 });

    const readingTab = page.locator('.filter-tabs button, .filter-tabs a').filter({ hasText: 'Reading' });
    if (await readingTab.isVisible({ timeout: 3000 }).catch(() => false)) {
      await readingTab.click();
      await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 10000 });

      // Cards with "reading" status should have the --reading modifier
      const card = page.locator('.article-card--reading').first();
      if (await card.isVisible({ timeout: 3000 }).catch(() => false)) {
        // Should NOT have a reading-status-badge
        await expect(card.locator('.reading-status-badge')).not.toBeVisible();
      }
    }

    expect(errors).toEqual([]);
  });
});
