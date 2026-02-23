import { useSignal } from '@preact/signals';
import { useEffect } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { user } from '../state.js';
import { performLogout, exportData, getCacheStats, triggerAutoPrecache } from '../api.js';
import { getBookmarkletCode } from '../utils.js';
import { IconBookmark } from '../components/Icons.jsx';

function formatBytes(bytes) {
  if (bytes === 0) return '0 B';
  var units = ['B', 'KB', 'MB', 'GB'];
  var i = Math.floor(Math.log(bytes) / Math.log(1024));
  if (i >= units.length) i = units.length - 1;
  var value = bytes / Math.pow(1024, i);
  return value.toFixed(i === 0 ? 0 : 1) + ' ' + units[i];
}

export function Settings() {
  const u = user.value;
  const exporting = useSignal(null);
  const autoCacheEnabled = useSignal(
    localStorage.getItem('tasche-auto-cache') !== 'false'
  );
  const cacheStats = useSignal(null);
  const precaching = useSignal(false);

  useEffect(function () {
    loadCacheStats();

    function handleSWMessage(event) {
      if (!event.data) return;
      if (event.data.type === 'AUTO_PRECACHE_COMPLETE') {
        precaching.value = false;
        loadCacheStats();
      } else if (event.data.type === 'AUTO_PRECACHE_ERROR') {
        precaching.value = false;
      }
    }

    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.addEventListener('message', handleSWMessage);
    }

    return function () {
      if ('serviceWorker' in navigator) {
        navigator.serviceWorker.removeEventListener('message', handleSWMessage);
      }
    };
  }, []);

  function loadCacheStats() {
    getCacheStats().then(function (stats) {
      cacheStats.value = stats;
    });
  }

  function handleToggleAutoCache() {
    var newValue = !autoCacheEnabled.value;
    autoCacheEnabled.value = newValue;
    localStorage.setItem('tasche-auto-cache', String(newValue));
  }

  function handlePrecacheNow() {
    precaching.value = true;
    triggerAutoPrecache(20);
  }

  function handleExport(format) {
    exporting.value = format;
    exportData(format)
      .catch(function () {
        // export errors are non-critical
      })
      .finally(function () {
        exporting.value = null;
      });
  }

  return (
    <>
      <Header />
      <main class="main-content">
        <h2 class="section-title">Settings</h2>

        <div style={{ marginTop: '16px' }}>
          <h3 class="section-title">Offline Reading</h3>
          <div class="settings-toggle-row">
            <div class="settings-toggle-info">
              <span class="settings-toggle-label">Auto-cache articles for offline</span>
              <span class="settings-toggle-desc">
                Automatically cache your 20 most recent unread articles
                so they are available offline.
              </span>
            </div>
            <button
              class={'settings-toggle' + (autoCacheEnabled.value ? ' settings-toggle--on' : '')}
              onClick={handleToggleAutoCache}
              role="switch"
              aria-checked={autoCacheEnabled.value}
            >
              <span class="settings-toggle-knob" />
            </button>
          </div>
          {cacheStats.value && (
            <p class="settings-detail" style={{ marginTop: '12px' }}>
              {cacheStats.value.articleCount === 0
                ? 'No articles cached.'
                : cacheStats.value.articleCount + ' article' +
                  (cacheStats.value.articleCount === 1 ? '' : 's') +
                  ' cached (' + formatBytes(cacheStats.value.totalSize) + ')'
              }
            </p>
          )}
          <button
            class="btn btn-secondary"
            style={{ marginTop: '8px' }}
            disabled={precaching.value || !navigator.onLine}
            onClick={handlePrecacheNow}
          >
            {precaching.value ? 'Caching...' : 'Cache articles now'}
          </button>
        </div>

        <div style={{ marginTop: '32px' }}>
          <h3 class="section-title">Bookmarklet</h3>
          <p class="bookmarklet-hint">
            Drag this link to your bookmarks bar to save articles from any page.
            The bookmarklet captures the page content directly, so it works with
            paywalled articles you can see in your browser.
          </p>
          <a
            href={getBookmarkletCode()}
            class="btn btn-secondary"
            onClick={function (e) { e.preventDefault(); }}
          >
            <IconBookmark /> Save to Tasche
          </a>
        </div>

        <div style={{ marginTop: '32px' }}>
          <h3 class="section-title">Export Data</h3>
          <p class="settings-detail">
            Download your saved articles for backup or migration.
          </p>
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <button
              class="btn btn-secondary"
              disabled={exporting.value !== null}
              onClick={function () { handleExport('json'); }}
            >
              {exporting.value === 'json' ? 'Exporting...' : 'Export as JSON'}
            </button>
            <button
              class="btn btn-secondary"
              disabled={exporting.value !== null}
              onClick={function () { handleExport('html'); }}
            >
              {exporting.value === 'html' ? 'Exporting...' : 'Export as HTML Bookmarks'}
            </button>
          </div>
        </div>

        <div style={{ marginTop: '32px' }}>
          {u && (
            <p class="settings-detail">
              Logged in as: <strong>{u.email || u.username || 'Unknown'}</strong>
            </p>
          )}
          <button class="btn btn-secondary" onClick={performLogout}>
            Log out
          </button>
        </div>
      </main>
    </>
  );
}
