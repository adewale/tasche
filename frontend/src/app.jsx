import { Component } from 'preact';
import { useEffect, useState } from 'preact/hooks';

import { Toast } from './components/Toast.jsx';
import { AudioPlayer } from './components/AudioPlayer.jsx';
import { KeyboardShortcutsHelp } from './components/KeyboardShortcutsHelp.jsx';
import { showShortcuts } from './state.js';
import { parseTagsFromHash } from './nav.js';
import { Library } from './views/Library.jsx';
import { Reader } from './views/Reader.jsx';
import { MarkdownView } from './views/MarkdownView.jsx';
import { Tags } from './views/Tags.jsx';
import { Settings } from './views/Settings.jsx';
import { Stats } from './views/Stats.jsx';
import { Login } from './views/Login.jsx';
import { user, isOffline, syncStatus, addToast } from './state.js';
import { getSession, createArticle, triggerSync, triggerAutoPrecache } from './api.js';

import './app.css';

/**
 * FilteredLibrary handles the `#/?tag=xxx` and `#/?q=xxx` route patterns.
 * Extracts tags (possibly multiple) and q params from window.location.hash.
 */
function FilteredLibrary() {
  const hash = window.location.hash;
  const tags = parseTagsFromHash(hash);
  const qMatch = hash.match(/[?&]q=([^&]*)/);
  const q = qMatch ? decodeURIComponent(qMatch[1]) : null;
  return <Library tags={tags} q={q} />;
}

class ErrorBoundary extends Component {
  state = { error: null };

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        <div class="error-boundary">
          <h2>Something went wrong</h2>
          <p>{this.state.error.message}</p>
          <button
            onClick={() => {
              this.setState({ error: null });
              window.location.hash = '#/';
            }}
          >
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export function App() {
  const [ready, setReady] = useState(false);

  // Global "?" keyboard shortcut to toggle the shortcuts help panel.
  // Registered here so it works on ALL screens (Library, Reader,
  // Tags, Stats, Settings), not just Library.
  useEffect(function () {
    function handleGlobalKeyDown(e) {
      var tagName = document.activeElement ? document.activeElement.tagName : '';
      if (tagName === 'INPUT' || tagName === 'TEXTAREA' || tagName === 'SELECT') {
        return;
      }
      if (e.key === '?' || (e.key === '/' && e.shiftKey)) {
        e.preventDefault();
        showShortcuts.value = !showShortcuts.value;
      }
      if (e.key === 'Escape' && showShortcuts.value) {
        e.preventDefault();
        showShortcuts.value = false;
      }
    }
    window.addEventListener('keydown', handleGlobalKeyDown);
    return function () {
      window.removeEventListener('keydown', handleGlobalKeyDown);
    };
  }, []);

  useEffect(() => {
    initApp();

    // Online/offline detection
    function handleOnline() {
      isOffline.value = false;
      addToast('Back online. Syncing...', 'info');
      syncStatus.value = 'syncing';
      triggerSync();
    }

    function handleOffline() {
      isOffline.value = true;
      addToast('You are offline', 'info');
    }

    // Centralized SW message handler — routes all message types (#13)
    function handleSWMessage(event) {
      if (!event.data || !event.data.type) return;

      switch (event.data.type) {
        case 'SYNC_STATUS':
          syncStatus.value = event.data.status;
          if (event.data.status === 'synced') {
            addToast('All changes synced', 'success');
            setTimeout(function () {
              syncStatus.value = null;
            }, 3000);
          } else if (event.data.status === 'error') {
            addToast(
              'Some offline changes could not be synced. They will retry automatically.',
              'error',
            );
          }
          break;

        case 'CACHES_CLEARED':
          addToast('Caches cleared', 'info');
          break;

        case 'AUTO_PRECACHE_COMPLETE': {
          const { cached, failed } = event.data;
          if (failed > 0) {
            addToast('Cached ' + cached + ' articles, ' + failed + ' failed', 'info');
          } else if (cached > 0) {
            addToast(
              cached + ' article' + (cached === 1 ? '' : 's') + ' cached for offline',
              'success',
            );
          }
          break;
        }

        case 'AUTO_PRECACHE_ERROR':
          addToast('Auto-cache could not complete', 'error');
          break;
      }
    }

    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.addEventListener('message', handleSWMessage);
    }

    return () => {
      window.removeEventListener('online', handleOnline);
      window.removeEventListener('offline', handleOffline);
      if ('serviceWorker' in navigator) {
        navigator.serviceWorker.removeEventListener('message', handleSWMessage);
      }
    };
  }, []);

  async function initApp() {
    try {
      const u = await getSession();
      user.value = u;

      // Schedule auto-precache after a short delay to avoid competing with
      // initial page load. Only runs when online and the preference is enabled.
      if (navigator.onLine) {
        var autoCacheEnabled = localStorage.getItem('tasche-auto-cache');
        // Default is enabled (null means not yet set, treat as enabled)
        if (autoCacheEnabled === null || autoCacheEnabled === 'true') {
          setTimeout(function () {
            if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
              triggerAutoPrecache(20);
            }
          }, 5000);
        }
      }

      // Handle Web Share Target (URL passed as query param)
      const urlParams = new URLSearchParams(window.location.search);
      const sharedUrl = urlParams.get('url');
      if (sharedUrl) {
        const sharedTitle = urlParams.get('title') || '';
        createArticle(sharedUrl, sharedTitle)
          .then(() => addToast('Article saved!', 'success'))
          .catch((e) => addToast('Could not save shared article: ' + e.message, 'error'));
        // Clean the URL params
        window.history.replaceState({}, '', window.location.pathname + window.location.hash);
      }
    } catch (_e) {
      user.value = null;
    }
    setReady(true);
  }

  if (!ready) {
    return (
      <div class="loading loading--fullscreen">
        <div class="spinner"></div>
      </div>
    );
  }

  return (
    <div id="app">
      <ErrorBoundary>
        <AppRouter />
      </ErrorBoundary>
      <Toast />
      <AudioPlayer />
      {showShortcuts.value && (
        <KeyboardShortcutsHelp
          onClose={function () {
            showShortcuts.value = false;
          }}
        />
      )}
    </div>
  );
}

/**
 * Hash-based router.
 * Manually parses window.location.hash for route matching.
 */
function AppRouter() {
  const [currentPath, setCurrentPath] = useState(window.location.hash.slice(1) || '/');

  useEffect(() => {
    function onHashChange() {
      setCurrentPath(window.location.hash.slice(1) || '/');
    }
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  // Check if the current hash has query params: #/?tag=xxx or #/?q=xxx
  if (currentPath.match(/^\/\?/)) {
    if (!user.value) return <Login />;
    return <FilteredLibrary />;
  }

  // Manual hash routing
  if (currentPath === '/login') {
    return <Login />;
  }

  if (!user.value) {
    return <Login />;
  }

  if (currentPath === '/' || currentPath === '') {
    return <Library />;
  }

  const markdownMatch = currentPath.match(/^\/article\/(.+)\/markdown$/);
  if (markdownMatch) {
    return <MarkdownView id={markdownMatch[1]} />;
  }

  const articleMatch = currentPath.match(/^\/article\/(.+)$/);
  if (articleMatch) {
    return <Reader id={articleMatch[1]} />;
  }

  if (currentPath === '/tags') {
    return <Tags />;
  }

  if (currentPath === '/stats') {
    return <Stats />;
  }

  if (currentPath === '/settings') {
    return <Settings />;
  }

  // Default to library
  return <Library />;
}
