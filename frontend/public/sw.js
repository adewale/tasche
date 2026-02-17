/**
 * Tasche Service Worker
 *
 * Handles:
 * - App shell caching (index.html)
 * - API response caching for GET requests
 * - Offline fallback with cached content
 * - Background sync for offline mutations
 * - Explicit "Save for offline" article + audio caching
 * - LRU cache eviction for offline content
 */

const CACHE_NAME = 'tasche-v1';
const STATIC_CACHE = 'tasche-static-v1';
const API_CACHE = 'tasche-api-v1';
const OFFLINE_CACHE = 'tasche-offline-v1';
const OFFLINE_META_KEY = 'tasche-offline-meta';

const APP_SHELL = [
  '/',
  '/manifest.json',
];

const SYNC_QUEUE_KEY = 'tasche-sync-queue';

// Maximum number of articles in the offline cache before LRU eviction
const MAX_OFFLINE_ARTICLES = 100;

// ---------------------------------------------------------------------------
// Install — cache app shell
// ---------------------------------------------------------------------------

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then((cache) => {
      return cache.addAll(APP_SHELL);
    })
  );
  self.skipWaiting();
});

// ---------------------------------------------------------------------------
// Activate — clean old caches, preserve sync queue + offline cache
// ---------------------------------------------------------------------------

self.addEventListener('activate', (event) => {
  const KEEP = [STATIC_CACHE, API_CACHE, CACHE_NAME, OFFLINE_CACHE];
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys
          .filter((key) => !KEEP.includes(key))
          .map((key) => caches.delete(key))
      );
    })
  );
  self.clients.claim();
});

// ---------------------------------------------------------------------------
// Fetch — serve from cache, fall back to network
// ---------------------------------------------------------------------------

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // Static assets — cache first
  if (APP_SHELL.includes(url.pathname) || url.pathname.startsWith('/static/') || url.pathname.startsWith('/assets/')) {
    event.respondWith(cacheFirst(event.request, STATIC_CACHE));
    return;
  }

  // API GET requests — network first, then check offline cache, then API cache
  if (url.pathname.startsWith('/api/') && event.request.method === 'GET') {
    event.respondWith(networkFirstWithOffline(event.request));
    return;
  }

  // Everything else — network only
  event.respondWith(fetch(event.request));
});

async function cacheFirst(request, cacheName) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    // Return a basic offline page if we can't fetch
    const fallback = await caches.match('/');
    if (fallback) return fallback;
    return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
  }
}

/**
 * Network first, then check the offline cache (explicit saves), then the API cache.
 * This ensures explicitly saved offline content is served even when the API cache
 * entry has been evicted.
 */
async function networkFirstWithOffline(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(API_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    // Try offline cache first (explicit saves)
    const offlineCached = await caches.open(OFFLINE_CACHE).then((c) => c.match(request));
    if (offlineCached) {
      // Update access time for LRU eviction (fire-and-forget)
      updateAccessTime(request.url);
      return offlineCached;
    }

    // Then try API cache (automatic caching)
    const cached = await caches.match(request);
    if (cached) return cached;

    return new Response(
      JSON.stringify({ error: 'Offline' }),
      { status: 503, headers: { 'Content-Type': 'application/json' } }
    );
  }
}

// ---------------------------------------------------------------------------
// Background Sync — replay queued mutations when online
// ---------------------------------------------------------------------------

self.addEventListener('sync', (event) => {
  if (event.tag === 'tasche-sync') {
    event.waitUntil(replayQueue());
  }
});

async function replayQueue() {
  const queue = await getQueue();
  if (queue.length === 0) return;

  const remaining = [];

  // Notify clients that sync is starting
  notifyClients({ type: 'SYNC_STATUS', status: 'syncing' });

  for (const item of queue) {
    try {
      const response = await fetch(item.url, {
        method: item.method,
        headers: item.headers,
        body: item.body,
        credentials: 'include',
      });
      if (!response.ok && response.status >= 500) {
        // Server error — keep in queue for retry
        remaining.push(item);
      }
    } catch (err) {
      // Network error — keep in queue
      remaining.push(item);
    }
  }

  await saveQueue(remaining);

  // Notify clients about sync result
  if (remaining.length === 0) {
    notifyClients({ type: 'SYNC_STATUS', status: 'synced' });
  } else {
    notifyClients({ type: 'SYNC_STATUS', status: 'error' });
  }
}

