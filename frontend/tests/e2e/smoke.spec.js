// @ts-check
import { test, expect } from '@playwright/test';

/**
 * E2E smoke tests for Tasche.
 *
 * Requires DISABLE_AUTH=true on the target backend so tests can skip OAuth.
 * Run against staging: E2E_BASE_URL=https://tasche-staging.adewale-883.workers.dev npx playwright test
 * Run against local:   E2E_BASE_URL=http://localhost:8787 npx playwright test
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


test.describe('App loading', () => {
  test('app loads and shows library', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.header-logo')).toBeVisible();
    await expect(page.locator('main.main-content')).toBeVisible();
  });

  test('DISABLE_AUTH session works', async ({ request }) => {
    const resp = await request.get('/api/auth/session');
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body.user_id).toBe('dev');
  });

  test('health endpoint responds', async ({ request }) => {
    const resp = await request.get('/api/health');
    expect(resp.ok()).toBeTruthy();
    const body = await resp.json();
    expect(body.status).toBe('ok');
  });
});


test.describe('Article lifecycle', () => {
  test('save article via API and verify it appears in library', async ({ page, request }) => {
    const createResp = await request.post('/api/articles', {
      data: { url: 'https://example.com/smoke-test-article' },
    });
    expect(createResp.ok()).toBeTruthy();
    const { id } = await createResp.json();
    createdArticleIds.push(id);

    // Navigate to library — the article should appear (may be pending/processing)
    await page.goto('/');
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });
  });

  test('save article via the URL input form', async ({ page, request }) => {
    await page.goto('/');
    await expect(page.locator('.save-form')).toBeVisible({ timeout: 10000 });

    const input = page.locator('input[placeholder="Paste a URL to save..."]');
    await input.fill('https://example.com/ui-form-test');
    await page.locator('.save-form .btn-primary').click();

    // Wait for article card to appear (the UI adds it optimistically or after refresh)
    await expect(page.locator('.article-card').first()).toBeVisible({ timeout: 15000 });

    // Clean up: find the article via API (response is a plain array)
    const listResp = await request.get('/api/articles');
    if (listResp.ok()) {
      const articles = await listResp.json();
      const found = articles.find((a) => a.original_url === 'https://example.com/ui-form-test');
      if (found) createdArticleIds.push(found.id);
    }
  });

  test('open article in reader view', async ({ page, request }) => {
    const createResp = await request.post('/api/articles', {
      data: { url: 'https://example.com/reader-test', title: 'Reader Test Article' },
    });
    const { id } = await createResp.json();
    createdArticleIds.push(id);

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('.reader-title')).toBeVisible();
    await expect(page.locator('a.reader-back')).toBeVisible();
  });

  test('delete article via API', async ({ request }) => {
    const createResp = await request.post('/api/articles', {
      data: { url: 'https://example.com/delete-test' },
    });
    const { id } = await createResp.json();

    const deleteResp = await request.delete(`/api/articles/${id}`);
    expect(deleteResp.ok()).toBeTruthy();

    const getResp = await request.get(`/api/articles/${id}`);
    expect(getResp.status()).toBe(404);
  });
});


test.describe('Search', () => {
  test('search page loads', async ({ page }) => {
    await page.goto('/#/search');
    await expect(page.locator('input[type="search"]')).toBeVisible({ timeout: 10000 });
  });

  test('search UI completes without errors', async ({ page }) => {
    await page.goto('/#/search');
    const searchInput = page.locator('input[type="search"]');
    await searchInput.fill('test');
    await page.locator('.search-container .btn-primary').click();

    // Wait for search to complete — either results or empty state
    await expect(
      page.locator('.article-card, .empty-state').first()
    ).toBeVisible({ timeout: 10000 });
  });
});


test.describe('Tags', () => {
  test('tags page loads', async ({ page }) => {
    await page.goto('/#/tags');
    await expect(page.locator('h2.section-title')).toBeVisible({ timeout: 10000 });
  });

  test('create and delete a tag via API', async ({ request }) => {
    const createResp = await request.post('/api/tags', {
      data: { name: `smoke-tag-${Date.now()}` },
    });
    expect(createResp.ok()).toBeTruthy();
    const created = await createResp.json();
    expect(created.id).toBeTruthy();

    // Tags list is a plain array
    const listResp = await request.get('/api/tags');
    const tags = await listResp.json();
    const found = tags.find((t) => t.id === created.id);
    expect(found).toBeTruthy();

    const deleteResp = await request.delete(`/api/tags/${created.id}`);
    expect(deleteResp.ok()).toBeTruthy();
  });

  test('create tag via UI', async ({ page, request }) => {
    await page.goto('/#/tags');
    await expect(page.locator('input[placeholder="New tag name..."]')).toBeVisible({ timeout: 10000 });

    const tagName = `ui-tag-${Date.now()}`;
    await page.locator('input[placeholder="New tag name..."]').fill(tagName);
    await page.locator('.input-group .btn-primary').click();

    // Wait for the tag to appear in the list
    await expect(page.locator('.tag-row-name')).toBeVisible({ timeout: 5000 });

    // Clean up via API (tags list is a plain array)
    const listResp = await request.get('/api/tags');
    if (listResp.ok()) {
      const tags = await listResp.json();
      const found = tags.find((t) => t.name === tagName);
      if (found) createdTagIds.push(found.id);
    }
  });

  test('assign tag to article via API', async ({ request }) => {
    const articleResp = await request.post('/api/articles', {
      data: { url: 'https://example.com/tag-assign-test' },
    });
    const { id: articleId } = await articleResp.json();
    createdArticleIds.push(articleId);

    const tagResp = await request.post('/api/tags', {
      data: { name: `assign-tag-${Date.now()}` },
    });
    const { id: tagId } = await tagResp.json();
    createdTagIds.push(tagId);

    const assignResp = await request.post(`/api/articles/${articleId}/tags`, {
      data: { tag_id: tagId },
    });
    expect(assignResp.ok()).toBeTruthy();

    // Article tags list is a plain array
    const tagsResp = await request.get(`/api/articles/${articleId}/tags`);
    const articleTags = await tagsResp.json();
    expect(articleTags.some((t) => t.id === tagId)).toBeTruthy();
  });
});


test.describe('Settings', () => {
  test('settings page loads', async ({ page }) => {
    await page.goto('/#/settings');
    await expect(page.locator('h2.section-title')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('h2.section-title')).toHaveText('Settings');
  });
});


test.describe('Navigation', () => {
  test('header navigation links work', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('.header-logo')).toBeVisible({ timeout: 10000 });

    await page.locator('a[href="#/search"]').click();
    await expect(page.locator('input[type="search"]')).toBeVisible({ timeout: 5000 });

    await page.locator('a[href="#/tags"]').click();
    await expect(page.locator('input[placeholder="New tag name..."]')).toBeVisible({ timeout: 5000 });

    await page.locator('a[href="#/settings"]').click();
    await expect(page.locator('h2.section-title')).toHaveText('Settings', { timeout: 5000 });

    await page.locator('.header-logo').click();
    await expect(page.locator('.save-form')).toBeVisible({ timeout: 5000 });
  });
});
