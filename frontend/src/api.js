/**
 * Tasche API Client
 *
 * All API calls use credentials: 'include' for cookie-based auth.
 * On 401, redirects to login.
 */

import { user } from './state.js';

function navigateToLogin() {
  window.location.hash = '#/login';
}

function handleUnauthorized() {
  user.value = null;
  navigateToLogin();
  throw new Error('Unauthorized');
}

export async function request(method, path, body) {
  const opts = {
    method,
    headers: {},
    credentials: 'include',
  };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  if (resp.status === 401) handleUnauthorized();
  if (resp.status === 204) return null;
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    var detail = resp.statusText;
    try {
      detail = JSON.parse(text).detail || detail;
    } catch (_e) {
      // response wasn't JSON — use raw text for logging
    }
    console.error('[API] %s %s → %d %s', method, path, resp.status, detail, text.slice(0, 500));
    const e = new Error(detail);
    e.status = resp.status;
    throw e;
  }
  if (resp.headers.get('content-type')?.includes('application/json')) {
    const data = await resp.json();
    // Attach cache provenance when served from SW cache
    var cacheSource = resp.headers.get('X-Tasche-Source');
    if (cacheSource === 'cache' && data && typeof data === 'object' && !Array.isArray(data)) {
      data._cachedAt = resp.headers.get('X-Tasche-Cached-At') || new Date().toISOString();
    }
    return data;
  }
  return resp;
}

// Health (unauthenticated — uses plain fetch, not request())
// Retries with backoff to handle Python Worker cold starts after deploy
export function getHealthConfig() {
  var maxAttempts = 3;
  var baseDelay = 1500;

  function attempt(n) {
    return fetch('/api/health/config')
      .then(function (resp) {
        if (!resp.ok) throw new Error('Health check failed');
        return resp.json();
      })
      .catch(function (err) {
        console.error('[API] GET /api/health/config attempt %d failed:', n, err.message);
        if (n < maxAttempts) {
          return new Promise(function (resolve) {
            setTimeout(function () {
              resolve(attempt(n + 1));
            }, baseDelay * n);
          });
        }
        return { status: 'unreachable', checks: [] };
      });
  }

  return attempt(1);
}

// Auth
export function getSession() {
  return request('GET', '/api/auth/session');
}

export function logout() {
  return request('POST', '/api/auth/logout');
}

export async function performLogout() {
  try {
    await logout();
  } catch (_e) {
    // ignore network errors during logout
  }
  user.value = null;
  window.location.hash = '#/login';
}

// Articles
export function listArticles(params) {
  const qs = new URLSearchParams();
  if (params.q) qs.set('q', params.q);
  if (params.reading_status) qs.set('reading_status', params.reading_status);
  if (params.is_favorite !== undefined) qs.set('is_favorite', params.is_favorite);
  if (params.audio_status) qs.set('audio_status', params.audio_status);
  if (params.tag) {
    if (Array.isArray(params.tag)) {
      params.tag.forEach(function (t) {
        qs.append('tag', t);
      });
    } else {
      qs.append('tag', params.tag);
    }
  }
  if (params.sort) qs.set('sort', params.sort);
  if (params.limit != null) qs.set('limit', params.limit);
  if (params.offset != null) qs.set('offset', params.offset);
  return request('GET', '/api/articles?' + qs.toString());
}

export function getArticle(id) {
  return request('GET', '/api/articles/' + id);
}

export function createArticle(url, title, listenLater) {
  const body = { url };
  if (title) body.title = title;
  if (listenLater) body.listen_later = true;
  return request('POST', '/api/articles', body);
}

export function updateArticle(id, data) {
  return request('PATCH', '/api/articles/' + id, data);
}

export function deleteArticle(id) {
  return request('DELETE', '/api/articles/' + id);
}

// Batch operations
export function batchUpdateArticles(articleIds, updates) {
  return request('POST', '/api/articles/batch-update', {
    article_ids: articleIds,
    updates: updates,
  });
}

export function batchDeleteArticles(articleIds) {
  return request('POST', '/api/articles/batch-delete', {
    article_ids: articleIds,
  });
}

// Authenticated fetch with shared 401 handling (returns raw Response)
function authenticatedFetch(path) {
  return fetch(path, { credentials: 'include' }).then(function (resp) {
    if (resp.status === 401) handleUnauthorized();
    return resp;
  });
}

// Fetch text content (returns null on error/404)
function fetchText(path) {
  return authenticatedFetch(path)
    .then(function (resp) {
      if (!resp.ok) {
        console.error('[API] GET %s → %d %s', path, resp.status, resp.statusText);
        return null;
      }
      return resp.text();
    })
    .catch(function (err) {
      console.error('[API] GET %s failed:', path, err.message);
      return null;
    });
}

// Article content from R2
export function getArticleContent(articleId) {
  return fetchText('/api/articles/' + articleId + '/content');
}

// Article markdown from D1
export function getArticleMarkdown(articleId) {
  return fetchText('/api/articles/' + articleId + '/markdown');
}

// Tags
export function listTags() {
  return request('GET', '/api/tags');
}

export function createTag(name) {
  return request('POST', '/api/tags', { name });
}

export function deleteTag(id) {
  return request('DELETE', '/api/tags/' + id);
}

export function renameTag(id, newName) {
  return request('PATCH', '/api/tags/' + id, { name: newName });
}

export function getArticleTags(articleId) {
  return request('GET', '/api/articles/' + articleId + '/tags');
}

export function addArticleTag(articleId, tagId) {
  return request('POST', '/api/articles/' + articleId + '/tags', { tag_id: tagId });
}