async function getQueue() {
  try {
    const cache = await caches.open(CACHE_NAME);
    const response = await cache.match(SYNC_QUEUE_KEY);
    if (!response) return [];
    return await response.json();
  } catch {
    return [];
  }
}

async function saveQueue(queue) {
  const cache = await caches.open(CACHE_NAME);
  await cache.put(
    SYNC_QUEUE_KEY,
    new Response(JSON.stringify(queue), {
      headers: { 'Content-Type': 'application/json' },
    })
  );
}

// ---------------------------------------------------------------------------
// Offline Metadata — track cached articles for LRU eviction
// ---------------------------------------------------------------------------

async function getOfflineMeta() {
  try {
    const cache = await caches.open(OFFLINE_CACHE);
    const resp = await cache.match(OFFLINE_META_KEY);
    if (!resp) return {};
    return await resp.json();
  } catch {
    return {};
  }
}

async function saveOfflineMeta(meta) {
  const cache = await caches.open(OFFLINE_CACHE);
  await cache.put(
    OFFLINE_META_KEY,
    new Response(JSON.stringify(meta), {
      headers: { 'Content-Type': 'application/json' },
    })
  );
}

/**
 * Update access time for an article when served from offline cache (true LRU).
 */
function updateAccessTime(url) {
  // Extract article ID from URL like /api/articles/{id} or /api/articles/{id}/content
  const match = url.match(/\/api\/articles\/([^/]+)/);
  if (!match) return;
  const articleId = match[1];

  getOfflineMeta().then(function (meta) {
    if (meta[articleId]) {
      meta[articleId].accessedAt = Date.now();
      saveOfflineMeta(meta);
    }
  }).catch(function () {});
}

/**
 * Evict the least recently used articles if the count exceeds MAX_OFFLINE_ARTICLES.
 * Each entry in meta is: { [articleId]: { accessedAt, hasContent, hasAudio } }
 */
async function evictIfNeeded(meta) {
  const ids = Object.keys(meta);
  if (ids.length <= MAX_OFFLINE_ARTICLES) return meta;

  // Sort by accessedAt ascending (oldest first)
  ids.sort((a, b) => (meta[a].accessedAt || 0) - (meta[b].accessedAt || 0));

  const cache = await caches.open(OFFLINE_CACHE);
  const toEvict = ids.slice(0, ids.length - MAX_OFFLINE_ARTICLES);

  for (const id of toEvict) {
    // Delete all cached URLs for this article
    await cache.delete('/api/articles/' + id);
    await cache.delete('/api/articles/' + id + '/content');
    await cache.delete('/api/articles/' + id + '/audio');
    delete meta[id];
  }

  return meta;
}

// ---------------------------------------------------------------------------
// Notify all clients
// ---------------------------------------------------------------------------

async function notifyClients(message) {
  const clients = await self.clients.matchAll({ type: 'window' });
  for (const client of clients) {
    client.postMessage(message);
  }
}

// ---------------------------------------------------------------------------
// Message handler — receive commands from main thread
// ---------------------------------------------------------------------------

self.addEventListener('message', (event) => {
  if (!event.data || !event.data.type) return;

  switch (event.data.type) {
    case 'QUEUE_REQUEST':
      event.waitUntil(handleQueueRequest(event));
      break;

    case 'CACHE_ARTICLES':
      event.waitUntil(handleCacheArticles(event));
      break;

    case 'SAVE_FOR_OFFLINE':
      event.waitUntil(handleSaveForOffline(event));
      break;

    case 'SAVE_AUDIO_OFFLINE':
      event.waitUntil(handleSaveAudioOffline(event));
      break;

    case 'CHECK_OFFLINE_STATUS':
      event.waitUntil(handleCheckOfflineStatus(event));
      break;

    case 'SKIP_WAITING':
      self.skipWaiting();
      break;

    case 'REPLAY_QUEUE':
      event.waitUntil(replayQueue());
      break;
  }
});

