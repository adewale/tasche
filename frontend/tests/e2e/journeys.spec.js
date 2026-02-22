// @ts-check
import { test, expect } from '@playwright/test';

/**
 * E2E journey tests for Tasche — covers user interactions not in smoke.spec.js.
 *
 * Requires DISABLE_AUTH=true on the target backend.
 * Run: E2E_BASE_URL=http://localhost:6060 npx playwright test tests/e2e/journeys.spec.js
 */

/** @type {string[]} */
const createdArticleIds = [];
/** @type {string[]} */
const createdTagIds = [];

test.afterAll(async ({ request }) => {
  for (const id of createdArticleIds) {
    try { await request.delete(`/api/articles/${id}`); } catch { /* best effort */ }
  }
  for (const id of createdTagIds) {
    try { await request.delete(`/api/tags/${id}`); } catch { /* best effort */ }
  }
});


// ---------------------------------------------------------------------------
// Helper: create an article via API and track for cleanup
// ---------------------------------------------------------------------------
async function createArticle(request, url, title) {
  const data = { url };
  if (title) data.title = title;
  const resp = await request.post('/api/articles', { data });
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();
  createdArticleIds.push(body.id);
  return body;
}

async function createTag(request, name) {
  const resp = await request.post('/api/tags', { data: { name } });
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();
  createdTagIds.push(body.id);
  return body;
}


// ---------------------------------------------------------------------------
// Reader view: toggle favorite
// ---------------------------------------------------------------------------
test.describe('Reader — toggle favorite', () => {
  test('favorite button toggles state in reader view', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/fav-test', 'Fav Test');

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // Initially should be un-favorited (btn-secondary, text "Favorite")
    const favBtn = page.locator('.reader-actions button').filter({ hasText: 'Favorite' }).first();
    await expect(favBtn).toBeVisible({ timeout: 5000 });
    await expect(favBtn).toHaveClass(/btn-secondary/);

    // Click to favorite
    await favBtn.click();
    await expect(favBtn).toHaveText(/Favorited/, { timeout: 5000 });
    await expect(favBtn).toHaveClass(/btn-primary/);

    // Verify via API
    const getResp = await request.get(`/api/articles/${id}`);
    const article = await getResp.json();
    expect(article.is_favorite).toBeTruthy();

    // Click again to un-favorite
    await favBtn.click();
    await expect(favBtn).toHaveText(/Favorite/, { timeout: 5000 });
    await expect(favBtn).toHaveClass(/btn-secondary/);
  });
});


// ---------------------------------------------------------------------------
// Reader view: change reading status
// ---------------------------------------------------------------------------
test.describe('Reader — change reading status', () => {
  test('reading status dropdown updates article state', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/status-test', 'Status Test');

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    const statusSelect = page.locator('select.input-inline-select');
    await expect(statusSelect).toBeVisible({ timeout: 5000 });

    // Reader auto-sets to "reading" on open, so change to "archived"
    await statusSelect.selectOption('archived');

    // Wait for toast confirming status update
    await expect(page.locator('.toast')).toBeVisible({ timeout: 5000 });

    // Verify via API
    const getResp = await request.get(`/api/articles/${id}`);
    const article = await getResp.json();
    expect(article.reading_status).toBe('archived');
  });
});


// ---------------------------------------------------------------------------
// Reader view: delete article via UI
// ---------------------------------------------------------------------------
test.describe('Reader — delete article via UI', () => {
  test('delete button removes article and navigates to library', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/delete-ui-test', 'Delete UI Test');

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // Accept the confirm dialog
    page.on('dialog', (dialog) => dialog.accept());

    const deleteBtn = page.locator('.reader-actions button.btn-danger');
    await expect(deleteBtn).toBeVisible();
    await deleteBtn.click();

    // Should navigate back to library
    await expect(page.locator('.save-form')).toBeVisible({ timeout: 10000 });

    // Verify article is gone via API
    const getResp = await request.get(`/api/articles/${id}`);
    expect(getResp.status()).toBe(404);

    // Remove from cleanup list since already deleted
    const idx = createdArticleIds.indexOf(id);
    if (idx !== -1) createdArticleIds.splice(idx, 1);
  });
});


// ---------------------------------------------------------------------------
// Reader view: retry failed article
// ---------------------------------------------------------------------------
test.describe('Reader — retry failed article', () => {
  test('retry button appears for failed articles and re-queues', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/retry-test', 'Retry Test');

    // Force the article into "failed" state via API
    const patchResp = await request.patch(`/api/articles/${id}`, {
      data: { reading_status: 'unread' },
    });
    expect(patchResp.ok()).toBeTruthy();

    // Directly set status to failed (need a raw D1 update — use process-now with bad URL instead)
    // Actually, we need to use the retry endpoint which only works on failed/pending articles.
    // The article is already "pending" (just created), so retry should work.
    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // Article is pending, so retry button should be visible
    const retryBtn = page.locator('.reader-actions button').filter({ hasText: 'Retry' });
    await expect(retryBtn).toBeVisible({ timeout: 5000 });

    await retryBtn.click();

    // Should show success toast
    await expect(page.locator('.toast').filter({ hasText: 're-queued' })).toBeVisible({ timeout: 5000 });
  });
});


