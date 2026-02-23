import { useState, useEffect, useRef } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { ArticleCard } from '../components/ArticleCard.jsx';
import { Pagination } from '../components/Pagination.jsx';
import { KeyboardShortcutsHelp } from '../components/KeyboardShortcutsHelp.jsx';
import { IconBookOpen, IconHeadphones, IconSelectMode, IconArchive, IconTrash, IconX } from '../components/Icons.jsx';
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
  updateArticle,
  deleteArticle as apiDeleteArticle,
  batchUpdateArticles,
  batchDeleteArticles,
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

const SORT_OPTIONS = [
  { key: 'newest', label: 'Newest first' },
  { key: 'oldest', label: 'Oldest first' },
  { key: 'shortest', label: 'Shortest first' },
  { key: 'longest', label: 'Longest first' },
  { key: 'title_asc', label: 'Title A-Z' },
];

function getSavedSort() {
  try {
    var saved = localStorage.getItem('tasche_sort');
    if (saved && SORT_OPTIONS.some(function (o) { return o.key === saved; })) {
      return saved;
    }
  } catch (e) {
    // localStorage unavailable
  }
  return 'newest';
}

export function Library({ tag }) {
  const [saveUrl, setSaveUrl] = useState('');
  const [listenLater, setListenLater] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  const [showHelp, setShowHelp] = useState(false);
  const [currentSort, setCurrentSort] = useState(getSavedSort);
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const currentFilter = filterSignal.value;
  const articleList = articles.value;
  const isLoading = loadingSignal.value;
  const moreAvailable = hasMoreSignal.value;
  const urlInputRef = useRef(null);

  useEffect(() => {
    articles.value = [];
    offsetSignal.value = 0;
    hasMoreSignal.value = true;
    setSelectedIndex(-1);
    loadArticles(true);
  }, [currentFilter, tag, currentSort]);

  // Reset selectedIndex when article list changes
  useEffect(() => {
    if (selectedIndex >= articleList.length) {
      setSelectedIndex(articleList.length > 0 ? articleList.length - 1 : -1);
    }
  }, [articleList.length]);

  // Keyboard shortcuts
  useEffect(() => {
    function handleKeyDown(e) {
      // Skip if an input, textarea, or select is focused
      var tagName = document.activeElement ? document.activeElement.tagName : '';
      if (tagName === 'INPUT' || tagName === 'TEXTAREA' || tagName === 'SELECT') {
        return;
      }

      // Skip if help overlay is open (except ? and Escape to close it)
      if (showHelp) {
        if (e.key === '?' || e.key === 'Escape') {
          e.preventDefault();
          setShowHelp(false);
        }
        return;
      }

      var list = articles.value;

      if (e.key === 'j') {
        e.preventDefault();
        setSelectedIndex(function (prev) {
          var next = prev + 1;
          if (next >= list.length) return list.length - 1;
          return next;
        });
      } else if (e.key === 'k') {
        e.preventDefault();
        setSelectedIndex(function (prev) {
          var next = prev - 1;
          if (next < 0) return 0;
          return next;
        });
      } else if (e.key === 'o' || e.key === 'Enter') {
        e.preventDefault();
        if (selectedIndex >= 0 && selectedIndex < list.length) {
          window.location.hash = '#/article/' + list[selectedIndex].id;
        }
      } else if (e.key === 'a') {
        e.preventDefault();
        if (selectedIndex >= 0 && selectedIndex < list.length) {
          handleArchiveSelected(list[selectedIndex]);
        }
      } else if (e.key === 's') {
        e.preventDefault();
        if (selectedIndex >= 0 && selectedIndex < list.length) {
          handleFavoriteSelected(list[selectedIndex]);
        }
      } else if (e.key === 'd') {
        e.preventDefault();
        if (selectedIndex >= 0 && selectedIndex < list.length) {
          handleDeleteSelected(list[selectedIndex]);
        }
      } else if (e.key === '/') {
        e.preventDefault();
        window.location.hash = '#/search';
      } else if (e.key === 'n') {
        e.preventDefault();
        if (urlInputRef.current) {
          urlInputRef.current.focus();
        }
      } else if (e.key === '?') {
        e.preventDefault();
        setShowHelp(true);
      }
    }

    window.addEventListener('keydown', handleKeyDown);
    return function () {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [selectedIndex, showHelp]);

  // Scroll selected card into view
  useEffect(() => {
    if (selectedIndex < 0) return;
    var el = document.querySelector('.article-card--selected');
    if (el) {
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [selectedIndex]);

  async function handleArchiveSelected(article) {
    var newStatus = article.reading_status === 'archived' ? 'unread' : 'archived';
    try {
      await updateArticle(article.id, { reading_status: newStatus });
      articles.value = articles.value.map(function (art) {
        return art.id === article.id ? { ...art, reading_status: newStatus } : art;
      });
      addToast(newStatus === 'archived' ? 'Archived' : 'Moved to unread', 'success');
    } catch (err) {
      if (isOffline.value) {
        queueOfflineMutation('/api/articles/' + article.id, 'PATCH', { reading_status: newStatus });
        articles.value = articles.value.map(function (art) {
          return art.id === article.id ? { ...art, reading_status: newStatus } : art;
        });
        addToast('Queued for sync', 'info');
      } else {
        addToast(err.message, 'error');
      }
    }
  }

  async function handleFavoriteSelected(article) {
    var newFav = !article.is_favorite;
    try {
      await updateArticle(article.id, { is_favorite: newFav });
      articles.value = articles.value.map(function (art) {
        return art.id === article.id ? { ...art, is_favorite: newFav ? 1 : 0 } : art;
      });
    } catch (err) {
      if (isOffline.value) {
        queueOfflineMutation('/api/articles/' + article.id, 'PATCH', { is_favorite: newFav });
        articles.value = articles.value.map(function (art) {
          return art.id === article.id ? { ...art, is_favorite: newFav ? 1 : 0 } : art;
        });
        addToast('Queued for sync', 'info');
      } else {
        addToast(err.message, 'error');
      }
    }
  }

  async function handleDeleteSelected(article) {
    if (!confirm('Delete this article?')) return;
    try {
      await apiDeleteArticle(article.id);
      articles.value = articles.value.filter(function (art) { return art.id !== article.id; });
      addToast('Article deleted', 'success');
    } catch (err) {
      addToast(err.message, 'error');
    }
  }

  async function loadArticles(reset) {
    if (loadingSignal.value || (!hasMoreSignal.value && !reset)) return;
    loadingSignal.value = true;

    const currentOffset = reset ? 0 : offsetSignal.value;

    try {
      const params = { limit: limitSignal.value, offset: currentOffset };
      if (currentSort && currentSort !== 'newest') {
        params.sort = currentSort;
      }
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
      var result = await apiCreateArticle(url, null, listenLater);
      if (result && result.updated) {
        var date = result.created_at ? formatDate(result.created_at) : '';
        addToast('Article was already added' + (date ? ' on ' + date : '') + '. Refreshing it now.', 'info');
      } else {
        addToast(listenLater ? 'Article saved! Audio will be generated.' : 'Article saved!', 'success');
      }
      setSaveUrl('');
      setListenLater(false);
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

  function handleSortChange(e) {
    var value = e.target.value;
    setCurrentSort(value);
    try {
      localStorage.setItem('tasche_sort', value);
    } catch (err) {
      // localStorage unavailable
    }
  }

  function toggleSelectMode() {
    if (selectMode) {
      setSelectMode(false);
      setSelected(new Set());
    } else {
      setSelectMode(true);
      setSelected(new Set());
    }
  }

  function handleToggleSelect(articleId) {
    setSelected(function (prev) {
      var next = new Set(prev);
      if (next.has(articleId)) {
        next.delete(articleId);
      } else {
        next.add(articleId);
      }
      return next;
    });
  }

  function handleSelectAll() {
    var allIds = new Set(articleList.map(function (a) { return a.id; }));
    setSelected(allIds);
  }

  function handleClearSelection() {
    setSelected(new Set());
  }

  async function handleBulkArchive() {
    if (selected.size === 0) return;
    var ids = Array.from(selected);
    try {
      await batchUpdateArticles(ids, { reading_status: 'archived' });
      articles.value = articles.value.map(function (art) {
        return selected.has(art.id) ? { ...art, reading_status: 'archived' } : art;
      });
      addToast('Archived ' + ids.length + ' article' + (ids.length === 1 ? '' : 's'), 'success');
      setSelectMode(false);
      setSelected(new Set());
    } catch (err) {
      addToast('Bulk archive failed: ' + err.message, 'error');
    }
  }

  async function handleBulkDelete() {
    if (selected.size === 0) return;
    var count = selected.size;
    if (!confirm('Delete ' + count + ' article' + (count === 1 ? '' : 's') + '?')) return;
    var ids = Array.from(selected);
    try {
      await batchDeleteArticles(ids);
      articles.value = articles.value.filter(function (art) {
        return !selected.has(art.id);
      });
      addToast('Deleted ' + count + ' article' + (count === 1 ? '' : 's'), 'success');
      setSelectMode(false);
      setSelected(new Set());
    } catch (err) {
      addToast('Bulk delete failed: ' + err.message, 'error');
    }
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
                  ref={urlInputRef}
                  class="input"
                  type="url"
                  placeholder="Paste a URL to save..."
                  autocomplete="off"
                  value={saveUrl}
                  onInput={function (e) { setSaveUrl(e.target.value); }}
                  onKeyDown={handleKeyDown}
                />
                <button
                  class={'btn listen-toggle' + (listenLater ? ' listen-toggle--active' : '')}
                  title={listenLater ? 'Audio will be generated' : 'Save & generate audio'}
                  onClick={function () { setListenLater(!listenLater); }}
                  type="button"
                >
                  <IconHeadphones size={16} />
                </button>
                <button class="btn btn-primary" onClick={handleSave}>
                  Save
                </button>
              </div>
            </div>
            <div class="filter-bar">
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
              <select
                class="input input-inline-select"
                value={currentSort}
                onChange={handleSortChange}
              >
                {SORT_OPTIONS.map(function (opt) {
                  return (
                    <option key={opt.key} value={opt.key}>
                      {opt.label}
                    </option>
                  );
                })}
              </select>
              <button
                class={'btn btn-sm' + (selectMode ? ' btn-primary' : ' btn-secondary')}
                onClick={toggleSelectMode}
                title={selectMode ? 'Exit select mode' : 'Select articles'}
              >
                <IconSelectMode size={14} />
                {selectMode ? 'Done' : 'Select'}
              </button>
            </div>
          </>
        )}

        {selectMode && (
          <div class="bulk-action-bar">
            <span class="bulk-action-bar-count">
              {selected.size} selected
            </span>
            <button class="btn btn-sm btn-secondary" onClick={handleSelectAll}>
              Select All
            </button>
            <button class="btn btn-sm btn-secondary" onClick={handleClearSelection} disabled={selected.size === 0}>
              Clear
            </button>
            <button class="btn btn-sm btn-secondary" onClick={handleBulkArchive} disabled={selected.size === 0}>
              <IconArchive size={14} />
              Archive
            </button>
            <button class="btn btn-sm btn-danger" onClick={handleBulkDelete} disabled={selected.size === 0}>
              <IconTrash size={14} />
              Delete
            </button>
            <button class="btn btn-sm btn-secondary bulk-action-bar-close" onClick={toggleSelectMode} title="Exit select mode">
              <IconX size={14} />
            </button>
          </div>
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
          {articleList.map(function (a, index) {
            return (
              <ArticleCard
                key={a.id}
                article={a}
                selected={selectMode ? selected.has(a.id) : index === selectedIndex}
                selectMode={selectMode}
                onToggleSelect={handleToggleSelect}
              />
            );
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
      {showHelp && (
        <KeyboardShortcutsHelp onClose={function () { setShowHelp(false); }} />
      )}
    </>
  );
}