async function handleQueueRequest(event) {
  const queue = await getQueue();
  const newReq = event.data.request;
  // Deduplicate: if a request with the same URL and method exists, replace it
  const existingIndex = queue.findIndex(
    (item) => item.url === newReq.url && item.method === newReq.method
  );
  if (existingIndex !== -1) {
    queue[existingIndex] = newReq;
  } else {
    queue.push(newReq);
  }
  return saveQueue(queue);
}

async function handleCacheArticles(event) {
  // Pre-cache article detail endpoints for offline reading
  const articleIds = event.data.articleIds || [];
  const cache = await caches.open(API_CACHE);
  for (const id of articleIds) {
    const url = '/api/articles/' + id;
    try {
      const cached = await cache.match(url);
      if (!cached) {
        const resp = await fetch(url, { credentials: 'include' });
        if (resp.ok) cache.put(url, resp);
      }
    } catch (e) {
      // ignore fetch errors during prefetch
    }
  }
}

async function handleSaveForOffline(event) {
  const articleId = event.data.articleId;
  if (!articleId) return;

  const cache = await caches.open(OFFLINE_CACHE);
  const meta = await getOfflineMeta();

  const detailUrl = '/api/articles/' + articleId;
  const contentUrl = '/api/articles/' + articleId + '/content';

  try {
    // Fetch and cache article detail JSON
    const detailResp = await fetch(detailUrl, { credentials: 'include' });
    if (detailResp.ok) {
      await cache.put(detailUrl, detailResp.clone());
    }

    // Fetch and cache article content HTML
    const contentResp = await fetch(contentUrl, { credentials: 'include' });
    if (contentResp.ok) {
      await cache.put(contentUrl, contentResp.clone());
    }

    // Only mark as cached if both responses were OK
    if (!detailResp.ok || !contentResp.ok) {
      throw new Error('Failed to cache: detail=' + detailResp.status + ' content=' + contentResp.status);
    }

    // Update metadata
    const existing = meta[articleId] || {};
    meta[articleId] = {
      ...existing,
      hasContent: true,
      accessedAt: Date.now(),
    };

    // Evict old entries if needed
    const cleaned = await evictIfNeeded(meta);
    await saveOfflineMeta(cleaned);

    // Notify the requesting client
    if (event.source) {
      event.source.postMessage({
        type: 'OFFLINE_SAVED',
        articleId: articleId,
        what: 'content',
      });
    }
  } catch (err) {
    if (event.source) {
      event.source.postMessage({
        type: 'OFFLINE_SAVE_ERROR',
        articleId: articleId,
        what: 'content',
        error: err.message,
      });
    }
  }
}

async function handleSaveAudioOffline(event) {
  const articleId = event.data.articleId;
  if (!articleId) return;

  const cache = await caches.open(OFFLINE_CACHE);
  const meta = await getOfflineMeta();

  const audioUrl = '/api/articles/' + articleId + '/audio';

  try {
    const audioResp = await fetch(audioUrl, { credentials: 'include' });
    if (audioResp.ok) {
      await cache.put(audioUrl, audioResp.clone());
    } else {
      throw new Error('Failed to fetch audio: ' + audioResp.status);
    }

    // Update metadata
    const existing = meta[articleId] || {};
    meta[articleId] = {
      ...existing,
      hasAudio: true,
      accessedAt: Date.now(),
    };

    const cleaned = await evictIfNeeded(meta);
    await saveOfflineMeta(cleaned);

    if (event.source) {
      event.source.postMessage({
        type: 'OFFLINE_SAVED',
        articleId: articleId,
        what: 'audio',
      });
    }
  } catch (err) {
    if (event.source) {
      event.source.postMessage({
        type: 'OFFLINE_SAVE_ERROR',
        articleId: articleId,
        what: 'audio',
        error: err.message,
      });
    }
  }
}

async function handleCheckOfflineStatus(event) {
  const articleId = event.data.articleId;
  if (!articleId) return;

  const meta = await getOfflineMeta();
  const entry = meta[articleId] || null;

  if (event.source) {
    event.source.postMessage({
      type: 'OFFLINE_STATUS',
      articleId: articleId,
      cached: !!entry,
      hasContent: !!(entry && entry.hasContent),
      hasAudio: !!(entry && entry.hasAudio),
    });
  }
}
