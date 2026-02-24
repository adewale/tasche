/**
 * Tasche Service Worker
 *
 * Handles:
 * - Network-first for HTML navigation and hashed assets
 * - API response caching for GET requests
 * - Offline fallback with cached content
 * - Background sync for offline mutations
 * - Explicit "Save for offline" article + audio caching
 * - LRU cache eviction for offline content
 */

const STATIC_CACHE = 'tasche-static-v2';
const API_CACHE = 'tasche-api-v1';
const OFFLINE_CACHE = 'tasche-offline-v1';
const CACHE_NAME = 'tasche-v1';
const OFFLINE_META_KEY = 'tasche-offline-meta';

const SYNC_QUEUE_KEY = 'tasche-sync-queue';

// Maximum number of articles in the offline cache before LRU eviction
const MAX_OFFLINE_ARTICLES = 100;

// Default number of articles to auto-precache
const DEFAULT_PRECACHE_LIMIT = 20;

// Timeout for individual fetch requests during precaching (ms)
const PRECACHE_FETCH_TIMEOUT = 10000;

// ---------------------------------------------------------------------------
// Install — skip waiting, let activate handle cache cleanup
// ---------------------------------------------------------------------------

self.addEventListener('install', (event) => {
  // Don't precache the app shell — network-first handles it.
  // skipWaiting only after install succeeds (it's a no-op promise here).
  event.waitUntil(self.skipWaiting());
});

// ---------------------------------------------------------------------------
// Activate — clean old caches, claim clients
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
    }).then(() => self.clients.claim())
  );
});

// ---------------------------------------------------------------------------
// Fetch — route by request type
// ---------------------------------------------------------------------------

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // Hashed assets (/assets/index-CnPyFRMI.js) — cache first is safe because
  // the hash changes on every build, so a cache hit is always correct.
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(cacheFirstHashedAsset(event.request));
    return;
  }

  // Navigation and app shell (/, /manifest.json, /static/*) — network first
  // so we always get the latest index.html with correct script hashes.
  if (url.pathname === '/' || url.pathname === '/manifest.json' || url.pathname.startsWith('/static/')) {
    event.respondWith(networkFirstNavigation(event.request));
    return;
  }

  // API GET requests — network first, then offline cache, then API cache
  if (url.pathname.startsWith('/api/') && event.request.method === 'GET') {
    event.respondWith(networkFirstWithOffline(event.request));
    return;
  }

  // Everything else — network only (no respondWith, let browser handle)
});

/**
 * Cache-first for hashed assets. The content-hash in the filename guarantees
 * that a cache hit is always fresh. Cache misses go to network.
 */
async function cacheFirstHashedAsset(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(STATIC_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
  }
}

/**
 * Network-first for navigation requests (index.html, manifest, static assets).
 * Always tries the network to get the latest HTML with correct script hashes.
 * Falls back to cache only when offline.
 */
async function networkFirstNavigation(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(STATIC_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    const cached = await caches.match(request);
    if (cached) return cached;
    return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
  }
}

/**
 * Network first, then check the offline cache (explicit saves), then the API cache.
 * Only caches successful (2xx) complete responses.
 */
async function networkFirstWithOffline(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      // Clone before reading to ensure a complete response is cached
      const clone = response.clone();
      const cache = await caches.open(API_CACHE);
      await cache.put(request, clone);
    }
    return response;
  } catch (err) {
    // Try offline cache first (explicit saves)
    const offlineCache = await caches.open(OFFLINE_CACHE);
    const offlineCached = await offlineCache.match(request);
    if (offlineCached) {
      updateAccessTime(request.url);
      return offlineCached;
    }

    // Then try API cache (automatic caching) — scope to API_CACHE only
    const apiCache = await caches.open(API_CACHE);
    const apiCached = await apiCache.match(request);
    if (apiCached) return apiCached;

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
        remaining.push(item);
      }
    } catch (err) {
      remaining.push(item);
    }
  }

  await saveQueue(remaining);

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
  try {
    const cache = await caches.open(OFFLINE_CACHE);
    await cache.put(
      OFFLINE_META_KEY,
      new Response(JSON.stringify(meta), {
        headers: { 'Content-Type': 'application/json' },
      })
    );
  } catch {
    // Quota exceeded or other storage error — log but don't crash
  }
}

/**
 * Update access time for an article when served from offline cache (true LRU).
 * Awaits the full save chain so errors are properly contained.
 */
async function updateAccessTime(url) {
  try {
    const match = url.match(/\/api\/articles\/([^/]+)/);
    if (!match) return;
    const articleId = match[1];

    const meta = await getOfflineMeta();
    if (meta[articleId]) {
      meta[articleId].accessedAt = Date.now();
      await saveOfflineMeta(meta);
    }
  } catch {
    // Non-critical — LRU ordering may be slightly stale
  }
}

/**
 * Evict the least recently used articles if the count exceeds MAX_OFFLINE_ARTICLES.
 */
