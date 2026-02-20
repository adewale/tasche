import { useState, useEffect } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { ArticleCard } from '../components/ArticleCard.jsx';
import { Pagination } from '../components/Pagination.jsx';
import { IconBookOpen } from '../components/Icons.jsx';
import {
  articles,
  filter as filterSignal,
  offset as offsetSignal,
  hasMore as hasMoreSignal,
  loading as loadingSignal,
  isOffline,
  addToast,
  limit as limitSignal,
} from '../state.js';
import {
  listArticles,
  createArticle as apiCreateArticle,
  cacheArticlesForOffline,
  queueOfflineMutation,
} from '../api.js';
import { formatDate } from '../utils.js';

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'unread', label: 'Unread' },
  { key: 'reading', label: 'Reading' },
  { key: 'archived', label: 'Archived' },
  { key: 'favorites', label: 'Favorites' },
  { key: 'listen', label: 'Audio' },
];

export function Library({ tag }) {
  const [saveUrl, setSaveUrl] = useState('');
  const currentFilter = filterSignal.value;
  const articleList = articles.value;
  const isLoading = loadingSignal.value;
  const moreAvailable = hasMoreSignal.value;

  useEffect(() => {
    articles.value = [];
    offsetSignal.value = 0;
    hasMoreSignal.value = true;
    loadArticles(true);
  }, [currentFilter, tag]);

  async function loadArticles(reset) {
    if (loadingSignal.value || (!hasMoreSignal.value && !reset)) return;
    loadingSignal.value = true;

    const currentOffset = reset ? 0 : offsetSignal.value;

    try {
      const params = { limit: limitSignal.value, offset: currentOffset };
      if (tag) {
        params.tag = tag;
      } else if (currentFilter === 'unread') {
        params.reading_status = 'unread';
      } else if (currentFilter === 'reading') {
        params.reading_status = 'reading';
      } else if (currentFilter === 'archived') {
        params.reading_status = 'archived';
      } else if (currentFilter === 'favorites') {
        params.is_favorite = 1;
      } else if (currentFilter === 'listen') {
        params.audio_status = 'ready';
      }

      const result = await listArticles(params);
      if (reset) {
        articles.value = result;
      } else {
        articles.value = [...articles.value, ...result];
      }
      offsetSignal.value = currentOffset + result.length;
      hasMoreSignal.value = result.length >= limitSignal.value;

      const unreadIds = result
        .filter(function (a) { return a.reading_status === 'unread'; })
        .map(function (a) { return a.id; });
      cacheArticlesForOffline(unreadIds);
    } catch (e) {
      addToast('Failed to load articles: ' + e.message, 'error');
    } finally {
      loadingSignal.value = false;
    }
  }

  async function handleSave() {
    const url = saveUrl.trim();
    if (!url) {
      addToast('Please enter a URL', 'error');
      return;
    }

    try {
      var result = await apiCreateArticle(url);
      if (result && result.updated) {
        var date = result.created_at ? formatDate(result.created_at) : '';
        addToast('Article was already added' + (date ? ' on ' + date : '') + '. Refreshing it now.', 'info');
      } else {
        addToast('Article saved!', 'success');
      }
      setSaveUrl('');
      articles.value = [];
      offsetSignal.value = 0;
      hasMoreSignal.value = true;
      loadArticles(true);
    } catch (e) {
      if (isOffline.value) {
        queueOfflineMutation('/api/articles', 'POST', { url: url });
        addToast('Saved offline. Will sync when back online.', 'info');
        setSaveUrl('');
      } else {
        addToast(e.message, 'error');
      }
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter') handleSave();
  }

  function setFilter(key) {
    filterSignal.value = key;
  }

  function renderSkeletons() {
    return Array.from({ length: 3 }, function (_, i) {
      return (
        <div class="skeleton-card" key={'skel-' + i}>
          <div class="skeleton skeleton-thumbnail"></div>
          <div class="skeleton-lines">
            <div class="skeleton skeleton-line"></div>
            <div class="skeleton skeleton-line"></div>
            <div class="skeleton skeleton-line"></div>
          </div>
        </div>
      );
    });
  }

  return (
    <>
      <Header />
      <main class="main-content">
        {tag ? (
          <>
            <a href="#/tags" class="reader-back">
              Back to tags
            </a>
            <h2 class="section-title">Articles tagged</h2>
          </>
        ) : (
          <>
            <div class="save-form">
              <div class="input-group">
                <input
                  class="input"
                  type="url"
                  placeholder="Paste a URL to save..."
                  autocomplete="off"
                  value={saveUrl}
                  onInput={function (e) { setSaveUrl(e.target.value); }}
                  onKeyDown={handleKeyDown}
                />
                <button class="btn btn-primary" onClick={handleSave}>
                  Save
                </button>
              </div>
            </div>
            <div class="filter-tabs">
              {FILTERS.map(function (f) {
                return (
                  <button
                    key={f.key}
                    class={'filter-tab' + (currentFilter === f.key ? ' active' : '')}
                    onClick={function () { setFilter(f.key); }}
                  >
                    {f.label}
                  </button>
                );
              })}
            </div>
          </>
        )}

        <div class="article-list">
          {articleList.length === 0 && !isLoading && (
            <div class="empty-state">
              <div class="empty-state-icon">
                <IconBookOpen />
              </div>
              <div class="empty-state-title">No articles yet</div>
              <div class="empty-state-text">Save a URL above to get started.</div>
            </div>
          )}
          {articleList.map(function (a) {
            return <ArticleCard key={a.id} article={a} />;
          })}
        </div>

        {isLoading && articleList.length === 0 && (
          <div class="article-list">{renderSkeletons()}</div>
        )}

        {isLoading && articleList.length > 0 && (
          <div class="loading">
            <div class="spinner"></div>
          </div>
        )}

        <Pagination
          hasMore={moreAvailable}
          loading={isLoading}
          onLoadMore={function () { loadArticles(false); }}
        />
      </main>
    </>
  );
}
