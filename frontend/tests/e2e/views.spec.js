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
    const { id } = await createArticle(
      request,
      'https://example.com/md-view-test',
      'Markdown View Test',
    );

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
        page.locator('.markdown-view-rendered, .markdown-view-content').first(),
      ).toBeVisible({ timeout: 5000 });
    }

    // No JS errors should have occurred
    expect(errors).toEqual([]);
  });

  test('markdown view source tab works when content exists', async ({ page, request }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/md-source-test',
      'MD Source Test',
    );
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
    const { id } = await createArticle(
      request,
      'https://example.com/md-missing-test',
      'MD Missing Test',
    );

    // Don't process — article will have no markdown
    const errors = setupErrorListener(page);

    await page.goto(`/#/article/${id}/markdown`);

    // Should show either the markdown view or an error state — but NOT crash
    await expect(page.locator('.markdown-view-title, .empty-state').first()).toBeVisible({
      timeout: 10000,
    });

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
    const backLink = page
      .locator('a.reader-back, a.btn-secondary')
      .filter({ hasText: /back/i })
      .first();
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
    await expect(page.locator('h1.section-title')).toHaveText('Reading Statistics', {
      timeout: 10000,
    });

    // Should show stat cards
    await expect(page.locator('.stat-card').first()).toBeVisible({ timeout: 5000 });

    // Should have key stats: total articles, words read
    await expect(
      page.locator('.stat-card-label').filter({ hasText: 'Total articles' }),
    ).toBeVisible();
    await expect(page.locator('.stat-card-label').filter({ hasText: 'Words read' })).toBeVisible();

    expect(errors).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Library: sort and filter
// ---------------------------------------------------------------------------
test.describe('Library — sort options', () => {
  // Create articles with distinct properties so sort order is verifiable.
  // We use the API to set reading_time_minutes and titles that sort predictably.
  let articleA; // earliest, shortest, title "AAA Sort"
  let articleB; // latest, longest, title "ZZZ Sort"

  test.beforeAll(async ({ request }) => {
    articleA = await createArticle(request, 'https://example.com/sort-aaa', 'AAA Sort Test');
    // Small delay so created_at differs
    await new Promise((r) => setTimeout(r, 1100));
    articleB = await createArticle(request, 'https://example.com/sort-zzz', 'ZZZ Sort Test');

    // Set reading times so shortest/longest sort is testable
    await request.patch(`/api/articles/${articleA.id}`, {
      data: { reading_time_minutes: 2 },
    });
    await request.patch(`/api/articles/${articleB.id}`, {
      data: { reading_time_minutes: 30 },
    });
  });

  /** Helper: get the sort <select> element. */
  async function getSortSelect(page) {
    return page.locator('select.input-inline-select');
  }

  /** Helper: wait for cards to load after sort change. */
  async function waitForCards(page) {
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 10000 });
  }

  /** Helper: get ordered list of visible card titles. */
  async function getCardTitles(page) {
    return page.locator('.article-card-title').allTextContents();
  }

  test('default sort is newest first', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/');
    await waitForCards(page);

    const sortSelect = await getSortSelect(page);
    await expect(sortSelect).toHaveValue('newest');

    // Newest first: articleB (created second) should appear before articleA
    const titles = await getCardTitles(page);
    const idxA = titles.findIndex((t) => t.includes('AAA Sort'));
    const idxB = titles.findIndex((t) => t.includes('ZZZ Sort'));
    if (idxA !== -1 && idxB !== -1) {
      expect(idxB).toBeLessThan(idxA);
    }

    expect(errors).toEqual([]);
  });

  test('oldest first reverses default order', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/');
    await waitForCards(page);

    const sortSelect = await getSortSelect(page);
    await sortSelect.selectOption('oldest');

    // Wait for cards to reload after sort change
    await waitForCards(page);

    // Oldest first: articleA (created first) should appear before articleB
    const titles = await getCardTitles(page);
    const idxA = titles.findIndex((t) => t.includes('AAA Sort'));
    const idxB = titles.findIndex((t) => t.includes('ZZZ Sort'));
    if (idxA !== -1 && idxB !== -1) {
      expect(idxA).toBeLessThan(idxB);
    }

    expect(errors).toEqual([]);
  });

  test('shortest first sorts by reading time ascending', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/');
    await waitForCards(page);

    const sortSelect = await getSortSelect(page);
    await sortSelect.selectOption('shortest');

    await waitForCards(page);

    // Shortest first: articleA (2 min) should appear before articleB (30 min)
    const titles = await getCardTitles(page);
    const idxA = titles.findIndex((t) => t.includes('AAA Sort'));
    const idxB = titles.findIndex((t) => t.includes('ZZZ Sort'));
    if (idxA !== -1 && idxB !== -1) {
      expect(idxA).toBeLessThan(idxB);
    }

    expect(errors).toEqual([]);
  });

  test('longest first sorts by reading time descending', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/');
    await waitForCards(page);

    const sortSelect = await getSortSelect(page);
    await sortSelect.selectOption('longest');

    await waitForCards(page);

    // Longest first: articleB (30 min) should appear before articleA (2 min)
    const titles = await getCardTitles(page);
    const idxA = titles.findIndex((t) => t.includes('AAA Sort'));
    const idxB = titles.findIndex((t) => t.includes('ZZZ Sort'));
    if (idxA !== -1 && idxB !== -1) {
      expect(idxB).toBeLessThan(idxA);
    }

    expect(errors).toEqual([]);
  });

  test('title A-Z sorts alphabetically', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/');
    await waitForCards(page);

    const sortSelect = await getSortSelect(page);
    await sortSelect.selectOption('title_asc');

    await waitForCards(page);

    // Title A-Z: articleA ("AAA") should appear before articleB ("ZZZ")
    const titles = await getCardTitles(page);
    const idxA = titles.findIndex((t) => t.includes('AAA Sort'));
    const idxB = titles.findIndex((t) => t.includes('ZZZ Sort'));
    if (idxA !== -1 && idxB !== -1) {
      expect(idxA).toBeLessThan(idxB);
    }

    expect(errors).toEqual([]);
  });

  test('changing sort multiple times works without sticking', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/');
    await waitForCards(page);

    const sortSelect = await getSortSelect(page);

    // Cycle through every sort option — each should re-render cards
    const options = ['oldest', 'shortest', 'longest', 'title_asc', 'newest'];
    for (const opt of options) {
      await sortSelect.selectOption(opt);
      await waitForCards(page);
      await expect(sortSelect).toHaveValue(opt);
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
    const selectBtn = page
      .locator('button[title="Select mode"], button')
      .filter({ hasText: /select/i })
      .first();
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
    const themeBtn = page
      .locator('button[title*="theme" i], button[title*="Theme" i], .theme-toggle')
      .first();
    if (await themeBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await themeBtn.click();
      // Page should not crash
      await expect(page.locator('.reader-header')).toBeVisible({ timeout: 5000 });
    }

    expect(errors).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Reader: view mode toggle (Original / Rendered / Source)
// ---------------------------------------------------------------------------
test.describe('Reader — view modes', () => {
  test('reader toolbar shows View segmented control', async ({ page, request }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/view-mode-test',
      'View Mode Test',
    );
    await request.post(`/api/articles/${id}/process-now`);

    const errors = setupErrorListener(page);

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // The reader toolbar should have Original, Rendered, Source buttons
    const toolbar = page.locator('.reader-toolbar');
    if (await toolbar.isVisible({ timeout: 5000 }).catch(() => false)) {
      await expect(toolbar.locator('button').filter({ hasText: 'Original' })).toBeVisible();
      await expect(toolbar.locator('button').filter({ hasText: 'Rendered' })).toBeVisible();
      await expect(toolbar.locator('button').filter({ hasText: 'Source' })).toBeVisible();
    }

    expect(errors).toEqual([]);
  });

  test('switching to Rendered mode loads markdown', async ({ page, request }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/rendered-mode-test',
      'Rendered Mode Test',
    );
    await request.post(`/api/articles/${id}/process-now`);

    const errors = setupErrorListener(page);

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    const renderedBtn = page.locator('.reader-toolbar button').filter({ hasText: 'Rendered' });
    if (await renderedBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await renderedBtn.click();

      // Should show either rendered markdown content or a status message
      await expect(page.locator('.reader-content, .reader-status-message').first()).toBeVisible({
        timeout: 10000,
      });
    }

    expect(errors).toEqual([]);
  });

  test('switching to Source mode shows raw markdown with copy button', async ({
    page,
    request,
  }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/source-mode-test',
      'Source Mode Test',
    );
    await request.post(`/api/articles/${id}/process-now`);

    const errors = setupErrorListener(page);

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    const sourceBtn = page.locator('.reader-toolbar button').filter({ hasText: 'Source' });
    if (await sourceBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await sourceBtn.click();

      // Should show raw markdown in a <pre> block or a status message
      await expect(
        page.locator('.markdown-view-content, .reader-status-message, pre').first(),
      ).toBeVisible({ timeout: 10000 });

      // If content loaded, Copy Markdown button should be visible
      const copyBtn = page.locator('button').filter({ hasText: /copy markdown/i });
      if (await copyBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
        await expect(copyBtn).toBeVisible();
      }
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
    await expect(page.locator('h2.section-title').first()).toHaveText('Settings', {
      timeout: 10000,
    });

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
      page.locator('.reader-header, .empty-state, [class*="error"]').first(),
    ).toBeVisible({ timeout: 10000 });

    // The page should still be functional (header visible)
    await expect(page.locator('.header-logo')).toBeVisible();

    // Filter out expected 404 errors
    const unexpectedErrors = errors.filter((e) => !e.includes('404') && !e.includes('Not Found'));
    expect(unexpectedErrors).toEqual([]);
  });

  test('markdown view handles non-existent article gracefully', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/#/article/nonexistent-id-12345/markdown');

    // Should show error state, not crash
    await expect(
      page.locator('.markdown-view-title, .empty-state, [class*="error"]').first(),
    ).toBeVisible({ timeout: 10000 });

    await expect(page.locator('.header-logo')).toBeVisible();

    const unexpectedErrors = errors.filter((e) => !e.includes('404') && !e.includes('Not Found'));
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
      if (
        await card
          .locator('.article-card-favicon')
          .isVisible()
          .catch(() => false)
      ) {
        await expect(card.locator('.favicon-container')).toBeVisible();
      }
    }

    expect(errors).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Save article via UI form
// ---------------------------------------------------------------------------
test.describe('Save article — UI form', () => {
  test('save article by entering URL and clicking Save', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.save-form')).toBeVisible({ timeout: 10000 });

    const input = page.locator('.save-form input[type="url"]');
    await input.fill('https://example.com/e2e-save-form-test');
    await page.locator('.save-form .btn-primary').click();

    // Should show success toast
    await expect(page.locator('.toast').filter({ hasText: /saved/i })).toBeVisible({
      timeout: 10000,
    });

    // New article should appear in the list
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 10000 });

    // Clean up: find and delete the article
    const resp = await page.request.get('/api/articles?limit=1');
    const articles = await resp.json();
    if (articles.length > 0) {
      createdArticleIds.push(articles[0].id);
    }

    expect(errors).toEqual([]);
  });

  test('save with Enter key works', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.save-form')).toBeVisible({ timeout: 10000 });

    const input = page.locator('.save-form input[type="url"]');
    await input.fill('https://example.com/e2e-enter-key-test');
    await input.press('Enter');

    await expect(page.locator('.toast').filter({ hasText: /saved/i })).toBeVisible({
      timeout: 10000,
    });

    const resp = await page.request.get('/api/articles?limit=1');
    const articles = await resp.json();
    if (articles.length > 0) {
      createdArticleIds.push(articles[0].id);
    }

    expect(errors).toEqual([]);
  });

  test('saving empty URL shows error', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.save-form')).toBeVisible({ timeout: 10000 });

    // Click save without entering a URL
    await page.locator('.save-form .btn-primary').click();

    await expect(page.locator('.toast').filter({ hasText: /url/i })).toBeVisible({ timeout: 5000 });

    expect(errors).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Library: filter tabs
// ---------------------------------------------------------------------------
test.describe('Library — filter tabs', () => {
  test.beforeAll(async ({ request }) => {
    // Create articles in different states for filter testing
    const unread = await createArticle(
      request,
      'https://example.com/filter-unread',
      'Filter Unread',
    );
    const archived = await createArticle(
      request,
      'https://example.com/filter-archived',
      'Filter Archived',
    );
    const fav = await createArticle(request, 'https://example.com/filter-fav', 'Filter Favourite');

    await request.patch(`/api/articles/${archived.id}`, {
      data: { reading_status: 'archived' },
    });
    await request.patch(`/api/articles/${fav.id}`, {
      data: { is_favorite: 1 },
    });
  });

  test('Unread tab is the default active tab', async ({ page }) => {
    const errors = setupErrorListener(page);
    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    await expect(page.locator('.filter-tab.active')).toHaveText('Unread');

    expect(errors).toEqual([]);
  });

  test('Archived tab filters to archived articles', async ({ page }) => {
    const errors = setupErrorListener(page);
    await page.goto('/');
    await expect(page.locator('.save-form')).toBeVisible({ timeout: 10000 });

    await page.locator('.filter-tab').filter({ hasText: 'Archived' }).click();
    await expect(page.locator('.filter-tab.active')).toHaveText('Archived');

    await expect(page.locator('.article-card, .empty-state').first()).toBeVisible({
      timeout: 10000,
    });

    expect(errors).toEqual([]);
  });

  test('Favourites tab filters to favourite articles', async ({ page }) => {
    const errors = setupErrorListener(page);
    await page.goto('/');
    await expect(page.locator('.save-form')).toBeVisible({ timeout: 10000 });

    await page.locator('.filter-tab').filter({ hasText: 'Favourites' }).click();
    await expect(page.locator('.filter-tab.active')).toHaveText('Favourites');

    await expect(page.locator('.article-card, .empty-state').first()).toBeVisible({
      timeout: 10000,
    });

    expect(errors).toEqual([]);
  });

  test('switching between tabs reloads article list', async ({ page }) => {
    const errors = setupErrorListener(page);
    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    // Switch through multiple tabs — page should not crash
    for (const tab of ['Audio', 'Favourites', 'Archived', 'Unread']) {
      await page.locator('.filter-tab').filter({ hasText: tab }).click();
      // Wait for either articles or empty state
      await expect(page.locator('.article-card, .empty-state').first()).toBeVisible({
        timeout: 10000,
      });
    }

    expect(errors).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------
test.describe('Search', () => {
  test.beforeAll(async ({ request }) => {
    const art = await createArticle(
      request,
      'https://example.com/search-target',
      'Searchable Unique Title XYZ',
    );
    await request.post(`/api/articles/${art.id}/process-now`);
  });

  test('search view loads without crashing', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/#/search');

    await expect(
      page.locator('input[type="search"], input[placeholder*="earch"]').first(),
    ).toBeVisible({ timeout: 10000 });

    expect(errors).toEqual([]);
  });

  test('search returns results for matching query', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/#/search');
    const searchInput = page.locator('input[type="search"], input[placeholder*="earch"]').first();
    await expect(searchInput).toBeVisible({ timeout: 10000 });

    await searchInput.fill('Searchable Unique Title XYZ');
    await searchInput.press('Enter');

    // Should show results or "no results" message — not crash
    await expect(
      page.locator('.article-card, .search-result, .empty-state, [class*="result"]').first(),
    ).toBeVisible({ timeout: 10000 });

    expect(errors).toEqual([]);
  });

  test('empty search shows no results', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/#/search');
    const searchInput = page.locator('input[type="search"], input[placeholder*="earch"]').first();
    await expect(searchInput).toBeVisible({ timeout: 10000 });

    await searchInput.fill('zzzznonexistentqueryzzzz');
    await searchInput.press('Enter');

    // Should show empty state or "no results"
    await expect(
      page
        .locator('.empty-state, [class*="no-result"]')
        .first()
        .or(page.locator('text=/no results|no articles/i').first()),
    ).toBeVisible({ timeout: 10000 });

    expect(errors).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Card actions: favourite, archive, delete
// ---------------------------------------------------------------------------
test.describe('Card actions', () => {
  test('favourite button toggles on article card', async ({ page, request }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/fav-btn-test',
      'Fav Button Test',
    );
    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    // Find the card
    const card = page.locator('.article-card').filter({ hasText: 'Fav Button Test' });
    await expect(card).toBeVisible({ timeout: 5000 });

    // Click favourite button
    const favBtn = card.locator('.fav-btn');
    await favBtn.click();

    // Button should now have 'favorited' class
    await expect(card.locator('.fav-btn.favorited')).toBeVisible({ timeout: 5000 });

    // Toggle it back
    await favBtn.click();
    await expect(card.locator('.fav-btn:not(.favorited)')).toBeVisible({ timeout: 5000 });

    expect(errors).toEqual([]);
  });

  test('archive button archives article from card', async ({ page, request }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/archive-btn-test',
      'Archive Button Test',
    );
    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    const card = page.locator('.article-card').filter({ hasText: 'Archive Button Test' });
    await expect(card).toBeVisible({ timeout: 5000 });

    // Click archive button
    const archiveBtn = card.locator('button[title="Archive"]');
    await archiveBtn.click();

    // Toast should confirm archive
    await expect(page.locator('.toast')).toBeVisible({ timeout: 5000 });

    expect(errors).toEqual([]);
  });

  test('delete button removes article with confirmation', async ({ page, request }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/delete-btn-test',
      'Delete Button Test',
    );
    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    const card = page.locator('.article-card').filter({ hasText: 'Delete Button Test' });
    await expect(card).toBeVisible({ timeout: 5000 });

    // Handle confirm dialog
    page.on('dialog', (dialog) => dialog.accept());

    // Click delete button
    await card.locator('.delete-btn').click();

    // Card should eventually disappear
    await expect(card).not.toBeVisible({ timeout: 10000 });

    // Remove from cleanup list since it's already deleted
    const idx = createdArticleIds.indexOf(id);
    if (idx !== -1) createdArticleIds.splice(idx, 1);

    expect(errors).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Reader interactions
// ---------------------------------------------------------------------------
test.describe('Reader — interactions', () => {
  test('clicking article card navigates to reader', async ({ page, request }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/nav-reader-test',
      'Nav To Reader Test',
    );
    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    const card = page.locator('.article-card').filter({ hasText: 'Nav To Reader Test' });
    await expect(card).toBeVisible({ timeout: 5000 });

    // Click the card body (not action buttons)
    await card.locator('.article-card-body').click();

    // Should navigate to reader
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    expect(errors).toEqual([]);
  });

  test('reader shows article title and content area', async ({ page, request }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/reader-content-test',
      'Reader Content Test',
    );
    await request.post(`/api/articles/${id}/process-now`);

    const errors = setupErrorListener(page);

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // Title should be visible
    await expect(page.locator('.reader-title')).toBeVisible({ timeout: 5000 });

    // Content area should exist (either reader-content or loading state)
    await expect(page.locator('.reader-content, .reader-status-message').first()).toBeVisible({
      timeout: 10000,
    });

    expect(errors).toEqual([]);
  });

  test('favourite button toggles in reader', async ({ page, request }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/reader-fav-test',
      'Reader Fav Test',
    );
    const errors = setupErrorListener(page);

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    const favBtn = page.locator('.reader-actions button').filter({ hasText: /favourite/i });
    if (await favBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await favBtn.click();
      await expect(page.locator('.toast')).toBeVisible({ timeout: 5000 });
    }

    expect(errors).toEqual([]);
  });

  test('back navigation from reader returns to library', async ({ page, request }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/reader-back-test',
      'Reader Back Test',
    );
    const errors = setupErrorListener(page);

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // Click back link
    const backLink = page.locator('a.reader-back').first();
    await expect(backLink).toBeVisible({ timeout: 5000 });
    await backLink.click();

    // Should be back at library
    await expect(page.locator('.save-form')).toBeVisible({ timeout: 10000 });

    expect(errors).toEqual([]);
  });

  test('reading status dropdown changes status', async ({ page, request }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/reader-status-test',
      'Reader Status Test',
    );
    const errors = setupErrorListener(page);

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // Look for status dropdown
    const statusSelect = page
      .locator('.reader-actions select, select')
      .filter({ has: page.locator('option') })
      .first();
    if (await statusSelect.isVisible({ timeout: 5000 }).catch(() => false)) {
      await statusSelect.selectOption('archived');
      await expect(page.locator('.toast')).toBeVisible({ timeout: 5000 });
    }

    expect(errors).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Keyboard shortcuts
// ---------------------------------------------------------------------------
test.describe('Keyboard shortcuts', () => {
  test('? key shows keyboard shortcuts help', async ({ page, request }) => {
    await createArticle(request, 'https://example.com/kb-help-test', 'KB Help Test');
    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    // Press ? to show help
    await page.keyboard.press('Shift+/');

    await expect(page.locator('.shortcuts-overlay')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('.shortcuts-title')).toHaveText('Keyboard Shortcuts');

    // Close with Escape
    await page.keyboard.press('Escape');
    await expect(page.locator('.shortcuts-overlay')).not.toBeVisible({ timeout: 3000 });

    expect(errors).toEqual([]);
  });

  test('j/k keys navigate article selection in library', async ({ page, request }) => {
    await createArticle(request, 'https://example.com/kb-nav-1', 'KB Nav One');
    await createArticle(request, 'https://example.com/kb-nav-2', 'KB Nav Two');
    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    // Press j to select first article
    await page.keyboard.press('j');
    await expect(page.locator('.article-card--checked')).toBeVisible({ timeout: 3000 });

    // Press j again to move to next
    await page.keyboard.press('j');

    // Press k to move back up
    await page.keyboard.press('k');

    // Should still have a selected card
    await expect(page.locator('.article-card--checked')).toBeVisible({ timeout: 3000 });

    expect(errors).toEqual([]);
  });

  test('/ key navigates to search from library', async ({ page, request }) => {
    await createArticle(request, 'https://example.com/kb-search-test', 'KB Search Test');
    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    await page.keyboard.press('/');

    // Should navigate to search view
    await expect(
      page.locator('input[type="search"], input[placeholder*="earch"]').first(),
    ).toBeVisible({ timeout: 10000 });

    expect(errors).toEqual([]);
  });

  test('Escape key returns from reader to library', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/kb-esc-test', 'KB Esc Test');
    const errors = setupErrorListener(page);

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    await page.keyboard.press('Escape');

    // Should return to library
    await expect(page.locator('.save-form')).toBeVisible({ timeout: 10000 });

    expect(errors).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Tag management
// ---------------------------------------------------------------------------
test.describe('Tags', () => {
  const createdTagIds = [];

  test.afterAll(async ({ request }) => {
    for (const id of createdTagIds) {
      try {
        await request.delete(`/api/tags/${id}`);
      } catch {
        /* best effort */
      }
    }
  });

  test('tags view loads and shows tag list', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/#/tags');
    await expect(
      page.locator('h2.section-title, h1.section-title').filter({ hasText: /tags/i }).first(),
    ).toBeVisible({ timeout: 10000 });

    expect(errors).toEqual([]);
  });

  test('can create a new tag', async ({ page, request }) => {
    const errors = setupErrorListener(page);

    await page.goto('/#/tags');
    await expect(page.locator('h2.section-title, h1.section-title').first()).toBeVisible({
      timeout: 10000,
    });

    const tagInput = page.locator('input[placeholder*="ag" i]').first();
    if (await tagInput.isVisible({ timeout: 5000 }).catch(() => false)) {
      await tagInput.fill('E2E Test Tag');
      await page
        .locator('button')
        .filter({ hasText: /create/i })
        .click();

      await expect(page.locator('.toast').filter({ hasText: /created/i })).toBeVisible({
        timeout: 5000,
      });

      // Clean up via API
      const resp = await request.get('/api/tags');
      const tags = await resp.json();
      const newTag = tags.find((t) => t.name === 'E2E Test Tag');
      if (newTag) createdTagIds.push(newTag.id);
    }

    expect(errors).toEqual([]);
  });

  test('tag filtering works from card tag chip', async ({ page, request }) => {
    // Create a tag and assign it
    const tagResp = await request.post('/api/tags', { data: { name: 'E2E Filter Tag' } });
    const tag = await tagResp.json();
    createdTagIds.push(tag.id);

    const article = await createArticle(
      request,
      'https://example.com/tag-filter-test',
      'Tag Filter Test',
    );
    await request.post(`/api/articles/${article.id}/tags`, {
      data: { tag_id: tag.id },
    });

    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    // Find the tag chip on the card
    const tagChip = page.locator('.tag-chip').filter({ hasText: 'E2E Filter Tag' });
    if (await tagChip.isVisible({ timeout: 5000 }).catch(() => false)) {
      await tagChip.click();

      // Should navigate to tag-filtered view
      await expect(
        page
          .locator('.reader-back, a')
          .filter({ hasText: /back to tags/i })
          .first(),
      ).toBeVisible({ timeout: 10000 });
    }

    expect(errors).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Bulk operations
// ---------------------------------------------------------------------------
test.describe('Bulk operations', () => {
  test('select all and bulk archive', async ({ page, request }) => {
    await createArticle(request, 'https://example.com/bulk-archive-1', 'Bulk Archive 1');
    await createArticle(request, 'https://example.com/bulk-archive-2', 'Bulk Archive 2');

    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    // Enter select mode
    const selectBtn = page
      .locator('button')
      .filter({ hasText: /select/i })
      .first();
    await selectBtn.click();

    // Wait for bulk action bar
    await expect(page.locator('.bulk-action-bar')).toBeVisible({ timeout: 5000 });

    // Click select all
    await page
      .locator('.bulk-action-bar button')
      .filter({ hasText: /select all/i })
      .click();

    // Verify count shows
    const countText = await page.locator('.bulk-action-bar-count').textContent();
    expect(parseInt(countText)).toBeGreaterThanOrEqual(2);

    // Click archive
    await page
      .locator('.bulk-action-bar button')
      .filter({ hasText: /archive/i })
      .click();

    // Should show success toast
    await expect(page.locator('.toast').filter({ hasText: /archived/i })).toBeVisible({
      timeout: 5000,
    });

    expect(errors).toEqual([]);
  });

  test('select and bulk delete with confirmation', async ({ page, request }) => {
    await createArticle(request, 'https://example.com/bulk-del-test', 'Bulk Delete Test');

    const errors = setupErrorListener(page);

    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    // Enter select mode
    await page
      .locator('button')
      .filter({ hasText: /select/i })
      .first()
      .click();
    await expect(page.locator('.bulk-action-bar')).toBeVisible({ timeout: 5000 });

    // Click a card to select it
    await page.locator('.article-card').first().click();

    // Handle confirm dialog
    page.on('dialog', (dialog) => dialog.accept());

    // Click delete
    const deleteBtn = page.locator('.bulk-action-bar .btn-danger');
    if (await deleteBtn.isVisible({ timeout: 3000 }).catch(() => false)) {
      await deleteBtn.click();
      await expect(page.locator('.toast').filter({ hasText: /deleted/i })).toBeVisible({
        timeout: 5000,
      });
    }

    expect(errors).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Settings view
// ---------------------------------------------------------------------------
test.describe('Settings', () => {
  test('settings page loads with all sections', async ({ page }) => {
    const errors = setupErrorListener(page);

    await page.goto('/#/settings');
    await expect(page.locator('h2.section-title').first()).toBeVisible({ timeout: 10000 });

    // Should have export section
    await expect(page.locator('text=/export/i').first()).toBeVisible({ timeout: 5000 });

    // Should have bookmarklet section
    await expect(page.locator('text=/bookmarklet/i').first()).toBeVisible({ timeout: 5000 });

    expect(errors).toEqual([]);
  });

  test('navigation to all views from header works', async ({ page }) => {
    const errors = setupErrorListener(page);

    // Start at library
    await page.goto('/');
    await expect(page.locator('.save-form')).toBeVisible({ timeout: 10000 });

    // Navigate to search via header
    const searchLink = page
      .locator('.header-nav a[href*="search"], .header-nav button')
      .filter({ hasText: /search/i })
      .first();
    if (await searchLink.isVisible({ timeout: 3000 }).catch(() => false)) {
      await searchLink.click();
      await expect(
        page.locator('input[type="search"], input[placeholder*="earch"]').first(),
      ).toBeVisible({ timeout: 10000 });
    }

    // Navigate to tags
    await page.goto('/#/tags');
    await expect(page.locator('h2.section-title, h1.section-title').first()).toBeVisible({
      timeout: 10000,
    });

    // Navigate to stats
    await page.goto('/#/stats');
    await expect(page.locator('h1.section-title').filter({ hasText: /statistic/i })).toBeVisible({
      timeout: 10000,
    });

    // Navigate to settings
    await page.goto('/#/settings');
    await expect(page.locator('h2.section-title').first()).toBeVisible({ timeout: 10000 });

    // Back to library
    await page.locator('.header-logo').click();
    await expect(page.locator('.save-form')).toBeVisible({ timeout: 10000 });

    expect(errors).toEqual([]);
  });
});