async function evictIfNeeded(meta) {
  const ids = Object.keys(meta);
  if (ids.length <= MAX_OFFLINE_ARTICLES) return meta;

  ids.sort((a, b) => (meta[a].accessedAt || 0) - (meta[b].accessedAt || 0));

  const cache = await caches.open(OFFLINE_CACHE);
  const toEvict = ids.slice(0, ids.length - MAX_OFFLINE_ARTICLES);

  for (const id of toEvict) {
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
// Fetch with timeout helper
// ---------------------------------------------------------------------------

function fetchWithTimeout(url, options, timeoutMs) {
  return new Promise(function (resolve, reject) {
    var timer = setTimeout(function () {
      reject(new Error('Fetch timeout: ' + url));
    }, timeoutMs);

    fetch(url, options).then(function (resp) {
      clearTimeout(timer);
      resolve(resp);
    }).catch(function (err) {
      clearTimeout(timer);
      reject(err);
    });
  });
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

    case 'AUTO_PRECACHE':
      event.waitUntil(handleAutoPrecache(event));
      break;

    case 'GET_CACHE_STATS':
      event.waitUntil(handleGetCacheStats(event));
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
  const articleIds = event.data.articleIds || [];
  const cache = await caches.open(API_CACHE);
  for (const id of articleIds) {
    const url = '/api/articles/' + id;
    try {
      const cached = await cache.match(url);
      if (!cached) {
        const resp = await fetchWithTimeout(url, { credentials: 'include' }, PRECACHE_FETCH_TIMEOUT);
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
    const detailResp = await fetch(detailUrl, { credentials: 'include' });
    if (detailResp.ok) {
      await cache.put(detailUrl, detailResp.clone());
    }

    const contentResp = await fetch(contentUrl, { credentials: 'include' });
    if (contentResp.ok) {
      await cache.put(contentUrl, contentResp.clone());
    }

    if (!detailResp.ok || !contentResp.ok) {
      throw new Error('Failed to cache: detail=' + detailResp.status + ' content=' + contentResp.status);
    }

    const existing = meta[articleId] || {};
    meta[articleId] = {
      ...existing,
      hasContent: true,
      accessedAt: Date.now(),
    };

    const cleaned = await evictIfNeeded(meta);
    await saveOfflineMeta(cleaned);

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

// ---------------------------------------------------------------------------
// Auto-precache — automatically cache recent unread articles with timeouts
// ---------------------------------------------------------------------------

async function handleAutoPrecache(event) {
  var limit = (event.data && event.data.limit) || DEFAULT_PRECACHE_LIMIT;
  var cached = 0;
  var skipped = 0;
  var failed = 0;

  try {
    var listUrl = '/api/articles?reading_status=unread&limit=' + limit + '&sort=created_at:desc';
    var listResp = await fetchWithTimeout(listUrl, { credentials: 'include' }, PRECACHE_FETCH_TIMEOUT);
    if (!listResp.ok) {
      throw new Error('Failed to fetch article list: ' + listResp.status);
    }

    var articles = await listResp.json();
    if (!Array.isArray(articles) || articles.length === 0) {
      notifyClients({
        type: 'AUTO_PRECACHE_COMPLETE',
        cached: 0,
        skipped: 0,
        failed: 0,
        total: 0,
      });
      return;
    }

    var cache = await caches.open(OFFLINE_CACHE);
    var meta = await getOfflineMeta();

    for (var i = 0; i < articles.length; i++) {
      var article = articles[i];
      if (!article.id) continue;

      if (article.status && article.status !== 'ready') {
        skipped++;
        continue;
      }

      if (meta[article.id] && meta[article.id].hasContent) {
        skipped++;
        continue;
      }

      try {
        var detailUrl = '/api/articles/' + article.id;
        var contentUrl = '/api/articles/' + article.id + '/content';

        var detailResp = await fetchWithTimeout(detailUrl, { credentials: 'include' }, PRECACHE_FETCH_TIMEOUT);
        if (!detailResp.ok) {
          failed++;
          continue;
        }

        var contentResp = await fetchWithTimeout(contentUrl, { credentials: 'include' }, PRECACHE_FETCH_TIMEOUT);
        if (!contentResp.ok) {
          failed++;
          continue;
        }

        await cache.put(detailUrl, detailResp.clone());
        await cache.put(contentUrl, contentResp.clone());

        var existing = meta[article.id] || {};
        meta[article.id] = {
          hasContent: true,
          hasAudio: existing.hasAudio || false,
          accessedAt: Date.now(),
          autoCached: true,
        };

        cached++;
      } catch (err) {
        failed++;
      }
    }

    meta = await evictIfNeeded(meta);
    await saveOfflineMeta(meta);

    notifyClients({
      type: 'AUTO_PRECACHE_COMPLETE',
      cached: cached,
      skipped: skipped,
      failed: failed,
      total: articles.length,
    });
  } catch (err) {
    notifyClients({
      type: 'AUTO_PRECACHE_ERROR',
      error: err.message,
    });
  }
}

// ---------------------------------------------------------------------------
// Cache stats — report number of cached articles and estimated size
// ---------------------------------------------------------------------------

async function handleGetCacheStats(event) {
  try {
    var meta = await getOfflineMeta();
    var articleIds = Object.keys(meta);
    var articleCount = articleIds.length;

    var totalSize = 0;
    var cache = await caches.open(OFFLINE_CACHE);
    var keys = await cache.keys();

    for (var i = 0; i < keys.length; i++) {
      var key = keys[i];
      if (key.url && key.url.endsWith(OFFLINE_META_KEY)) continue;
      try {
        var resp = await cache.match(key);
        if (resp) {
          var blob = await resp.clone().blob();
          totalSize += blob.size;
        }
      } catch (e) {
        // ignore individual size check errors
      }
    }

    if (event.source) {
      event.source.postMessage({
        type: 'CACHE_STATS',
        articleCount: articleCount,
        totalSize: totalSize,
      });
    }
  } catch (err) {
    if (event.source) {
      event.source.postMessage({
        type: 'CACHE_STATS',
        articleCount: 0,
        totalSize: 0,
      });
    }
  }
}
