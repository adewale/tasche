import { useEffect, useState } from 'preact/hooks';

import { Toast } from './components/Toast.jsx';
import { AudioPlayer } from './components/AudioPlayer.jsx';
import { Library } from './views/Library.jsx';
import { Reader } from './views/Reader.jsx';
import { Search } from './views/Search.jsx';
import { Tags } from './views/Tags.jsx';
import { Settings } from './views/Settings.jsx';
import { Login } from './views/Login.jsx';
import { user, isOffline, syncStatus, addToast } from './state.js';
import { getSession, createArticle, triggerSync } from './api.js';

import './app.css';

/**
 * TagFilteredLibrary handles the `#/?tag=xxx` route pattern.
 * preact-router doesn't parse query strings from hash routes,
 * so we extract the tag param from window.location.hash.
 */
function TagFilteredLibrary() {
  const hash = window.location.hash;
  const match = hash.match(/[?&]tag=([^&]+)/);
  const tag = match ? decodeURIComponent(match[1]) : null;
  return <Library tag={tag} />;
}

/**
 * Route guard component that checks auth and renders appropriate view
 */
function AuthGuard({ component: Component, ...props }) {
  if (!user.value) {
    return <Login />;
  }
  return <Component {...props} />;
}

export function App() {
  const [ready, setReady] = useState(false);

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

    // Listen for sync status messages from service worker
    function handleSWMessage(event) {
      if (!event.data || event.data.type !== 'SYNC_STATUS') return;
      syncStatus.value = event.data.status;
      if (event.data.status === 'synced') {
        addToast('All changes synced', 'success');
        // Reset status after a moment
        setTimeout(function () {
          syncStatus.value = null;
        }, 3000);
      } else if (event.data.status === 'error') {
        addToast('Some changes failed to sync', 'error');
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

      // Handle Web Share Target (URL passed as query param)
      const urlParams = new URLSearchParams(window.location.search);
      const sharedUrl = urlParams.get('url');
      if (sharedUrl) {
        const sharedTitle = urlParams.get('title') || '';
        createArticle(sharedUrl, sharedTitle)
          .then(() => addToast('Article saved!', 'success'))
          .catch((e) => addToast('Save failed: ' + e.message, 'error'));
        // Clean the URL params
        window.history.replaceState(
          {},
          '',
          window.location.pathname + window.location.hash
        );
      }
    } catch (e) {
      user.value = null;
    }
    setReady(true);
  }

  if (!ready) {
    return (
      <div class="loading" style="min-height:100vh">
        <div class="spinner"></div>
      </div>
    );
  }

  return (
    <div id="app">
      <AppRouter />
      <Toast />
      <AudioPlayer />
    </div>
  );
}

/**
 * Hash-based router using preact-router.
 * We use handleRoute to detect tag-filtered routes
 * since preact-router doesn't handle query params in hash routes.
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

  // Check if the current hash has a tag filter: #/?tag=xxx
  if (currentPath.startsWith('/?tag=') || currentPath.match(/^\/\?.*tag=/)) {
    if (!user.value) return <Login />;
    return <TagFilteredLibrary />;
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

  const articleMatch = currentPath.match(/^\/article\/(.+)$/);
  if (articleMatch) {
    return <Reader id={articleMatch[1]} />;
  }

  if (currentPath === '/search') {
    return <Search />;
  }

  if (currentPath === '/tags') {
    return <Tags />;
  }

  if (currentPath === '/settings') {
    return <Settings />;
  }

  // Default to library
  return <Library />;
}
