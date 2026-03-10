import { useState, useEffect, useRef } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { EmptyState, LoadingSpinner } from '../components/EmptyState.jsx';
import { ArticleCard } from '../components/ArticleCard.jsx';
import { Pagination } from '../components/Pagination.jsx';
import {
  IconBookOpen,
  IconHeadphones,
  IconSelectMode,
  IconArchive,
  IconTrash,
  IconX,
  IconSearch,
} from '../components/Icons.jsx';
import { useKeyboardShortcuts } from '../hooks/useKeyboardShortcuts.js';
import { toggleArchive, toggleFavorite, removeArticle } from '../articleActions.js';
import { nav, buildTagHash } from '../nav.js';
import {
  articles,
  filter as filterSignal,
  offset as offsetSignal,
  hasMore as hasMoreSignal,
  loading as loadingSignal,
  isOffline,
  addToast,
  pollAudioStatus,
  pollArticleStatus,
  limit as limitSignal,
  showShortcuts,
  searchQuery,
  tags as tagsSignal,
} from '../state.js';
import {
  listArticles,
  getArticle,
  createArticle as apiCreateArticle,
  batchUpdateArticles,
  batchDeleteArticles,
  cacheArticlesForOffline,
  queueOfflineMutation,
} from '../api.js';
import { formatDate } from '../utils.js';

const FILTERS = [
  { key: 'unread', label: 'Unread' },
  { key: 'listen', label: 'Audio' },
  { key: 'favorites', label: 'Favourites' },
  { key: 'archived', label: 'Archived' },
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
    if (
      saved &&
      SORT_OPTIONS.some(function (o) {
        return o.key === saved;
      })
    ) {
      return saved;
    }
  } catch (_e) {
    // localStorage unavailable
  }
  return 'newest';
}