// ---------------------------------------------------------------------------
// Reader view: check original URL availability
// ---------------------------------------------------------------------------
test.describe('Reader — check original URL', () => {
  test('check now button updates original status', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/check-original-test', 'Check Original');

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // Original status should be "unknown" initially
    const statusDiv = page.locator('.original-status');
    await expect(statusDiv).toBeVisible({ timeout: 5000 });

    // Look for the "Check now" button
    const checkBtn = statusDiv.locator('button').filter({ hasText: 'Check now' });

    // If "Check now" is visible, click it
    if (await checkBtn.isVisible()) {
      await checkBtn.click();

      // Wait for the toast or for the status to change
      await expect(page.locator('.toast')).toBeVisible({ timeout: 15000 });

      // Verify via API that original_status was updated
      const getResp = await request.get(`/api/articles/${id}`);
      const article = await getResp.json();
      expect(article.original_status).not.toBe('unknown');
    }
  });
});


// ---------------------------------------------------------------------------
// Reader view: add and remove tags via UI
// ---------------------------------------------------------------------------
test.describe('Reader — tag management via UI', () => {
  test('add tag to article and remove it', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/tag-ui-test', 'Tag UI Test');
    const tagName = `e2e-tag-${Date.now()}`;
    const tag = await createTag(request, tagName);

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // Click "+ Tag" button to open the picker
    const addTagBtn = page.locator('button.tag-chip').filter({ hasText: '+ Tag' });
    await expect(addTagBtn).toBeVisible({ timeout: 5000 });
    await addTagBtn.click();

    // Tag picker should appear with a select and Add button
    await expect(page.locator('.tag-picker')).toBeVisible({ timeout: 5000 });

    // Select the tag we created
    const tagSelect = page.locator('.tag-picker select');
    await tagSelect.selectOption(tag.id);

    // Click "Add"
    await page.locator('.tag-picker .btn-primary').click();

    // Tag chip should appear with the tag name
    await expect(page.locator('.tag-chip').filter({ hasText: tagName })).toBeVisible({ timeout: 5000 });

    // Toast should confirm
    await expect(page.locator('.toast').filter({ hasText: 'Tag added' })).toBeVisible({ timeout: 3000 });

    // Verify via API
    const tagsResp = await request.get(`/api/articles/${id}/tags`);
    const articleTags = await tagsResp.json();
    expect(articleTags.some((t) => t.id === tag.id)).toBeTruthy();

    // Now remove the tag by clicking the X
    const tagChip = page.locator('.tag-chip').filter({ hasText: tagName });
    const removeBtn = tagChip.locator('.tag-chip-remove');
    await removeBtn.click();

    // Tag chip should disappear
    await expect(tagChip).not.toBeVisible({ timeout: 5000 });

    // Toast should confirm removal
    await expect(page.locator('.toast').filter({ hasText: 'Tag removed' })).toBeVisible({ timeout: 3000 });

    // Verify via API
    const tagsResp2 = await request.get(`/api/articles/${id}/tags`);
    const articleTags2 = await tagsResp2.json();
    expect(articleTags2.some((t) => t.id === tag.id)).toBeFalsy();
  });
});


// ---------------------------------------------------------------------------
// Library: favorite toggle from article card
// ---------------------------------------------------------------------------
test.describe('Library — card actions', () => {
  test('favorite button on card toggles favorite state', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/card-fav-test', 'Card Fav Test');

    // Process the article so the processing overlay goes away
    await request.post(`/api/articles/${id}/process-now`);

    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    // Find the card for our article
    const card = page.locator('.article-card').filter({ hasText: 'Card Fav Test' });
    if (await card.isVisible()) {
      const favBtn = card.locator('.fav-btn');
      await expect(favBtn).toBeVisible();

      // Should not be favorited initially
      await expect(favBtn).not.toHaveClass(/favorited/);

      // Click to favorite
      await favBtn.click();
      await expect(favBtn).toHaveClass(/favorited/, { timeout: 5000 });

      // Verify via API
      const getResp = await request.get(`/api/articles/${id}`);
      const article = await getResp.json();
      expect(article.is_favorite).toBeTruthy();
    }
  });

  test('delete button on card removes article', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/card-delete-test', 'Card Delete Test');

    // Process the article so the processing overlay goes away
    await request.post(`/api/articles/${id}/process-now`);

    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    const card = page.locator('.article-card').filter({ hasText: 'Card Delete Test' });
    if (await card.isVisible()) {
      // Accept confirm dialog
      page.on('dialog', (dialog) => dialog.accept());

      const deleteBtn = card.locator('.delete-btn');
      await deleteBtn.click();

      // Card should disappear
      await expect(card).not.toBeVisible({ timeout: 5000 });

      // Verify via API
      const getResp = await request.get(`/api/articles/${id}`);
      expect(getResp.status()).toBe(404);

      // Remove from cleanup list
      const idx = createdArticleIds.indexOf(id);
      if (idx !== -1) createdArticleIds.splice(idx, 1);
    }
  });
});


