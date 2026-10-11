// @ts-check
import { test, expect } from '@playwright/test';

/**
 * E2E tests for TTS immersive reading features and settings page.
 *
 * Requires DISABLE_AUTH=true on the target backend.
 * Run: E2E_BASE_URL=http://localhost:6060 npx playwright test tests/e2e/immersive-and-settings.spec.js
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

/**
 * Capability probe for generated audio, decided from the deployment's declared
 * configuration before any TTS behaviour runs, never from the feature failing.
 * Returns a skip reason, or null when TTS must work (and its tests must pass).
 * An operator can declare TTS unavailable, e.g. for `wrangler dev` without
 * Workers AI access, with E2E_TTS_UNAVAILABLE=1.
 */
async function ttsUnavailableReason(request) {
  if (process.env.E2E_TTS_UNAVAILABLE === '1') {
    return 'E2E_TTS_UNAVAILABLE=1: operator declared Workers AI unavailable';
  }
  const resp = await request.get('/api/health/config');
  expect(resp.ok(), `/api/health/config returned ${resp.status()}`).toBeTruthy();
  const { checks } = await resp.json();
  expect(checks, 'health/config lists checks only when auth is bypassed').toBeTruthy();
  const ai = checks.find((check) => check.name === 'AI');
  return ai && ai.status === 'ok' ? null : 'Workers AI binding is not configured on this backend';
}

/** Poll until audio is ready or failed (max 120 s); returns the last status seen. */
async function waitForAudio(request, id) {
  let status = 'unknown';
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    const getResp = await request.get(`/api/articles/${id}`);
    expect(getResp.ok(), `GET /api/articles/${id} returned ${getResp.status()}`).toBeTruthy();
    status = (await getResp.json()).audio_status;
    if (status === 'ready' || status === 'failed') break;
    await new Promise((r) => setTimeout(r, 3000));
  }
  return status;
}

async function _createTag(request, name) {
  const resp = await request.post('/api/tags', { data: { name } });
  expect(resp.ok()).toBeTruthy();
  const body = await resp.json();
  createdTagIds.push(body.id);
  return body;
}

// ---------------------------------------------------------------------------
// 1. TTS Immersive Reading
// ---------------------------------------------------------------------------
test.describe('Immersive Reading', () => {
  test('listen later button triggers audio generation', async ({ page, request }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/immersive-listen',
      'Immersive Listen Test',
    );
    await request.post(`/api/articles/${id}/process-now`);

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // "Listen Later" button should be visible for a processed article without audio
    const listenBtn = page.locator('.reader-actions button').filter({ hasText: 'Listen Later' });
    await expect(listenBtn).toBeVisible({ timeout: 5000 });
    await listenBtn.click();

    // Should show a toast about audio generation being queued
    await expect(page.locator('.toast').first()).toBeVisible({ timeout: 5000 });
  });
});

// Tests that need generated audio. Skipped only when the capability probe says
// this backend cannot generate audio; if it can, a failed listen-later or audio
// that never becomes ready is a test failure (these used to call test.skip()
// in exactly those cases, so broken TTS reported as "skipped").
test.describe('Immersive Reading — generated audio', () => {
  /** @type {string | null} */
  let ttsSkipReason = null;

  test.beforeAll(async ({ request }) => {
    ttsSkipReason = await ttsUnavailableReason(request);
  });

  test.beforeEach(() => {
    test.skip(ttsSkipReason !== null, ttsSkipReason ?? '');
  });

  test('audio-timing endpoint returns valid data after TTS', async ({ request }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/immersive-timing',
      'Timing Test',
    );
    await request.post(`/api/articles/${id}/process-now`);

    const listenResp = await request.post(`/api/articles/${id}/listen-later`);
    expect(listenResp.ok(), `listen-later returned ${listenResp.status()}`).toBeTruthy();
    expect(await waitForAudio(request, id), 'audio_status after up to 120 s').toBe('ready');

    // GET audio-timing manifest
    const timingResp = await request.get(`/api/articles/${id}/audio-timing`);
    expect(timingResp.ok()).toBeTruthy();
    const manifest = await timingResp.json();

    // Verify manifest structure
    expect(manifest).toHaveProperty('version');
    expect(manifest).toHaveProperty('sentences');
    expect(Array.isArray(manifest.sentences)).toBeTruthy();

    if (manifest.sentences.length > 0) {
      const first = manifest.sentences[0];
      expect(first).toHaveProperty('text');
      expect(first).toHaveProperty('start_ms');
      expect(first).toHaveProperty('end_ms');
      expect(typeof first.text).toBe('string');
      expect(typeof first.start_ms).toBe('number');
      expect(typeof first.end_ms).toBe('number');
    }
  });

  test('reader toolbar shows immersive toggle when audio is available', async ({
    page,
    request,
  }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/immersive-toolbar',
      'Immersive Toolbar Test',
    );
    await request.post(`/api/articles/${id}/process-now`);

    // Trigger TTS via API
    const listenResp = await request.post(`/api/articles/${id}/listen-later`);
    expect(listenResp.ok(), `listen-later returned ${listenResp.status()}`).toBeTruthy();
    expect(await waitForAudio(request, id), 'audio_status after up to 120 s').toBe('ready');

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // Click the "Listen" button to start audio playback (which makes audioState.visible true)
    const listenBtn = page.locator('.reader-actions button').filter({ hasText: 'Listen' });
    await expect(listenBtn).toBeVisible({ timeout: 5000 });
    await listenBtn.click();

    // The Immersive segmented control should appear in the reader toolbar
    // It renders as a span.reader-toolbar-label with text "Immersive"
    const immersiveLabel = page.locator('.reader-toolbar-label').filter({ hasText: 'Immersive' });
    await expect(immersiveLabel).toBeVisible({ timeout: 10000 });

    // The segmented control should have Off/On buttons
    const immersiveGroup = immersiveLabel.locator('..').locator('.reader-toolbar-segments');
    await expect(immersiveGroup.locator('button').filter({ hasText: 'Off' })).toBeVisible();
    await expect(immersiveGroup.locator('button').filter({ hasText: 'On' })).toBeVisible();
  });
});

