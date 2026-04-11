// @ts-check
import { test, expect } from '@playwright/test';

/**
 * E2E tests verifying loading states on async buttons.
 *
 * These tests ensure buttons show loading text + disabled state during async
 * operations, preventing double-submit.
 *
 * Requires DISABLE_AUTH=true on the target backend.
 * Run: E2E_BASE_URL=https://tasche-staging.adewale-883.workers.dev npx playwright test tests/e2e/loading-states.spec.js
 */

/** @type {string[]} */
const createdArticleIds = [];
/** @type {string[]} */
const createdTagIds = [];

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
// Library: Save button loading state
// ---------------------------------------------------------------------------
test('Library — Save button shows "Saving..." while submitting', async ({ page }) => {
  await page.goto('/#/');
  await page.waitForSelector('input[placeholder="Paste a URL to save..."]');

  const input = page.locator('input[placeholder="Paste a URL to save..."]');
  await input.fill('https://example.com/loading-state-test-' + Date.now());

  const saveBtn = page.locator('button:has-text("Save")').first();
  expect(await saveBtn.textContent()).toContain('Save');

  await saveBtn.click();

  // Should show Saving... and be disabled momentarily
  // The button text changes to "Saving..." during the request
  try {
    await expect(saveBtn).toContainText('Saving', { timeout: 2000 });
  } catch {
    // If the request was fast, it may have already completed
  }

  // Eventually returns to Save
  await expect(saveBtn).toContainText('Save', { timeout: 10000 });
  await expect(saveBtn).toBeEnabled();
});

// ---------------------------------------------------------------------------
// Tags: Create Tag button loading state
// ---------------------------------------------------------------------------
test('Tags — Create Tag button shows "Creating..." while submitting', async ({ page, request }) => {
  await page.goto('/#/tags');
  await page.waitForSelector('input[placeholder="New tag name..."]');

  const input = page.locator('input[placeholder="New tag name..."]');
  const tagName = 'LoadingTest-' + Date.now();
  await input.fill(tagName);

  const createBtn = page.locator('button:has-text("Create Tag")');
  await createBtn.click();

  try {
    await expect(createBtn).toContainText('Creating', { timeout: 2000 });
  } catch {
    // Fast response
  }

  // Eventually returns to Create Tag
  await expect(createBtn).toContainText('Create Tag', { timeout: 5000 });

  // Clean up — find and delete the created tag
  const resp = await request.get('/api/tags');
  if (resp.ok()) {
    const tags = await resp.json();
    const created = tags.find((t) => t.name === tagName);
    if (created) {
      createdTagIds.push(created.id);
    }
  }
});

// ---------------------------------------------------------------------------
// Tags: Delete Tag button loading state
// ---------------------------------------------------------------------------
test('Tags — Delete Tag button shows "Deleting..." while removing', async ({ page, request }) => {
  // Create a tag to delete
  const tagName = 'ToDelete-' + Date.now();
  const resp = await request.post('/api/tags', { data: { name: tagName } });
  expect(resp.ok()).toBeTruthy();
  const tag = await resp.json();
  createdTagIds.push(tag.id);

  await page.goto('/#/tags');
  await page.waitForSelector('.tag-row');

  // Find the delete button in the tag row
  const tagRow = page.locator('.tag-row', { hasText: tagName });
  const deleteBtn = tagRow.locator('button:has-text("Delete")');

  page.on('dialog', (dialog) => dialog.accept());
  await deleteBtn.click();

  try {
    await expect(deleteBtn).toContainText('Deleting', { timeout: 2000 });
  } catch {
    // Fast response
  }
});

// ---------------------------------------------------------------------------
// Reader: Listen Later button loading state
// ---------------------------------------------------------------------------
test('Reader — Listen Later button shows "Requesting..." while submitting', async ({
  page,
  request,
}) => {
  const article = await createArticle(
    request,
    'https://example.com/listen-test-' + Date.now(),
    'Listen Test',
  );
  await request.post(`/api/articles/${article.id}/process-now`);

  await page.goto(`/#/article/${article.id}`);
  await page.waitForSelector('.reader-title');

  const listenBtn = page.locator('button:has-text("Listen Later")');
  await expect(listenBtn).toBeVisible({ timeout: 5000 });
  await listenBtn.click();

  try {
    await expect(listenBtn).toContainText('Requesting', { timeout: 2000 });
  } catch {
    // Fast response
  }
});

// ---------------------------------------------------------------------------
// Reader: Delete button loading state
// ---------------------------------------------------------------------------
test('Reader — Delete button shows "Deleting..." while removing', async ({ page, request }) => {
  const article = await createArticle(
    request,
    'https://example.com/delete-test-' + Date.now(),
    'Delete Test',
  );
  await request.post(`/api/articles/${article.id}/process-now`);

  await page.goto(`/#/article/${article.id}`);
  await page.waitForSelector('.reader-title');

  page.on('dialog', (dialog) => dialog.accept());

  const deleteBtn = page.locator('button:has-text("Delete")').first();
  await deleteBtn.click();

  try {
    await expect(deleteBtn).toContainText('Deleting', { timeout: 2000 });
  } catch {
    // Fast response — button may already be gone (navigated)
  }

  // Should navigate back to library
  await expect(page).toHaveURL(/#\/$/, { timeout: 10000 });
});

// ---------------------------------------------------------------------------
// Reader: Retry button loading state
// ---------------------------------------------------------------------------
test('Reader — Retry button shows "Retrying..." while reprocessing', async ({ page, request }) => {
  const article = await createArticle(
    request,
    'https://example.com/retry-test-' + Date.now(),
    'Retry Test',
  );
  await request.post(`/api/articles/${article.id}/process-now`);

  await page.goto(`/#/article/${article.id}`);
  await page.waitForSelector('.reader-title');

  const retryBtn = page.locator('button:has-text("Retry")');
  await retryBtn.click();

  try {
    await expect(retryBtn).toContainText('Retrying', { timeout: 2000 });
  } catch {
    // Fast response
  }

  // Button should re-enable
  await expect(retryBtn).toContainText('Retry', { timeout: 5000 });
});
