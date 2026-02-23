import { useState, useEffect, useRef } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { addToast } from '../state.js';
import {
  getFeeds,
  addFeed as apiAddFeed,
  deleteFeed as apiDeleteFeed,
  refreshFeed as apiRefreshFeed,
  refreshAllFeeds as apiRefreshAllFeeds,
  importOPML as apiImportOPML,
} from '../api.js';
import { IconRefresh, IconTrash, IconUpload } from '../components/Icons.jsx';

export function Feeds() {
  var feedsRef = useRef([]);
  var [feeds, setFeeds] = useState([]);
  var [feedUrl, setFeedUrl] = useState('');
  var [isLoading, setIsLoading] = useState(true);
  var [isAdding, setIsAdding] = useState(false);
  var [isRefreshingAll, setIsRefreshingAll] = useState(false);
  var [refreshingIds, setRefreshingIds] = useState({});
  var fileInputRef = useRef(null);

  useEffect(function () {
    loadFeeds();
  }, []);

  async function loadFeeds() {
    setIsLoading(true);
    try {
      var data = await getFeeds();
      feedsRef.current = data;
      setFeeds(data);
    } catch (e) {
      addToast('Failed to load feeds: ' + e.message, 'error');
    } finally {
      setIsLoading(false);
    }
  }

  async function handleAddFeed() {
    var url = feedUrl.trim();
    if (!url) {
      addToast('Enter a feed URL', 'error');
      return;
    }
    setIsAdding(true);
    try {
      var feed = await apiAddFeed(url);
      var newFeeds = [feed].concat(feedsRef.current);
      feedsRef.current = newFeeds;
      setFeeds(newFeeds);
      setFeedUrl('');
      addToast('Feed added: ' + (feed.title || feed.url), 'success');
    } catch (e) {
      addToast(e.message, 'error');
    } finally {
      setIsAdding(false);
    }
  }

  async function handleDeleteFeed(feedId) {
    if (!confirm('Remove this feed subscription?')) return;
    try {
      await apiDeleteFeed(feedId);
      var newFeeds = feedsRef.current.filter(function (f) { return f.id !== feedId; });
      feedsRef.current = newFeeds;
      setFeeds(newFeeds);
      addToast('Feed removed', 'success');
    } catch (e) {
      addToast(e.message, 'error');
    }
  }

  async function handleRefreshFeed(feedId) {
    setRefreshingIds(function (prev) {
      var next = Object.assign({}, prev);
      next[feedId] = true;
      return next;
    });
    try {
      var result = await apiRefreshFeed(feedId);
      var msg = result.new_articles + ' new article' + (result.new_articles !== 1 ? 's' : '');
      addToast('Feed refreshed: ' + msg, 'success');
      loadFeeds();
    } catch (e) {
      addToast('Refresh failed: ' + e.message, 'error');
    } finally {
      setRefreshingIds(function (prev) {
        var next = Object.assign({}, prev);
        delete next[feedId];
        return next;
      });
    }
  }

  async function handleRefreshAll() {
    setIsRefreshingAll(true);
    try {
      var result = await apiRefreshAllFeeds();
      var msg = result.total_new_articles + ' new article' + (result.total_new_articles !== 1 ? 's' : '');
      msg += ' from ' + result.feeds_checked + ' feed' + (result.feeds_checked !== 1 ? 's' : '');
      addToast(msg, 'success');
      loadFeeds();
    } catch (e) {
      addToast('Refresh failed: ' + e.message, 'error');
    } finally {
      setIsRefreshingAll(false);
    }
  }

  function handleImportClick() {
    if (fileInputRef.current) {
      fileInputRef.current.click();
    }
  }

  async function handleFileChange(e) {
    var file = e.target.files && e.target.files[0];
    if (!file) return;

    try {
      var text = await file.text();
      var result = await apiImportOPML(text);
      var msg = 'Imported ' + result.imported + ' feed' + (result.imported !== 1 ? 's' : '');
      if (result.skipped > 0) {
        msg += ', skipped ' + result.skipped + ' duplicate' + (result.skipped !== 1 ? 's' : '');
      }
      addToast(msg, 'success');
      loadFeeds();
    } catch (e2) {
      addToast('Import failed: ' + e2.message, 'error');
    }

    // Reset file input so the same file can be imported again
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter') handleAddFeed();
  }

  function formatDate(dateStr) {
    if (!dateStr) return 'Never';
    try {
      var d = new Date(dateStr);
      return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    } catch (e) {
      return dateStr;
    }
  }

  return (
    <>
      <Header />
      <main class="main-content">
        <div class="feeds-header">
          <h2 class="section-title">Feeds</h2>
          <div class="feeds-header-actions">
            <button
              class="btn btn-sm btn-secondary"
              onClick={handleImportClick}
              title="Import OPML"
            >
              <IconUpload size={14} />
              {' Import OPML'}
            </button>
            <button
              class="btn btn-sm btn-primary"
              onClick={handleRefreshAll}
              disabled={isRefreshingAll || feeds.length === 0}
            >
              <IconRefresh size={14} class={isRefreshingAll ? 'spin' : ''} />
              {isRefreshingAll ? ' Refreshing...' : ' Refresh All'}
            </button>
          </div>
          <input
            type="file"
            ref={fileInputRef}
            accept=".opml,.xml"
            style={{ display: 'none' }}
            onChange={handleFileChange}
          />
        </div>

        <div class="input-group" style={{ marginBottom: '16px' }}>
          <input
            class="input"
            type="url"
            placeholder="Feed URL (RSS or Atom)..."
            value={feedUrl}
            onInput={function (e) { setFeedUrl(e.target.value); }}
            onKeyDown={handleKeyDown}
            disabled={isAdding}
          />
          <button
            class="btn btn-primary"
            onClick={handleAddFeed}
            disabled={isAdding}
          >
            {isAdding ? 'Adding...' : 'Add Feed'}
          </button>
        </div>

        {isLoading && (
          <div class="loading">
            <div class="spinner"></div>
          </div>
        )}

        <div class="feeds-list">
          {!isLoading && feeds.length === 0 && (
            <div class="empty-state">
              <div class="empty-state-title">No feeds yet</div>
              <div class="empty-state-text">
                Subscribe to RSS or Atom feeds to automatically save new articles.
              </div>
            </div>
          )}
          {feeds.map(function (f) {
            var isRefreshing = refreshingIds[f.id];
            return (
              <div class="feed-row" key={f.id}>
                <div class="feed-row-info">
                  <div class="feed-row-title">
                    {f.title || f.url}
                  </div>
                  <div class="feed-row-meta">
                    <span class="feed-row-url" title={f.url}>{f.url}</span>
                    {f.site_url && (
                      <a
                        href={f.site_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        class="feed-row-site"
                      >
                        Site
                      </a>
                    )}
                    <span class="feed-row-fetched">
                      {'Last fetched: ' + formatDate(f.last_fetched_at)}
                    </span>
                  </div>
                </div>
                <div class="feed-row-actions">
                  <button
                    class="btn btn-sm btn-secondary"
                    onClick={function () { handleRefreshFeed(f.id); }}
                    disabled={isRefreshing}
                    title="Refresh feed"
                  >
                    <IconRefresh size={14} class={isRefreshing ? 'spin' : ''} />
                  </button>
                  <button
                    class="btn btn-sm btn-danger"
                    onClick={function () { handleDeleteFeed(f.id); }}
                    title="Remove feed"
                  >
                    <IconTrash size={14} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </main>
    </>
  );
}