// ---------------------------------------------------------------------------
// 2. Settings — voice preference
// ---------------------------------------------------------------------------
test.describe('Settings — voice preference', () => {
  test('voice picker shows Athena and Orion options', async ({ page }) => {
    await page.goto('/#/settings');
    await expect(page.locator('h2.section-title').first()).toHaveText('Settings', {
      timeout: 10000,
    });

    // "Listen Later Voice" heading should be visible
    await expect(page.getByRole('heading', { name: 'Listen Later Voice' })).toBeVisible({
      timeout: 5000,
    });

    // Athena and Orion buttons
    const voicePicker = page.locator('.voice-picker');
    await expect(voicePicker).toBeVisible({ timeout: 5000 });

    const athenaBtn = voicePicker.locator('button').filter({ hasText: 'Athena' });
    const orionBtn = voicePicker.locator('button').filter({ hasText: 'Orion' });

    await expect(athenaBtn).toBeVisible();
    await expect(orionBtn).toBeVisible();

    // Athena should be selected by default (btn-primary class)
    await expect(athenaBtn).toHaveClass(/btn-primary/);
    await expect(orionBtn).toHaveClass(/btn-secondary/);
  });

  test('changing voice preference persists', async ({ page, request }) => {
    await page.goto('/#/settings');
    await expect(page.locator('h2.section-title').first()).toHaveText('Settings', {
      timeout: 10000,
    });

    const voicePicker = page.locator('.voice-picker');
    await expect(voicePicker).toBeVisible({ timeout: 5000 });

    const athenaBtn = voicePicker.locator('button').filter({ hasText: 'Athena' });
    const orionBtn = voicePicker.locator('button').filter({ hasText: 'Orion' });

    // Click Orion
    await orionBtn.click();
    await expect(orionBtn).toHaveClass(/btn-primary/, { timeout: 5000 });
    await expect(athenaBtn).toHaveClass(/btn-secondary/, { timeout: 5000 });

    // Verify via API
    const prefsResp1 = await request.get('/api/preferences');
    expect(prefsResp1.ok()).toBeTruthy();
    const prefs1 = await prefsResp1.json();
    expect(prefs1.tts_voice).toBe('orion');

    // Click Athena back
    await athenaBtn.click();
    await expect(athenaBtn).toHaveClass(/btn-primary/, { timeout: 5000 });
    await expect(orionBtn).toHaveClass(/btn-secondary/, { timeout: 5000 });

    // Verify via API
    const prefsResp2 = await request.get('/api/preferences');
    expect(prefsResp2.ok()).toBeTruthy();
    const prefs2 = await prefsResp2.json();
    expect(prefs2.tts_voice).toBe('athena');
  });
});

// ---------------------------------------------------------------------------
// 3. Settings — offline reading controls
// ---------------------------------------------------------------------------
test.describe('Settings — offline reading', () => {
  test('offline reading section shows toggle and buttons', async ({ page }) => {
    await page.goto('/#/settings');
    await expect(page.locator('h2.section-title').first()).toHaveText('Settings', {
      timeout: 10000,
    });

    // "Offline Reading" heading
    await expect(page.getByRole('heading', { name: 'Offline Reading' })).toBeVisible({
      timeout: 5000,
    });

    // Auto-cache toggle (role=switch)
    const toggle = page.locator('button[role="switch"]');
    await expect(toggle).toBeVisible({ timeout: 5000 });
    await expect(toggle).toHaveAttribute('aria-label', 'Auto-cache articles for offline');

    // "Cache articles now" button
    const cacheBtn = page.locator('button').filter({ hasText: 'Cache articles now' });
    await expect(cacheBtn).toBeVisible({ timeout: 5000 });

    // "Clear cache & reload" button
    const clearBtn = page.locator('button').filter({ hasText: 'Clear cache & reload' });
    await expect(clearBtn).toBeVisible({ timeout: 5000 });
  });
});

// ---------------------------------------------------------------------------
// 4. Reader — continue reading nudge
// ---------------------------------------------------------------------------
test.describe('Reader — continue reading nudge', () => {
  test('reader displays continue reading nudge for articles with scroll position', async ({
    page,
    request,
  }) => {
    const { id } = await createArticle(
      request,
      'https://example.com/continue-reading-test',
      'Continue Reading Test',
    );
    await request.post(`/api/articles/${id}/process-now`);

    // Set a scroll_position > 0.05 and reading_progress < 0.95 via API
    // so the continue-reading nudge condition is met
    const patchResp = await request.patch(`/api/articles/${id}`, {
      data: { scroll_position: 0.35, reading_progress: 0.35 },
    });
    expect(patchResp.ok()).toBeTruthy();

    await page.goto(`/#/article/${id}`);
    await expect(page.locator('.reader-header')).toBeVisible({ timeout: 10000 });

    // The continue-reading-nudge should be visible
    const nudge = page.locator('.continue-reading-nudge');
    await expect(nudge).toBeVisible({ timeout: 5000 });
    // Verify it shows the percentage and action buttons
    await expect(nudge).toContainText('35%');
    await expect(nudge.locator('button').filter({ hasText: 'Continue reading' })).toBeVisible();
    await expect(nudge.locator('button').filter({ hasText: 'Start from top' })).toBeVisible();
  });
});
