// @ts-check
import { test, expect } from '@playwright/test';

/**
 * E2E tests for the offline → online sync round-trip.
 *
 * Tests the full flow: go offline → make changes → go online → verify sync.
 * Requires DISABLE_AUTH=true on the target backend.
 * Run: E2E_BASE_URL=http://localhost:8787 npx playwright test tests/e2e/offline-sync.spec.js
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
  const resp = await request.post('/api/articles', { data: { url, title } });
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();
  createdArticleIds.push(body.id);
  return body;
}

async function processArticle(request, id) {
  const resp = await request.post(`/api/articles/${id}/process-now`);
  expect(resp.ok()).toBeTruthy();
}

// ---------------------------------------------------------------------------
// Offline mutation: favorite toggle queues and syncs
// ---------------------------------------------------------------------------
test.describe('Offline sync round-trip', () => {
  test('favorite toggled offline syncs when back online', async ({ page, context, request }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/offline-fav-' + Date.now(),
      'Offline Fav Test',
    );
    await processArticle(request, id);

    // Navigate to the article reader
    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-title')).toBeVisible({ timeout: 10000 });

    // Confirm article starts as not-favourite
    const favBtn = page.locator('.reader-actions button').filter({ hasText: 'Favourite' });
    await expect(favBtn).toBeVisible({ timeout: 5000 });

    // Go offline
    await context.setOffline(true);

    // Toggle favorite — should queue for sync
    await favBtn.click();

    // Should see "Queued for sync" toast
    await expect(page.locator('.toast').filter({ hasText: 'Queued for sync' })).toBeVisible({
      timeout: 5000,
    });

    // Go back online
    await context.setOffline(false);

    // Wait for "Back online" or "All changes synced" toast
    await expect(page.locator('.toast').filter({ hasText: /synced|Back online/i })).toBeVisible({
      timeout: 15000,
    });

    // Give the sync request time to complete on the server
    await page.waitForTimeout(2000);

    // Verify the favorite actually persisted on the server
    const resp = await request.get(`/api/articles/${id}`);
    expect(resp.ok()).toBeTruthy();
    const article = await resp.json();
    expect(article.is_favorite).toBeTruthy();
  });

  test('archive toggled offline syncs when back online', async ({ page, context, request }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/offline-archive-' + Date.now(),
      'Offline Archive Test',
    );
    await processArticle(request, id);

    // Navigate to library so we can use the card archive button (which has offline queueing)
    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 10000 });

    const card = page.locator('.article-card').filter({ hasText: 'Offline Archive Test' });
    await expect(card).toBeVisible({ timeout: 5000 });

    // Go offline
    await context.setOffline(true);

    // Archive via the card action button (uses optimisticUpdate with offline queue)
    await card.locator('button[title="Archive"]').click();

    // Go back online
    await context.setOffline(false);

    // Wait for sync
    await expect(page.locator('.toast').filter({ hasText: /synced|Back online/i })).toBeVisible({
      timeout: 15000,
    });

    // Give the sync request time to complete on the server
    await page.waitForTimeout(2000);

    // Verify the status persisted on the server
    const resp = await request.get(`/api/articles/${id}`);
    expect(resp.ok()).toBeTruthy();
    const article = await resp.json();
    expect(article.reading_status).toBe('archived');
  });
});

// ---------------------------------------------------------------------------
// Offline indicator UI
// ---------------------------------------------------------------------------
test.describe('Offline indicator', () => {
  test('shows offline bar when network is lost', async ({ page, context }) => {
    await page.goto('/');
    await expect(page.locator('.header')).toBeVisible({ timeout: 10000 });

    // Go offline
    await context.setOffline(true);

    // Should show the offline bar
    await expect(page.locator('.offline-bar')).toBeVisible({ timeout: 5000 });

    // Go back online
    await context.setOffline(false);

    // Offline bar should disappear
    await expect(page.locator('.offline-bar')).not.toBeVisible({ timeout: 5000 });
  });
});
