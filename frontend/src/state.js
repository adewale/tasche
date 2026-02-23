/**
 * Tasche Global State (using @preact/signals)
 */

import { signal } from '@preact/signals';

// Auth
export const user = signal(null);

// Articles
export const articles = signal([]);
export const currentArticle = signal(null);

// Tags
export const tags = signal([]);

// Search
export const searchResults = signal([]);
export const searchQuery = signal('');

// Filters & pagination
export const filter = signal('unread'); // all, unread, reading, archived, favorites
export const offset = signal(0);
export const limit = signal(20);
export const hasMore = signal(true);
export const loading = signal(false);

// Online/offline
export const isOffline = signal(!navigator.onLine);

// Sync status: null | 'syncing' | 'synced' | 'error'
export const syncStatus = signal(null);

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
