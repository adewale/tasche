/**
 * Tasche Service Worker
 *
 * Handles:
 * - Network-first with timeout for API requests and navigation
 * - Cache-first for content-hashed static assets
 * - API cache invalidation after mutations
 * - Offline fallback with cache provenance headers
 * - Background sync for offline mutations with conflict detection
 * - Explicit "Save for offline" article + audio caching
 * - LRU cache eviction for offline content
 * - Graceful degradation (5xx → stale cache with indicator)
 */

const STATIC_CACHE = 'tasche-static-v2';
const API_CACHE = 'tasche-api-v2';
const OFFLINE_CACHE = 'tasche-offline-v1';
const CACHE_NAME = 'tasche-v1';
const OFFLINE_META_KEY = 'tasche-offline-meta';
const SYNC_QUEUE_KEY = 'tasche-sync-queue';

// Maximum number of articles in the offline cache before LRU eviction
const MAX_OFFLINE_ARTICLES = 100;

// Maximum entries in the API cache before pruning
const MAX_API_CACHE_ENTRIES = 60;

// Default number of articles to auto-precache
const DEFAULT_PRECACHE_LIMIT = 20;

// Timeout for individual fetch requests during precaching (ms)
const PRECACHE_FETCH_TIMEOUT = 10000;

// Timeout before falling back to cache on slow networks (ms)
const NETWORK_TIMEOUT_MS = 4000;

// ---------------------------------------------------------------------------
// Install — cache app shell, skip waiting
// ---------------------------------------------------------------------------

self.addEventListener('install', function (event) {
  event.waitUntil(self.skipWaiting());
});

// ---------------------------------------------------------------------------
// Activate — clean old caches, claim clients
// ---------------------------------------------------------------------------

self.addEventListener('activate', function (event) {
  var KEEP = [STATIC_CACHE, API_CACHE, CACHE_NAME, OFFLINE_CACHE];
  event.waitUntil(
    caches
      .keys()
      .then(function (keys) {
        return Promise.all(
          keys
            .filter(function (key) {
              return !KEEP.includes(key);
            })
            .map(function (key) {
              return caches.delete(key);
            }),
        );
      })
      .then(function () {
        return self.clients.claim();
      }),
  );
});

// ---------------------------------------------------------------------------
// Fetch — route by request type
// ---------------------------------------------------------------------------

self.addEventListener('fetch', function (event) {
  var url = new URL(event.request.url);

  // Only handle same-origin requests
  if (url.origin !== self.location.origin) return;

  // Hashed assets (/assets/index-CnPyFRMI.js) — cache first is safe because
  // the hash changes on every build, so a cache hit is always correct.
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      cacheFirstHashedAsset(event.request).catch(function () {
        return fetch(event.request);
      }),
    );
    return;
  }

  // Navigation and app shell (/, /manifest.json, /static/*) — network first
  // with timeout so slow connections fall back to cached shell quickly.
  if (
    url.pathname === '/' ||
    url.pathname === '/manifest.json' ||
    url.pathname.startsWith('/static/')
  ) {
    event.respondWith(
      networkFirstNavigation(event.request).catch(function () {
        return fetch(event.request);
      }),
    );
    return;
  }

  // API GET requests — network first with timeout, then cache fallback
  if (url.pathname.startsWith('/api/') && event.request.method === 'GET') {
    event.respondWith(
      networkFirstWithOffline(event.request).catch(function () {
        return fetch(event.request);
      }),
    );
    return;
  }

  // API mutations (POST, PATCH, PUT, DELETE) — pass through to network,
  // then invalidate related API cache entries on success
  if (url.pathname.startsWith('/api/') && event.request.method !== 'GET') {
    event.respondWith(
      networkWithCacheInvalidation(event.request).catch(function () {
        return fetch(event.request);
      }),
    );
    return;
  }

  // Everything else — network only (no respondWith, let browser handle)
});

// ---------------------------------------------------------------------------
// Cache-first for hashed assets
// ---------------------------------------------------------------------------