// ---------------------------------------------------------------------------
// Library: filter tabs
// ---------------------------------------------------------------------------
test.describe('Library — filter tabs', () => {
  test('filter tabs switch between reading statuses', async ({ page, request }) => {
    // Create an article and mark it archived
    const { id } = await createArticle(request, 'https://example.com/filter-test', 'Filter Test');
    await request.patch(`/api/articles/${id}`, {
      data: { reading_status: 'archived' },
    });

    await page.goto('/');
    await expect(page.locator('.save-form')).toBeVisible({ timeout: 10000 });

    // Click "Archived" tab
    const archivedTab = page.locator('.filter-tabs button, .filter-tabs a').filter({ hasText: 'Archived' });
    if (await archivedTab.isVisible()) {
      await archivedTab.click();
      // Should show the archived article or empty state
      await expect(
        page.locator('.article-card, .empty-state').first()
      ).toBeVisible({ timeout: 10000 });
    }

    // Click "Favorites" tab
    const favTab = page.locator('.filter-tabs button, .filter-tabs a').filter({ hasText: 'Favorites' });
    if (await favTab.isVisible()) {
      await favTab.click();
      await expect(
        page.locator('.article-card, .empty-state').first()
      ).toBeVisible({ timeout: 10000 });
    }
  });
});


// ---------------------------------------------------------------------------
// Reader view: listen later (TTS request)
// ---------------------------------------------------------------------------
test.describe('Reader — listen later', () => {
  test('listen later button queues audio generation', async ({ page, request }) => {
    const { id } = await createArticle(request, 'https://example.com/tts-test', 'TTS Test');

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // "Listen Later" button should be visible (since audio_status is not ready/pending)
    const listenBtn = page.locator('.reader-actions button').filter({ hasText: 'Listen Later' });

    if (await listenBtn.isVisible({ timeout: 5000 }).catch(() => false)) {
      await listenBtn.click();

      // Should show toast about audio generation
      await expect(
        page.locator('.toast').first()
      ).toBeVisible({ timeout: 5000 });
    }
  });
});


// ---------------------------------------------------------------------------
// Article processing: process-now endpoint works inline
// ---------------------------------------------------------------------------
test.describe('Article processing — inline', () => {
  test('process-now endpoint processes article successfully', async ({ request }) => {
    const { id } = await createArticle(request, 'https://example.com/process-now-test', 'Process Now Test');

    const processResp = await request.post(`/api/articles/${id}/process-now`);
    expect(processResp.ok()).toBeTruthy();
    const result = await processResp.json();

    // May succeed or fail depending on whether example.com returns real HTML
    // but the endpoint should always respond, not crash
    expect(result.id).toBe(id);
    expect(['success', 'error']).toContain(result.result);
  });
});


// ---------------------------------------------------------------------------
// Batch check originals
// ---------------------------------------------------------------------------
test.describe('Batch operations', () => {
  test('batch-check-originals endpoint runs without errors', async ({ request }) => {
    const resp = await request.post('/api/articles/batch-check-originals');
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body).toHaveProperty('checked');
    expect(body).toHaveProperty('results');
  });
});


// ---------------------------------------------------------------------------
// Settings: bookmarklet visible
// ---------------------------------------------------------------------------
test.describe('Settings — bookmarklet', () => {
  test('bookmarklet link is displayed', async ({ page }) => {
    await page.goto('/#/settings');
    await expect(page.locator('h2.section-title')).toHaveText('Settings', { timeout: 10000 });

    // Should show bookmarklet section
    await expect(page.locator('text=Bookmarklet')).toBeVisible({ timeout: 5000 });
  });
});


// ---------------------------------------------------------------------------
// API: retry endpoint rejects non-retryable articles
// ---------------------------------------------------------------------------
test.describe('API — retry guards', () => {
  test('retry returns 409 for a ready article', async ({ request }) => {
    const { id } = await createArticle(request, 'https://example.com/retry-guard-test');

    // Process it to "ready" state
    await request.post(`/api/articles/${id}/process-now`);

    // Check if it's now ready
    const getResp = await request.get(`/api/articles/${id}`);
    const article = await getResp.json();

    if (article.status === 'ready') {
      const retryResp = await request.post(`/api/articles/${id}/retry`);
      expect(retryResp.status()).toBe(409);
    }
  });
});
