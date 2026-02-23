import { useState, useEffect, useRef } from 'preact/hooks';
import { Header } from '../components/Header.jsx';
import { EmptyState, LoadingSpinner } from '../components/EmptyState.jsx';
import { searchResults, searchQuery, addToast } from '../state.js';
import { searchArticles } from '../api.js';
import { nav } from '../nav.js';
import { formatDate, highlightTerms } from '../utils.js';

function HighlightedText({ text, query }) {
  var segments = highlightTerms(text, query);
  return segments.map(function (seg, i) {
    if (seg.highlighted) {
      return <mark key={i}>{seg.text}</mark>;
    }
    return seg.text;
  });
}

export function Search() {
  const [query, setQuery] = useState(searchQuery.value);
  const [results, setResults] = useState(searchResults.value);
  const [info, setInfo] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const debounceRef = useRef(null);

  useEffect(() => {
    // Auto-search if there's a query on mount
    if (searchQuery.value) {
      performSearch(searchQuery.value);
    }
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  async function performSearch(q) {
    if (!q) {
      addToast('Enter a search query', 'error');
      return;
    }
    searchQuery.value = q;
    setIsLoading(true);
    setResults([]);
    setInfo('');

    try {
      const data = await searchArticles(q);
      searchResults.value = data;
      setResults(data);
      setInfo(data.length + ' result' + (data.length !== 1 ? 's' : '') + ' for "' + q + '"');
    } catch (e) {
      addToast('Search failed: ' + e.message, 'error');
    } finally {
      setIsLoading(false);
    }
  }

  function doSearch() {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const q = query.trim();
    performSearch(q);
  }

  function handleInput(e) {
    const val = e.target.value;
    setQuery(val);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const trimmed = val.trim();
    if (trimmed) {
      debounceRef.current = setTimeout(() => {
        performSearch(trimmed);
      }, 300);
    }
  }

  function handleKeyDown(e) {
    if (e.key === 'Enter') doSearch();
  }

  function navigateToArticle(id) {
    nav.article(id);
  }

  var currentQuery = searchQuery.value;

  return (
    <>
      <Header />
      <main class="main-content">
        <h2 class="section-title">Search</h2>
        <div class="search-container">
          <div class="input-group">
            <input
              class="input"
              type="search"
              placeholder="Search articles..."
              value={query}
              onInput={handleInput}
              onKeyDown={handleKeyDown}
              autofocus
            />
            <button class="btn btn-primary" onClick={doSearch}>
              Search
            </button>
          </div>
        </div>

        {info && <div class="search-results-info">{info}</div>}

        <div class="article-list">
          {results.length === 0 && !isLoading && info && (
            <EmptyState title="No results found">
              Try a different search query.
            </EmptyState>
          )}
          {results.map((a) => (
            <div
              key={a.id}
              class="article-card"
              onClick={() => navigateToArticle(a.id)}
            >
              <div class="article-card-title">
                <HighlightedText text={a.title || a.original_url} query={currentQuery} />
              </div>
              <div class="article-card-meta">
                {a.domain && (
                  <span class="article-card-domain">
                    <img
                      class="favicon"
                      src={'https://www.google.com/s2/favicons?domain=' + a.domain + '&sz=16'}
                      alt=""
                      width="14"
                      height="14"
                      loading="lazy"
                      onError={function (e) { e.target.style.display = 'none'; }}
                    />
                    {a.domain}
                  </span>
                )}
                <span>{formatDate(a.created_at)}</span>
              </div>
              {a.excerpt && (
                <div class="article-card-excerpt">
                  <HighlightedText text={a.excerpt} query={currentQuery} />
                </div>
              )}
            </div>
          ))}
        </div>

        {isLoading && <LoadingSpinner />}
      </main>
    </>
  );
}
