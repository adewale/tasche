import { useState, useEffect } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { ArticleCard } from '../components/ArticleCard.jsx';
import { Pagination } from '../components/Pagination.jsx';
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

const FILTERS = [
  { key: 'all', label: 'All' },
  { key: 'unread', label: 'Unread' },
  { key: 'reading', label: 'Reading' },
  { key: 'archived', label: 'Archived' },
  { key: 'favorites', label: 'Favorites' },
  { key: 'listen', label: '\uD83C\uDFA7' },
];

export function Library({ tag }) {
  const [saveUrl, setSaveUrl] = useState('');
  const currentFilter = filterSignal.value;
  const articleList = articles.value;
  const isLoading = loadingSignal.value;
  const moreAvailable = hasMoreSignal.value;

  // Reset and load articles on mount or when filter/tag changes
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

      // Pre-cache unread articles for offline reading
      const unreadIds = result
        .filter((a) => a.reading_status === 'unread')
        .map((a) => a.id);
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
      await apiCreateArticle(url);
      addToast('Article saved!', 'success');
      setSaveUrl('');
      // Reload list
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

  return (
    <>
      <Header />
      <main class="main-content">
        {tag ? (
          <>
            <a href="#/tags" class="reader-back">
              {'\u2190'} Back to tags
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
                  onInput={(e) => setSaveUrl(e.target.value)}
                  onKeyDown={handleKeyDown}
                />
                <button class="btn btn-primary" onClick={handleSave}>
                  Save
                </button>
              </div>
            </div>
            <div class="filter-tabs">
              {FILTERS.map((f) => (
                <button
                  key={f.key}
                  class={'filter-tab' + (currentFilter === f.key ? ' active' : '')}
                  onClick={() => setFilter(f.key)}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </>
        )}

        <div class="article-list">
          {articleList.length === 0 && !isLoading && (
            <div class="empty-state">
              <div class="empty-state-icon">{'\uD83D\uDCDA'}</div>
              <div class="empty-state-title">No articles yet</div>
              <div class="empty-state-text">Save a URL above to get started.</div>
            </div>
          )}
          {articleList.map((a) => (
            <ArticleCard key={a.id} article={a} />
          ))}
        </div>

        {isLoading && (
          <div class="loading">
            <div class="spinner"></div>
          </div>
        )}

        <Pagination
          hasMore={moreAvailable}
          loading={isLoading}
          onLoadMore={() => loadArticles(false)}
        />
      </main>
    </>
  );
}
