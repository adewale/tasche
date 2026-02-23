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
  if (resp.status === 401) {
    user.value = null;
    navigateToLogin();
    throw new Error('Unauthorized');
  }
  if (resp.status === 204) return null;
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    const e = new Error(err.detail || 'Request failed');
    e.status = resp.status;
    throw e;
  }
  if (resp.headers.get('content-type')?.includes('application/json')) {
    return resp.json();
  }
  return resp;
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
  } catch (e) {
    // ignore network errors during logout
  }
  user.value = null;
  window.location.hash = '#/login';
}

// Articles
export function listArticles(params) {
  const qs = new URLSearchParams();
  if (params.reading_status) qs.set('reading_status', params.reading_status);
  if (params.is_favorite !== undefined) qs.set('is_favorite', params.is_favorite);
  if (params.audio_status) qs.set('audio_status', params.audio_status);
  if (params.tag) qs.set('tag', params.tag);
  if (params.limit) qs.set('limit', params.limit);
  if (params.offset) qs.set('offset', params.offset);
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

// Article content from R2
export function getArticleContent(articleId) {
  return fetch('/api/articles/' + articleId + '/content', { credentials: 'include' })
    .then(function (resp) {
      if (resp.status === 401) {
        user.value = null;
        navigateToLogin();
        return null;
      }
      if (!resp.ok) return null;
      return resp.text();
    })
    .catch(function () {
      return null;
    });
}

// Search
export function searchArticles(q, limit, offset) {
  const qs = new URLSearchParams({ q });
  if (limit) qs.set('limit', limit);
  if (offset) qs.set('offset', offset);
  return request('GET', '/api/search?' + qs.toString());
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

// Offline mutation queue
export function queueOfflineMutation(url, method, body) {
  if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
    navigator.serviceWorker.controller.postMessage({
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
}

// Cache articles for offline reading
export function cacheArticlesForOffline(articleIds) {
  if (
    'serviceWorker' in navigator &&
    navigator.serviceWorker.controller &&
    articleIds.length > 0
  ) {
    navigator.serviceWorker.controller.postMessage({
      type: 'CACHE_ARTICLES',
      articleIds: articleIds,
    });
  }
}

// Save a single article for offline reading (explicit user action)
export function saveForOffline(articleId) {
  if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
    navigator.serviceWorker.controller.postMessage({
      type: 'SAVE_FOR_OFFLINE',
      articleId: articleId,
    });
  }
}

// Save article audio for offline listening (explicit user action)
export function saveAudioOffline(articleId) {
  if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
    navigator.serviceWorker.controller.postMessage({
      type: 'SAVE_AUDIO_OFFLINE',
      articleId: articleId,
    });
  }
}

// Check if an article is cached for offline reading
// Returns a Promise that resolves with { cached, hasContent, hasAudio }
export function isOfflineCached(articleId) {
  return new Promise(function (resolve) {
    if (!('serviceWorker' in navigator) || !navigator.serviceWorker.controller) {
      resolve({ cached: false, hasContent: false, hasAudio: false });
      return;
    }

    var timeout = setTimeout(function () {
      navigator.serviceWorker.removeEventListener('message', handler);
      resolve({ cached: false, hasContent: false, hasAudio: false });
    }, 2000);

    function handler(event) {
      if (
        event.data &&
        event.data.type === 'OFFLINE_STATUS' &&
        event.data.articleId === articleId
      ) {
        clearTimeout(timeout);
        navigator.serviceWorker.removeEventListener('message', handler);
        resolve({
          cached: event.data.cached,
          hasContent: event.data.hasContent,
          hasAudio: event.data.hasAudio,
        });
      }
    }

    navigator.serviceWorker.addEventListener('message', handler);
    navigator.serviceWorker.controller.postMessage({
      type: 'CHECK_OFFLINE_STATUS',
      articleId: articleId,
    });
  });
}

// Trigger sync queue replay
export function triggerSync() {
  if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
    if ('SyncManager' in window) {
      navigator.serviceWorker.ready
        .then(function (reg) {
          return reg.sync.register('tasche-sync');
        })
        .catch(function () {
          // Fallback: send message directly
          navigator.serviceWorker.controller.postMessage({
            type: 'REPLAY_QUEUE',
          });
        });
    } else {
      navigator.serviceWorker.controller.postMessage({
        type: 'REPLAY_QUEUE',
      });
    }
  }
}
