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

// Search (composed into the library list endpoint)
export const searchQuery = signal('');

// Pagination
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
  const saved = localStorage.getItem('tasche-theme');
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

// Generic status polling — used for both article processing and audio generation
function createPoller(intervalMs, field, toasts) {
  const timers = new Map();

  function stop(articleId) {
    const timerId = timers.get(articleId);
    if (timerId) {
      clearInterval(timerId);
      timers.delete(articleId);
    }
  }

  function start(articleId, fetchArticle) {
    if (timers.has(articleId)) return;
    const startTime = Date.now();
    const intervalId = setInterval(async function () {
      if (Date.now() - startTime > 600000) {
        stop(articleId);
        if (toasts.timeout) {
          addToast(toasts.timeout[0], toasts.timeout[1]);
        }
        return;
      }
      try {
        const article = await fetchArticle(articleId);
        const value = article[field];
        if (value === 'ready' || value === 'failed') {
          articles.value = articles.value.map(function (a) {
            return a.id === articleId ? { ...a, ...article } : a;
          });
          if (toasts[value]) {
            addToast(toasts[value][0], toasts[value][1]);
          }
          stop(articleId);
        }
      } catch (_e) {
        // Network error — keep polling until timeout
      }
    }, intervalMs);
    timers.set(articleId, intervalId);
  }

  return { start: start, stop: stop };
}

const audioPoller = createPoller(10000, 'audio_status', {
  ready: ['Audio is ready!', 'success'],
  failed: ['Audio generation failed', 'error'],
  timeout: ['Audio generation is taking longer than expected. Check back later.', 'info'],
});

const articlePoller = createPoller(5000, 'status', {
  ready: ['Article is ready!', 'success'],
  failed: ['Article processing failed', 'error'],
  timeout: ['Article processing is taking longer than expected. Check back later.', 'info'],
});

export const pollAudioStatus = audioPoller.start;
export const stopAudioPoll = audioPoller.stop;
export const pollArticleStatus = articlePoller.start;
export const stopArticlePoll = articlePoller.stop;
