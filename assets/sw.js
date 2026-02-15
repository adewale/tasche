/**
 * Tasche Service Worker
 *
 * Handles:
 * - App shell caching (index.html, app.js, style.css)
 * - API response caching for GET requests
 * - Offline fallback with cached content
 * - Background sync for offline mutations
 */

const CACHE_NAME = 'tasche-v1';
const STATIC_CACHE = 'tasche-static-v1';
const API_CACHE = 'tasche-api-v1';

const APP_SHELL = [
  '/',
  '/static/app.js',
  '/static/style.css',
  '/manifest.json',
];

const SYNC_QUEUE_KEY = 'tasche-sync-queue';

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
// Activate — clean old caches
// ---------------------------------------------------------------------------

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => {
      return Promise.all(
        keys
          .filter((key) => key !== STATIC_CACHE && key !== API_CACHE && key !== CACHE_NAME)
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
  if (APP_SHELL.includes(url.pathname) || url.pathname.startsWith('/static/')) {
    event.respondWith(cacheFirst(event.request, STATIC_CACHE));
    return;
  }

  // API GET requests — network first, cache fallback
  if (url.pathname.startsWith('/api/') && event.request.method === 'GET') {
    event.respondWith(networkFirst(event.request, API_CACHE));
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

async function networkFirst(request, cacheName) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(cacheName);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
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
  const remaining = [];

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
// Message handler — receive sync queue items from main thread
// ---------------------------------------------------------------------------

self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'QUEUE_REQUEST') {
    event.waitUntil(
      getQueue().then((queue) => {
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
      })
    );
  }

  if (event.data && event.data.type === 'CACHE_ARTICLES') {
    // Pre-cache article detail endpoints for offline reading
    const articleIds = event.data.articleIds || [];
    event.waitUntil(
      caches.open(API_CACHE).then(async (cache) => {
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
      })
    );
  }

  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