export function Library({ tags, q }) {
  const [saveUrl, setSaveUrl] = useState('');
  const [savingType, setSavingType] = useState(null); // null | 'save' | 'audio'
  const [bulkActing, setBulkActing] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(-1);
  var showHelp = showShortcuts.value;
  const [currentSort, setCurrentSort] = useState(getSavedSort);
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [searchInput, setSearchInput] = useState(q || '');
  const searchInputRef = useRef(null);
  const searchDebounceRef = useRef(null);
  const currentFilter = filterSignal.value;
  const articleList = articles.value;
  const isLoading = loadingSignal.value;
  const moreAvailable = hasMoreSignal.value;
  const urlInputRef = useRef(null);
  const lastLoadTimeRef = useRef(0);
  const hasLoadedOnce = useRef(false);

  // Sync search input when q prop changes (e.g. navigating to #/?q=...)
  useEffect(() => {
    setSearchInput(q || '');
    searchQuery.value = q || '';
  }, [q]);

  useEffect(() => {
    loadingSignal.value = true;
    articles.value = [];
    offsetSignal.value = 0;
    hasMoreSignal.value = true;
    setSelectedIndex(-1);
    loadArticles(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentFilter, tags && tags.join(','), currentSort, q]);

  // Reset selectedIndex when article list changes
  useEffect(() => {
    if (selectedIndex >= articleList.length) {
      setSelectedIndex(articleList.length > 0 ? articleList.length - 1 : -1);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [articleList.length]);

  // Keyboard shortcuts
  // Note: '?' is handled globally in App so it works on all screens.
  useKeyboardShortcuts(
    showHelp
      ? {}
      : {
          j: function () {
            var list = articles.value;
            setSelectedIndex(function (prev) {
              var next = prev + 1;
              return next >= list.length ? list.length - 1 : next;
            });
          },
          k: function () {
            setSelectedIndex(function (prev) {
              var next = prev - 1;
              return next < 0 ? 0 : next;
            });
          },
          o: function () {
            var list = articles.value;
            if (selectedIndex >= 0 && selectedIndex < list.length) {
              nav.article(list[selectedIndex].id);
            }
          },
          Enter: function () {
            var list = articles.value;
            if (selectedIndex >= 0 && selectedIndex < list.length) {
              nav.article(list[selectedIndex].id);
            }
          },
          a: function () {
            var list = articles.value;
            if (selectedIndex >= 0 && selectedIndex < list.length) {
              toggleArchive(list[selectedIndex]);
            }
          },
          s: function () {
            var list = articles.value;
            if (selectedIndex >= 0 && selectedIndex < list.length) {
              toggleFavorite(list[selectedIndex]);
            }
          },
          d: function () {
            var list = articles.value;
            if (selectedIndex >= 0 && selectedIndex < list.length) {
              removeArticle(list[selectedIndex].id);
            }
          },
          '/': function () {
            if (searchInputRef.current) searchInputRef.current.focus();
          },
          n: function () {
            if (urlInputRef.current) urlInputRef.current.focus();
          },
        },
    [selectedIndex, showHelp],
  );

  // Refresh article list when tab becomes visible after 30s+ away
  useEffect(() => {
    function handleVisibility() {
      if (document.visibilityState === 'visible' && Date.now() - lastLoadTimeRef.current > 30000) {
        loadArticles(true);
      }
    }
    document.addEventListener('visibilitychange', handleVisibility);
    return () => document.removeEventListener('visibilitychange', handleVisibility);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentFilter, tags && tags.join(','), currentSort, q]);

  // Scroll selected card into view
  useEffect(() => {
    if (selectedIndex < 0) return;
    var el = document.querySelector('.article-card--checked');
    if (el) {
      el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [selectedIndex]);

  async function loadArticles(reset) {
    if (!reset && (loadingSignal.value || !hasMoreSignal.value)) return;
    loadingSignal.value = true;

    const currentOffset = reset ? 0 : offsetSignal.value;

    try {
      const params = { limit: limitSignal.value, offset: currentOffset };
      if (q) {
        params.q = q;
      }
      if (currentSort && currentSort !== 'newest') {
        params.sort = currentSort;
      }
      if (tags && tags.length > 0) {
        params.tag = tags;
      } else if (currentFilter === 'unread') {
        params.reading_status = 'unread';
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

      lastLoadTimeRef.current = Date.now();
      hasLoadedOnce.current = true;

      const unreadIds = result
        .filter(function (a) {
          return a.reading_status === 'unread';
        })
        .map(function (a) {
          return a.id;
        });
      cacheArticlesForOffline(unreadIds);
    } catch (e) {
      addToast('Could not load articles. Pull down to retry. (' + e.message + ')', 'error');
    } finally {
      loadingSignal.value = false;
    }
  }

  async function handleSave(withAudio) {
    if (savingType) return;
    const url = saveUrl.trim();
    if (!url) {
      addToast('Please enter a URL', 'error');
      return;
    }

    setSavingType(withAudio ? 'audio' : 'save');
    try {
      var result = await apiCreateArticle(url, null, withAudio);
      if (result && result.updated) {
        var date = result.created_at ? formatDate(result.created_at) : '';
        addToast(
          'Article was already added' + (date ? ' on ' + date : '') + '. Refreshing it now.',
          'info',
        );
      } else {
        addToast(
          withAudio ? 'Article saved! Audio will be generated.' : 'Article saved!',
          'success',
        );
      }
      setSaveUrl('');
      articles.value = [];
      offsetSignal.value = 0;
      hasMoreSignal.value = true;
      loadArticles(true);
      if (result && result.id) {
        pollArticleStatus(result.id, getArticle);
        if (withAudio) {
          pollAudioStatus(result.id, getArticle);
        }
      }
    } catch (e) {
      if (isOffline.value) {
        queueOfflineMutation('/api/articles', 'POST', { url: url });
        addToast('Saved offline. Will sync when back online.', 'info');
        setSaveUrl('');
      } else {
        addToast('Could not save article: ' + e.message, 'error');
      }
    } finally {
      setSavingType(null);
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter') handleSave(false);
  }

  function setFilter(key) {
    filterSignal.value = key;
  }

  function handleSortChange(e) {
    var value = e.target.value;
    setCurrentSort(value);
    try {
      localStorage.setItem('tasche_sort', value);
    } catch (_err) {
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
    var allIds = new Set(
      articleList.map(function (a) {
        return a.id;
      }),
    );
    setSelected(allIds);
  }

  function handleClearSelection() {
    setSelected(new Set());
  }

  async function handleBulkArchive() {
    if (selected.size === 0 || bulkActing) return;
    setBulkActing(true);
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
      addToast('Could not archive ' + ids.length + ' articles: ' + err.message, 'error');
    } finally {
      setBulkActing(false);
    }
  }

  async function handleBulkDelete() {
    if (selected.size === 0 || bulkActing) return;
    var count = selected.size;
    if (!confirm('Delete ' + count + ' article' + (count === 1 ? '' : 's') + '?')) return;
    setBulkActing(true);
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
      addToast('Could not delete articles: ' + err.message, 'error');
    } finally {
      setBulkActing(false);
    }
  }

  function handleSearchInput(e) {
    var val = e.target.value;
    setSearchInput(val);
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    var trimmed = val.trim();
    if (trimmed) {
      searchDebounceRef.current = setTimeout(function () {
        nav.search(trimmed);
      }, 300);
    } else if (!val) {
      // Cleared the input — go back to unfiltered library
      if (q) nav.library();
    }
  }

  function handleSearchKeyDown(e) {
    if (e.key === 'Enter') {
      if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
      var trimmed = searchInput.trim();
      if (trimmed) {
        nav.search(trimmed);
      }
    }
    if (e.key === 'Escape') {
      setSearchInput('');
      if (q) nav.library();
      if (searchInputRef.current) searchInputRef.current.blur();
    }
  }

  function clearSearch() {
    setSearchInput('');
    searchQuery.value = '';
    nav.library();
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
        {tags && tags.length > 0 ? (
          <>
            <a href="#/tags" class="reader-back">
              Back to tags
            </a>
            <h2 class="section-title">Articles tagged</h2>
            <div class="tag-filter-bar">
              {tags.map(function (tagId) {
                var tagObj = tagsSignal.value.find(function (t) { return t.id === tagId; });
                var tagName = tagObj ? tagObj.name : tagId;
                return (
                  <span key={tagId} class="tag-filter-chip">
                    {tagName}
                    <button
                      class="tag-filter-chip-remove"
                      title={'Remove tag filter ' + tagName}
                      onClick={function () {
                        var remaining = tags.filter(function (t) { return t !== tagId; });
                        if (remaining.length === 0) {
                          nav.clearTagFilter();
                        } else {
                          window.location.hash = buildTagHash(remaining);
                        }
                      }}
                    >
                      ×
                    </button>
                  </span>
                );
              })}
              {tags.length > 1 && (
                <button class="btn btn-sm btn-secondary" onClick={nav.clearTagFilter}>
                  Clear all
                </button>
              )}
            </div>
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
                  onInput={function (e) {
                    setSaveUrl(e.target.value);
                  }}
                  onKeyDown={handleKeyDown}
                />
                <button
                  class="btn btn-primary"
                  onClick={function () {
                    handleSave(false);
                  }}
                  disabled={!!savingType}
                >
                  {savingType === 'save' ? 'Saving...' : 'Save'}
                </button>
                <button
                  class="btn btn-sm btn-secondary"
                  style="flex-shrink:0"
                  onClick={function () {
                    handleSave(true);
                  }}
                  disabled={!!savingType}
                >
                  <IconHeadphones size={14} />
                  {savingType === 'audio' ? 'Saving...' : 'Save audio'}
                </button>
              </div>
            </div>
            <div class="search-bar">
              <div class="input-group">
                <IconSearch size={16} />
                <input
                  ref={searchInputRef}
                  class="input"
                  type="search"
                  placeholder="Search articles..."
                  value={searchInput}
                  onInput={handleSearchInput}
                  onKeyDown={handleSearchKeyDown}
                />
                {q && (
                  <button class="btn btn-sm btn-secondary" onClick={clearSearch} title="Clear search">
                    <IconX size={14} />
                  </button>
                )}
              </div>
              {q && (
                <div class="search-results-info">
                  Searching for "{q}"
                  {!isLoading && ' — ' + articleList.length + ' result' + (articleList.length !== 1 ? 's' : '')}
                </div>
              )}
            </div>
            <div class="filter-bar">
              <div class="filter-tabs">
                {FILTERS.map(function (f) {
                  return (
                    <button
                      key={f.key}
                      class={'filter-tab' + (currentFilter === f.key ? ' active' : '')}
                      onClick={function () {
                        setFilter(f.key);
                      }}
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
                aria-label="Sort articles"
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
            <span class="bulk-action-bar-count">{selected.size} selected</span>
            <button class="btn btn-sm btn-secondary" onClick={handleSelectAll}>
              Select All
            </button>
            <button
              class="btn btn-sm btn-secondary"
              onClick={handleClearSelection}
              disabled={selected.size === 0}
            >
              Clear
            </button>
            <div class="bulk-action-bar-destructive">
              <button
                class="btn btn-sm btn-secondary"
                onClick={handleBulkArchive}
                disabled={selected.size === 0 || bulkActing}
              >
                <IconArchive size={14} />
                {bulkActing ? 'Archiving...' : 'Archive'}
              </button>
              <button
                class="btn btn-sm btn-danger"
                onClick={handleBulkDelete}
                disabled={selected.size === 0 || bulkActing}
              >
                <IconTrash size={14} />
                {bulkActing ? 'Deleting...' : 'Delete'}
              </button>
              <button
                class="btn btn-sm btn-secondary bulk-action-bar-close"
                onClick={toggleSelectMode}
                title="Exit select mode"
              >
                <IconX size={14} />
              </button>
            </div>
          </div>
        )}

        <div class="article-list">
          {articleList.length === 0 && !isLoading && (
            q ? (
              <EmptyState title="No results found">
                Try a different search query.
              </EmptyState>
            ) : (
              <EmptyState icon={IconBookOpen} title="No articles yet">
                Save a URL above to get started.
              </EmptyState>
            )
          )}
          {articleList.map(function (a, index) {
            return (
              <ArticleCard
                key={a.id}
                article={a}
                selected={selectMode ? selected.has(a.id) : index === selectedIndex}
                selectMode={selectMode}
                onToggleSelect={handleToggleSelect}
                activeTagIds={tags && tags.length > 0 ? new Set(tags) : null}
              />
            );
          })}
        </div>

        {isLoading && articleList.length === 0 && !hasLoadedOnce.current && (
          <div class="article-list">{renderSkeletons()}</div>
        )}

        {isLoading && articleList.length > 0 && <LoadingSpinner />}

        <Pagination
          hasMore={moreAvailable}
          loading={isLoading}
          onLoadMore={function () {
            loadArticles(false);
          }}
        />
      </main>
    </>
  );
}
