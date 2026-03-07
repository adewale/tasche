import { useSignal } from '@preact/signals';
import { useEffect, useCallback } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { user, addToast } from '../state.js';
import { useSWMessage } from '../hooks/useSWMessage.js';
import {
  performLogout,
  getCacheStats,
  triggerAutoPrecache,
  clearAllCaches,
  getPreferences,
  updatePreferences,
} from '../api.js';
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
  const autoCacheEnabled = useSignal(localStorage.getItem('tasche-auto-cache') !== 'false');
  const cacheStats = useSignal(null);
  const precaching = useSignal(false);
  const clearing = useSignal(false);
  const ttsVoice = useSignal('athena');
  const voiceLoading = useSignal(false);

  useEffect(function () {
    loadCacheStats();
    getPreferences()
      .then(function (prefs) {
        ttsVoice.value = prefs.tts_voice || 'athena';
      })
      .catch(function () {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useSWMessage(
    useCallback(function (event) {
      if (!event.data) return;
      if (event.data.type === 'AUTO_PRECACHE_COMPLETE') {
        precaching.value = false;
        loadCacheStats();
      } else if (event.data.type === 'AUTO_PRECACHE_ERROR') {
        precaching.value = false;
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []),
  );

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

  return (
    <>
      <Header />
      <main class="main-content">
        <h2 class="section-title">Settings</h2>

        <div class="mt-4">
          <h3 class="section-title">Offline Reading</h3>
          <div class="settings-toggle-row">
            <div class="settings-toggle-info">
              <span class="settings-toggle-label">Auto-cache articles for offline</span>
              <span class="settings-toggle-desc">
                Automatically cache your 20 most recent unread articles so they are available
                offline.
              </span>
            </div>
            <button
              class={'settings-toggle' + (autoCacheEnabled.value ? ' settings-toggle--on' : '')}
              onClick={handleToggleAutoCache}
              role="switch"
              aria-checked={autoCacheEnabled.value}
              aria-label="Auto-cache articles for offline"
            >
              <span class="settings-toggle-knob" />
            </button>
          </div>
          {cacheStats.value && (
            <p class="settings-detail mt-3">
              {cacheStats.value.articleCount === 0
                ? 'No articles cached.'
                : cacheStats.value.articleCount +
                  ' article' +
                  (cacheStats.value.articleCount === 1 ? '' : 's') +
                  ' cached (' +
                  formatBytes(cacheStats.value.totalSize) +
                  ')'}
            </p>
          )}
          <div class="flex-wrap-gap mt-2">
            <button
              class="btn btn-secondary"
              disabled={precaching.value || !navigator.onLine}
              onClick={handlePrecacheNow}
            >
              {precaching.value ? 'Caching...' : 'Cache articles now'}
            </button>
            <button
              class="btn btn-secondary"
              disabled={clearing.value}
              onClick={function () {
                clearing.value = true;
                clearAllCaches()
                  .then(function () {
                    addToast('Caches cleared. Reloading...', 'success');
                    setTimeout(function () {
                      window.location.reload();
                    }, 500);
                  })
                  .catch(function () {
                    clearing.value = false;
                    addToast('Failed to clear caches', 'error');
                  });
              }}
            >
              {clearing.value ? 'Clearing...' : 'Clear cache & reload'}
            </button>
          </div>
        </div>

        <div class="mt-8">
          <h3 class="section-title">Listen Later Voice</h3>
          <p class="settings-detail">Choose the voice for text-to-speech audio generation.</p>
          <div class="voice-picker">
            <button
              class={'btn ' + (ttsVoice.value === 'athena' ? 'btn-primary' : 'btn-secondary')}
              disabled={voiceLoading.value}
              onClick={function () {
                if (ttsVoice.value === 'athena') return;
                voiceLoading.value = true;
                updatePreferences({ tts_voice: 'athena' })
                  .then(function () {
                    ttsVoice.value = 'athena';
                  })
                  .catch(function () {
                    addToast('Failed to update voice preference', 'error');
                  })
                  .finally(function () {
                    voiceLoading.value = false;
                  });
              }}
            >
              Athena (female)
            </button>
            <button
              class={'btn ' + (ttsVoice.value === 'orion' ? 'btn-primary' : 'btn-secondary')}
              disabled={voiceLoading.value}
              onClick={function () {
                if (ttsVoice.value === 'orion') return;
                voiceLoading.value = true;
                updatePreferences({ tts_voice: 'orion' })
                  .then(function () {
                    ttsVoice.value = 'orion';
                  })
                  .catch(function () {
                    addToast('Failed to update voice preference', 'error');
                  })
                  .finally(function () {
                    voiceLoading.value = false;
                  });
              }}
            >
              Orion (male)
            </button>
          </div>
        </div>

        <div class="mt-8">
          <h3 class="section-title">Bookmarklet</h3>
          <p class="bookmarklet-hint">
            Drag this link to your bookmarks bar to save articles from any page.
          </p>
          <a
            href={getBookmarkletCode()}
            class="btn btn-secondary"
            onClick={function (e) {
              e.preventDefault();
            }}
          >
            <IconBookmark /> Save to Tasche
          </a>
        </div>

        <div class="mt-8">
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
