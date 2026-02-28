/**
 * Tasche Global State (using @preact/signals)
 */

import { signal } from '@preact/signals';

// Auth
export const user = signal(null);

// Articles
export const articles = signal([]);

// Tags
export const tags = signal([]);

// Search
export const searchResults = signal([]);
export const searchQuery = signal('');

// Filters & pagination
export const filter = signal('unread'); // unread, listen, favorites, archived
export const offset = signal(0);
export const limit = signal(20);
export const hasMore = signal(true);
export const loading = signal(false);

// Online/offline
export const isOffline = signal(!navigator.onLine);

// Sync status: null | 'syncing' | 'synced' | 'error'
export const syncStatus = signal(null);

// Theme: 'light' | 'dark' | 'system'
function getInitialTheme() {
  var saved = localStorage.getItem('tasche-theme');
  return saved === 'light' || saved === 'dark' ? saved : 'system';
}
export const theme = signal(getInitialTheme());

export function applyTheme(value) {
  theme.value = value;
  if (value === 'system') {
    localStorage.removeItem('tasche-theme');
    document.documentElement.removeAttribute('data-theme');
  } else {
    localStorage.setItem('tasche-theme', value);
    document.documentElement.setAttribute('data-theme', value);
  }
}

// Apply on load
if (theme.value !== 'system') {
  document.documentElement.setAttribute('data-theme', theme.value);
}

// Keyboard shortcuts help overlay
export const showShortcuts = signal(false);

// Toast notifications
export const toasts = signal([]);

let toastId = 0;

export function addToast(message, type) {
  type = type || 'info';
  const id = ++toastId;
  toasts.value = [...toasts.value, { id, message, type }];
  setTimeout(() => {
    removeToast(id);
  }, 3000);
}

export function removeToast(id) {
  toasts.value = toasts.value.filter((t) => t.id !== id);
}

// Audio polling — shared across Library, ArticleCard, Reader
var audioPollTimers = new Map();

export function pollAudioStatus(articleId, fetchArticle) {
  if (audioPollTimers.has(articleId)) return;
  var startTime = Date.now();
  var intervalId = setInterval(async function () {
    if (Date.now() - startTime > 600000) {
      stopAudioPoll(articleId);
      return;
    }
    try {
      var article = await fetchArticle(articleId);
      if (article.audio_status === 'ready' || article.audio_status === 'failed') {
        articles.value = articles.value.map(function (a) {
          return a.id === articleId ? { ...a, ...article } : a;
        });
        if (article.audio_status === 'ready') {
          addToast('Audio is ready!', 'success');
        } else {
          addToast('Audio generation failed', 'error');
        }
        stopAudioPoll(articleId);
      }
    } catch (_e) {
      // Network error — keep polling until timeout
    }
  }, 10000);
  audioPollTimers.set(articleId, intervalId);
}

export function stopAudioPoll(articleId) {
  var timerId = audioPollTimers.get(articleId);
  if (timerId) {
    clearInterval(timerId);
    audioPollTimers.delete(articleId);
  }
}

// Article-status polling — detect processing → ready/failed transitions
var articlePollTimers = new Map();

export function pollArticleStatus(articleId, fetchArticle) {
  if (articlePollTimers.has(articleId)) return;
  var startTime = Date.now();
  var intervalId = setInterval(async function () {
    if (Date.now() - startTime > 600000) {
      stopArticlePoll(articleId);
      return;
    }
    try {
      var article = await fetchArticle(articleId);
      if (article.status === 'ready' || article.status === 'failed') {
        articles.value = articles.value.map(function (a) {
          return a.id === articleId ? { ...a, ...article } : a;
        });
        if (article.status === 'failed') {
          addToast('Article processing failed', 'error');
        }
        stopArticlePoll(articleId);
      }
    } catch (_e) {
      // Network error — keep polling until timeout
    }
  }, 5000);
  articlePollTimers.set(articleId, intervalId);
}

export function stopArticlePoll(articleId) {
  var timerId = articlePollTimers.get(articleId);
  if (timerId) {
    clearInterval(timerId);
    articlePollTimers.delete(articleId);
  }
}