export function removeArticleTag(articleId, tagId) {
  return request('DELETE', '/api/articles/' + articleId + '/tags/' + tagId);
}

// Retry failed/pending article
export function retryArticle(articleId) {
  return request('POST', '/api/articles/' + articleId + '/retry');
}

// Original URL health check
export function checkOriginal(articleId) {
  return request('POST', '/api/articles/' + articleId + '/check-original');
}

// TTS / Audio
export function listenLater(articleId) {
  return request('POST', '/api/articles/' + articleId + '/listen-later');
}

// Audio (fetches via fetch() so HTTP errors are visible in JS)
export function getAudioUrl(articleId) {
  var path = '/api/articles/' + articleId + '/audio';
  return authenticatedFetch(path).then(function (resp) {
    if (!resp.ok) {
      return resp.text().then(function (body) {
        var detail = resp.statusText;
        try {
          detail = JSON.parse(body).detail || detail;
        } catch (_e) {
          // response wasn't JSON — use statusText
        }
        console.error('[API] GET %s → %d %s', path, resp.status, detail, body.slice(0, 500));
        throw new Error(resp.status + ': ' + detail);
      });
    }
    return resp.blob().then(function (blob) {
      return URL.createObjectURL(blob);
    });
  });
}

// Audio timing (for immersive reading)
export function getAudioTiming(articleId) {
  return request('GET', '/api/articles/' + articleId + '/audio-timing');
}

// Preferences
export function getPreferences() {
  return request('GET', '/api/preferences');
}

export function updatePreferences(data) {
  return request('PATCH', '/api/preferences', data);
}

// Stats
export function getStats() {
  return request('GET', '/api/stats');
}

// Service worker messaging helper
function sendToSW(message) {
  if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
    navigator.serviceWorker.controller.postMessage(message);
  }
}

// Offline mutation queue
export function queueOfflineMutation(url, method, body) {
  sendToSW({
    type: 'QUEUE_REQUEST',
    request: {
      url: url,
      method: method,
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined,
    },
  });
  if ('SyncManager' in window) {
    navigator.serviceWorker.ready
      .then(function (reg) {
        return reg.sync.register('tasche-sync');
      })
      .catch(function () {});
  }
}

// Cache articles for offline reading
export function cacheArticlesForOffline(articleIds) {
  if (articleIds.length > 0) {
    sendToSW({ type: 'CACHE_ARTICLES', articleIds: articleIds });
  }
}

// Save a single article for offline reading (explicit user action)
export function saveForOffline(articleId) {
  sendToSW({ type: 'SAVE_FOR_OFFLINE', articleId: articleId });
}

// Save article audio for offline listening (explicit user action)
export function saveAudioOffline(articleId) {
  sendToSW({ type: 'SAVE_AUDIO_OFFLINE', articleId: articleId });
}

// Remove a single article from offline cache (explicit user action)
export function removeFromOffline(articleId) {
  sendToSW({ type: 'REMOVE_FROM_OFFLINE', articleId: articleId });
}

// Query the service worker and wait for a response message.
// sendMsg: message to post, responseType: expected event.data.type,
// fallback: value if SW unavailable, extract: event.data → result,
// match: optional extra predicate on event.data, timeoutMs: max wait.
function swQuery(sendMsg, responseType, fallback, extract, match, timeoutMs) {
  return new Promise(function (resolve) {
    if (!('serviceWorker' in navigator) || !navigator.serviceWorker.controller) {
      resolve(fallback);
      return;
    }

    var timeout = setTimeout(function () {
      navigator.serviceWorker.removeEventListener('message', handler);
      resolve(fallback);
    }, timeoutMs || 5000);

    function handler(event) {
      if (event.data && event.data.type === responseType && (!match || match(event.data))) {
        clearTimeout(timeout);
        navigator.serviceWorker.removeEventListener('message', handler);
        resolve(extract(event.data));
      }
    }

    navigator.serviceWorker.addEventListener('message', handler);
    navigator.serviceWorker.controller.postMessage(sendMsg);
  });
}

// Check if an article is cached for offline reading
export function isOfflineCached(articleId) {
  return swQuery(
    { type: 'CHECK_OFFLINE_STATUS', articleId: articleId },
    'OFFLINE_STATUS',
    { cached: false, hasContent: false, hasAudio: false },
    function (d) {
      return { cached: d.cached, hasContent: d.hasContent, hasAudio: d.hasAudio };
    },
    function (d) {
      return d.articleId === articleId;
    },
    2000,
  );
}

// Trigger auto-precaching of recent unread articles
export function triggerAutoPrecache(limit) {
  sendToSW({ type: 'AUTO_PRECACHE', limit: limit || 20 });
}

// Request cache stats from the service worker
export function getCacheStats() {
  return swQuery(
    { type: 'GET_CACHE_STATS' },
    'CACHE_STATS',
    { articleCount: 0, totalSize: 0 },
    function (d) {
      return { articleCount: d.articleCount, totalSize: d.totalSize };
    },
  );
}

// Clear SW caches (force refresh)
export function clearAllCaches() {
  return swQuery({ type: 'CLEAR_CACHES' }, 'CACHES_CLEARED', false, function () {
    return true;
  });
}

// Trigger sync queue replay
export function triggerSync() {
  if ('SyncManager' in window && 'serviceWorker' in navigator) {
    navigator.serviceWorker.ready
      .then(function (reg) {
        return reg.sync.register('tasche-sync');
      })
      .catch(function () {
        sendToSW({ type: 'REPLAY_QUEUE' });
      });
  } else {
    sendToSW({ type: 'REPLAY_QUEUE' });
  }
}