async function cacheFirstHashedAsset(request) {
  var cached = await caches.match(request);
  if (cached) return cached;

  try {
    var response = await fetch(request);
    if (response.ok) {
      var cache = await caches.open(STATIC_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
  }
}

// ---------------------------------------------------------------------------
// Network-first with timeout for navigation
// ---------------------------------------------------------------------------

async function networkFirstNavigation(request) {
  try {
    var response = await raceTimeout(fetch(request), NETWORK_TIMEOUT_MS);
    if (response.ok) {
      var cache = await caches.open(STATIC_CACHE);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    var cached = await caches.match(request);
    if (cached) return cached;
    return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
  }
}

// ---------------------------------------------------------------------------
// Network-first with timeout for API requests
// Stamps X-Tasche-Cached-At on write, X-Tasche-Source: cache on read.
// Falls back to cache on both network errors AND 5xx server errors.
// ---------------------------------------------------------------------------

async function networkFirstWithOffline(request) {
  var response;
  try {
    response = await raceTimeout(fetch(request), NETWORK_TIMEOUT_MS);
  } catch (err) {
    // Network error or timeout — fall through to cache
    return serveCachedOrOffline(request);
  }

  // Successful response — cache it and return
  if (response.ok) {
    var body = await response.clone().blob();
    var headers = new Headers(response.headers);
    headers.set('X-Tasche-Cached-At', new Date().toISOString());
    var stamped = new Response(body, {
      status: response.status,
      statusText: response.statusText,
      headers: headers,
    });
    var cache = await caches.open(API_CACHE);
    await cache.put(request, stamped);
    pruneApiCache();
    return response;
  }

  // 5xx server error — try serving from cache with staleness indicator (#6)
  if (response.status >= 500) {
    var cachedFallback = await findBestCached(request);
    if (cachedFallback) return markAsCached(cachedFallback);
  }

  // 4xx or other — return the error response as-is
  return response;
}

/**
 * Search offline cache then API cache for the best cached response.
 * Prefers the most recently cached version.
 */
async function findBestCached(request) {
  var offlineCache = await caches.open(OFFLINE_CACHE);
  var offlineCached = await offlineCache.match(request);

  var apiCache = await caches.open(API_CACHE);
  var apiCached = await apiCache.match(request);

  if (offlineCached && apiCached) {
    // Compare timestamps — prefer the fresher one
    var offlineTime = offlineCached.headers.get('X-Tasche-Cached-At');
    var apiTime = apiCached.headers.get('X-Tasche-Cached-At');
    if (offlineTime && apiTime && new Date(apiTime) > new Date(offlineTime)) {
      return apiCached;
    }
    return offlineCached;
  }

  return offlineCached || apiCached || null;
}

/**
 * Fall back to cache when network is unavailable.
 */
async function serveCachedOrOffline(request) {
  // Try offline cache first (explicit saves)
  var offlineCache = await caches.open(OFFLINE_CACHE);
  var offlineCached = await offlineCache.match(request);
  if (offlineCached) {
    updateAccessTime(request.url);
    return markAsCached(offlineCached);
  }

  // Then try API cache
  var apiCache = await caches.open(API_CACHE);
  var apiCached = await apiCache.match(request);
  if (apiCached) return markAsCached(apiCached);

  return new Response(JSON.stringify({ error: 'Offline' }), {
    status: 503,
    headers: { 'Content-Type': 'application/json' },
  });
}

/**
 * Mark a cached response with X-Tasche-Source: cache so the frontend
 * can detect it was served from the SW cache, not the network.
 */
function markAsCached(response) {
  var headers = new Headers(response.headers);
  headers.set('X-Tasche-Source', 'cache');
  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: headers,
  });
}

// ---------------------------------------------------------------------------
// API mutation handler — pass through + cache invalidation (#1)
// ---------------------------------------------------------------------------

async function networkWithCacheInvalidation(request) {
  var response = await fetch(request);

  // Invalidate related cache entries after any successful mutation
  if (response.ok || response.status === 204) {
    invalidateApiCache(request.url);
  }

  return response;
}

/**
 * Invalidate API cache entries related to a mutation URL.
 * After archiving/deleting/updating an article, the list endpoints and
 * the specific article endpoint all become stale.
 */
async function invalidateApiCache(mutationUrl) {
  try {
    var cache = await caches.open(API_CACHE);
    var keys = await cache.keys();

    for (var i = 0; i < keys.length; i++) {
      var cachedUrl = new URL(keys[i].url);
      var pathname = cachedUrl.pathname;

      // Always invalidate list endpoints — any mutation can change the list
      if (pathname === '/api/articles' || pathname === '/api/search') {
        await cache.delete(keys[i]);
        continue;
      }

      // Invalidate the specific article that was mutated
      var match = mutationUrl.match(/\/api\/articles\/([^/]+)/);
      if (match) {
        var articleId = match[1];
        if (pathname.startsWith('/api/articles/' + articleId)) {
          await cache.delete(keys[i]);
          continue;
        }
      }

      // Invalidate tags and stats — they can change after article mutations
      if (pathname === '/api/tags' || pathname === '/api/stats') {
        await cache.delete(keys[i]);
      }
    }
  } catch (err) {
    // Cache invalidation is best-effort
  }
}

// ---------------------------------------------------------------------------
// API cache pruning — prevent unbounded growth (#5)
// ---------------------------------------------------------------------------

async function pruneApiCache() {
  try {
    var cache = await caches.open(API_CACHE);
    var keys = await cache.keys();

    if (keys.length <= MAX_API_CACHE_ENTRIES) return;

    // Build entries with their cached-at times
    var entries = [];
    for (var i = 0; i < keys.length; i++) {
      var resp = await cache.match(keys[i]);
      var cachedAt = resp ? resp.headers.get('X-Tasche-Cached-At') : null;
      entries.push({
        request: keys[i],
        time: cachedAt ? new Date(cachedAt).getTime() : 0,
      });
    }

    // Sort oldest first
    entries.sort(function (a, b) {
      return a.time - b.time;
    });

    // Delete oldest entries to get back under the limit
    var toDelete = entries.slice(0, entries.length - MAX_API_CACHE_ENTRIES);
    for (var j = 0; j < toDelete.length; j++) {
      await cache.delete(toDelete[j].request);
    }
  } catch (err) {
    // Pruning is best-effort
  }
}

// ---------------------------------------------------------------------------
// Race timeout helper (#2)
// ---------------------------------------------------------------------------

function raceTimeout(fetchPromise, timeoutMs) {
  return new Promise(function (resolve, reject) {
    var timer = setTimeout(function () {
      reject(new Error('Network timeout'));
    }, timeoutMs);

    fetchPromise
      .then(function (resp) {
        clearTimeout(timer);
        resolve(resp);
      })
      .catch(function (err) {
        clearTimeout(timer);
        reject(err);
      });
  });
}

/**
 * Fetch with timeout — used for precaching where we want per-URL timeouts.
 */
function fetchWithTimeout(url, options, timeoutMs) {
  return raceTimeout(fetch(url, options), timeoutMs);
}

// ---------------------------------------------------------------------------
// Background Sync — replay queued mutations with conflict detection (#7)
// ---------------------------------------------------------------------------

self.addEventListener('sync', function (event) {
  if (event.tag === 'tasche-sync') {
    event.waitUntil(replayQueue());
  }
});

async function replayQueue() {
  var queue = await getQueue();
  if (queue.length === 0) return;

  var remaining = [];

  notifyClients({ type: 'SYNC_STATUS', status: 'syncing' });

  for (var i = 0; i < queue.length; i++) {
    var item = queue[i];
    try {
      // Drop mutations older than 1 hour — they're likely stale (#7)
      if (item.queuedAt && Date.now() - item.queuedAt > 3600000) {
        continue;
      }

      var response = await fetch(item.url, {
        method: item.method,
        headers: item.headers,
        body: item.body,
        credentials: 'include',
      });

      // Successful — invalidate related cache entries
      if (response.ok || response.status === 204) {
        invalidateApiCache(item.url);
      }

      if (!response.ok && response.status >= 500) {
        remaining.push(item);
      }
      // 4xx errors (conflicts, not found) — drop them, don't retry
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
    var cache = await caches.open(CACHE_NAME);
    var response = await cache.match(SYNC_QUEUE_KEY);
    if (!response) return [];
    return await response.json();
  } catch (err) {
    return [];
  }
}

async function saveQueue(queue) {
  var cache = await caches.open(CACHE_NAME);
  await cache.put(
    SYNC_QUEUE_KEY,
    new Response(JSON.stringify(queue), {
      headers: { 'Content-Type': 'application/json' },
    }),
  );
}

// ---------------------------------------------------------------------------
// Offline Metadata — track cached articles for LRU eviction
// ---------------------------------------------------------------------------

async function getOfflineMeta() {
  try {
    var cache = await caches.open(OFFLINE_CACHE);
    var resp = await cache.match(OFFLINE_META_KEY);
    if (!resp) return {};
    return await resp.json();
  } catch (err) {
    return {};
  }
}

async function saveOfflineMeta(meta) {
  try {
    var cache = await caches.open(OFFLINE_CACHE);
    await cache.put(
      OFFLINE_META_KEY,
      new Response(JSON.stringify(meta), {
        headers: { 'Content-Type': 'application/json' },
      }),
    );
  } catch (err) {
    // Quota exceeded or other storage error — log but don't crash
  }
}

async function updateAccessTime(url) {
  try {
    var match = url.match(/\/api\/articles\/([^/]+)/);
    if (!match) return;
    var articleId = match[1];

    var meta = await getOfflineMeta();
    if (meta[articleId]) {
      meta[articleId].accessedAt = Date.now();
      await saveOfflineMeta(meta);
    }
  } catch (err) {
    // Non-critical — LRU ordering may be slightly stale
  }
}

async function evictIfNeeded(meta) {
  var ids = Object.keys(meta);
  if (ids.length <= MAX_OFFLINE_ARTICLES) return meta;

  ids.sort(function (a, b) {
    return (meta[a].accessedAt || 0) - (meta[b].accessedAt || 0);
  });

  var cache = await caches.open(OFFLINE_CACHE);
  var toEvict = ids.slice(0, ids.length - MAX_OFFLINE_ARTICLES);

  for (var i = 0; i < toEvict.length; i++) {
    var id = toEvict[i];
    await cache.delete('/api/articles/' + id);
    await cache.delete('/api/articles/' + id + '/content');
    await cache.delete('/api/articles/' + id + '/audio');
    await cache.delete('/api/articles/' + id + '/audio-timing');
    delete meta[id];
  }

  return meta;
}

// ---------------------------------------------------------------------------
// Notify all clients
// ---------------------------------------------------------------------------

async function notifyClients(message) {
  var clients = await self.clients.matchAll({ type: 'window' });
  for (var i = 0; i < clients.length; i++) {
    clients[i].postMessage(message);
  }
}

// ---------------------------------------------------------------------------
// Message handler — receive commands from main thread
// ---------------------------------------------------------------------------

self.addEventListener('message', function (event) {
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

    case 'CLEAR_CACHES':
      event.waitUntil(handleClearCaches(event));
      break;
  }
});

async function handleQueueRequest(event) {
  var queue = await getQueue();
  var newReq = event.data.request;
  // Add timestamp for conflict detection (#7)
  newReq.queuedAt = Date.now();
  var existingIndex = queue.findIndex(function (item) {
    return item.url === newReq.url && item.method === newReq.method;
  });
  if (existingIndex !== -1) {
    queue[existingIndex] = newReq;
  } else {
    queue.push(newReq);
  }
  return saveQueue(queue);
}

// Always refresh prefetched articles — removes the stale guard (#11)
async function handleCacheArticles(event) {
  var articleIds = event.data.articleIds || [];
  var apiCache = await caches.open(API_CACHE);
  for (var i = 0; i < articleIds.length; i++) {
    var id = articleIds[i];
    var url = '/api/articles/' + id;
    try {
      var resp = await fetchWithTimeout(url, { credentials: 'include' }, PRECACHE_FETCH_TIMEOUT);
      if (resp.ok) {
        // Stamp with cache time
        var body = await resp.clone().blob();
        var headers = new Headers(resp.headers);
        headers.set('X-Tasche-Cached-At', new Date().toISOString());
        var stamped = new Response(body, {
          status: resp.status,
          statusText: resp.statusText,
          headers: headers,
        });
        apiCache.put(url, stamped);
      }
    } catch (e) {
      // ignore fetch errors during prefetch
    }
  }
}

async function handleSaveForOffline(event) {
  var articleId = event.data.articleId;
  if (!articleId) return;

  var offlineCache = await caches.open(OFFLINE_CACHE);
  var apiCache = await caches.open(API_CACHE);
  var meta = await getOfflineMeta();

  var detailUrl = '/api/articles/' + articleId;
  var contentUrl = '/api/articles/' + articleId + '/content';

  try {
    var detailResp = await fetch(detailUrl, { credentials: 'include' });
    if (detailResp.ok) {
      // Stamp and store in both caches (#12)
      var dBody = await detailResp.clone().blob();
      var dHeaders = new Headers(detailResp.headers);
      dHeaders.set('X-Tasche-Cached-At', new Date().toISOString());
      var dStamped = new Response(dBody, {
        status: detailResp.status,
        statusText: detailResp.statusText,
        headers: dHeaders,
      });
      await offlineCache.put(detailUrl, dStamped.clone());
      await apiCache.put(detailUrl, dStamped);
    }

    var contentResp = await fetch(contentUrl, { credentials: 'include' });
    if (contentResp.ok) {
      var cBody = await contentResp.clone().blob();
      var cHeaders = new Headers(contentResp.headers);
      cHeaders.set('X-Tasche-Cached-At', new Date().toISOString());
      var cStamped = new Response(cBody, {
        status: contentResp.status,
        statusText: contentResp.statusText,
        headers: cHeaders,
      });
      await offlineCache.put(contentUrl, cStamped.clone());
      await apiCache.put(contentUrl, cStamped);
    }

    if (!detailResp.ok || !contentResp.ok) {
      throw new Error(
        'Failed to cache: detail=' + detailResp.status + ' content=' + contentResp.status,
      );
    }

    var existing = meta[articleId] || {};
    meta[articleId] = {
      hasContent: true,
      hasAudio: existing.hasAudio || false,
      accessedAt: Date.now(),
    };

    var cleaned = await evictIfNeeded(meta);
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
  var articleId = event.data.articleId;
  if (!articleId) return;

  var cache = await caches.open(OFFLINE_CACHE);
  var meta = await getOfflineMeta();

  var audioUrl = '/api/articles/' + articleId + '/audio';

  try {
    var audioResp = await fetch(audioUrl, { credentials: 'include' });
    if (audioResp.ok) {
      await cache.put(audioUrl, audioResp.clone());
    } else {
      throw new Error('Failed to fetch audio: ' + audioResp.status);
    }

    // Also cache audio-timing for immersive reading
    var timingUrl = '/api/articles/' + articleId + '/audio-timing';
    try {
      var timingResp = await fetch(timingUrl, { credentials: 'include' });
      if (timingResp.ok) {
        await cache.put(timingUrl, timingResp.clone());
      }
    } catch (_e) {
      // Timing data is optional — audio works without it
    }

    var existing = meta[articleId] || {};
    meta[articleId] = {
      hasContent: existing.hasContent || false,
      hasAudio: true,
      accessedAt: Date.now(),
    };

    var cleaned = await evictIfNeeded(meta);
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
  var articleId = event.data.articleId;
  if (!articleId) return;

  var meta = await getOfflineMeta();
  var entry = meta[articleId] || null;

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
// Auto-precache — stores in BOTH offline and API caches (#12)
// ---------------------------------------------------------------------------

async function handleAutoPrecache(event) {
  var limit = (event.data && event.data.limit) || DEFAULT_PRECACHE_LIMIT;
  var cached = 0;
  var skipped = 0;
  var failed = 0;

  try {
    var listUrl = '/api/articles?reading_status=unread&limit=' + limit + '&sort=newest';
    var listResp = await fetchWithTimeout(
      listUrl,
      { credentials: 'include' },
      PRECACHE_FETCH_TIMEOUT,
    );
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

    var offlineCache = await caches.open(OFFLINE_CACHE);
    var apiCache = await caches.open(API_CACHE);
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

        var detailResp = await fetchWithTimeout(
          detailUrl,
          { credentials: 'include' },
          PRECACHE_FETCH_TIMEOUT,
        );
        if (!detailResp.ok) {
          failed++;
          continue;
        }

        var contentResp = await fetchWithTimeout(
          contentUrl,
          { credentials: 'include' },
          PRECACHE_FETCH_TIMEOUT,
        );
        if (!contentResp.ok) {
          failed++;
          continue;
        }

        // Stamp and store in both caches (#12)
        var now = new Date().toISOString();

        var dBody = await detailResp.clone().blob();
        var dHeaders = new Headers(detailResp.headers);
        dHeaders.set('X-Tasche-Cached-At', now);
        var dStamped = new Response(dBody, {
          status: detailResp.status,
          statusText: detailResp.statusText,
          headers: dHeaders,
        });
        await offlineCache.put(detailUrl, dStamped.clone());
        await apiCache.put(detailUrl, dStamped);

        var cBody = await contentResp.clone().blob();
        var cHeaders = new Headers(contentResp.headers);
        cHeaders.set('X-Tasche-Cached-At', now);
        var cStamped = new Response(cBody, {
          status: contentResp.status,
          statusText: contentResp.statusText,
          headers: cHeaders,
        });
        await offlineCache.put(contentUrl, cStamped.clone());
        await apiCache.put(contentUrl, cStamped);

        // Also cache audio-timing for articles with audio
        if (article.audio_status === 'ready') {
          try {
            var timingUrl = '/api/articles/' + article.id + '/audio-timing';
            var timingResp = await fetchWithTimeout(
              timingUrl,
              { credentials: 'include' },
              PRECACHE_FETCH_TIMEOUT,
            );
            if (timingResp.ok) {
              await offlineCache.put(timingUrl, timingResp.clone());
            }
          } catch (_e) {
            // Timing is optional
          }
        }

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
// Cache stats
// ---------------------------------------------------------------------------

async function handleGetCacheStats(event) {
  try {
    var meta = await getOfflineMeta();
    var articleCount = Object.keys(meta).length;

    var totalSize = 0;
    var cache = await caches.open(OFFLINE_CACHE);
    var keys = await cache.keys();

    for (var i = 0; i < keys.length; i++) {
      if (keys[i].url && keys[i].url.endsWith(OFFLINE_META_KEY)) continue;
      try {
        var resp = await cache.match(keys[i]);
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

// ---------------------------------------------------------------------------
// Clear all caches (#9) — user-initiated "force refresh"
// ---------------------------------------------------------------------------

async function handleClearCaches(event) {
  try {
    await caches.delete(API_CACHE);
    await caches.delete(STATIC_CACHE);
    // Don't delete OFFLINE_CACHE — user explicitly saved those articles
    if (event.source) {
      event.source.postMessage({ type: 'CACHES_CLEARED' });
    }
    notifyClients({ type: 'CACHES_CLEARED' });
  } catch (err) {
    if (event.source) {
      event.source.postMessage({ type: 'CACHES_CLEAR_ERROR', error: err.message });
    }
  }
}
