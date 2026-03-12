import { useState, useEffect, useRef } from 'preact/hooks';
import { isOffline, syncStatus, theme, applyTheme, showShortcuts } from '../state.js';
import { readerPrefs, updatePref } from '../readerPrefs.js';
import { parseLibraryParams, nav } from '../nav.js';
import {
  IconLogo,
  IconSearch,
  IconTag,
  IconSettings,
  IconBarChart,
  IconMenu,
  IconKeyboard,
  IconMoon,
  IconSun,
  IconPencil,
  IconX,
} from './Icons.jsx';

const READER_THEME_OPTIONS = [
  { value: 'auto', label: 'Auto' },
  { value: 'light', label: 'Light' },
  { value: 'sepia', label: 'Sepia' },
  { value: 'dark', label: 'Dark' },
];

function isLibraryRoute(hash) {
  const path = hash.slice(1) || '/';
  return path === '/' || path === '' || (path.charAt(0) === '/' && path.charAt(1) === '?');
}

export function Header({ readerMode }) {
  const offline = isOffline.value;
  const syncing = syncStatus.value;
  const [menuOpen, setMenuOpen] = useState(false);
  const [searchEnabled, setSearchEnabled] = useState(function () {
    return isLibraryRoute(window.location.hash);
  });
  const [searchInput, setSearchInput] = useState(function () {
    const p = parseLibraryParams(window.location.hash);
    return p.q || '';
  });
  const [searchOpen, setSearchOpen] = useState(function () {
    return !!parseLibraryParams(window.location.hash).q;
  });
  const searchInputRef = useRef(null);
  const searchDebounceRef = useRef(null);
  const menuRef = useRef(null);

  // Track route changes to enable/disable search and sync input
  useEffect(function () {
    function onHashChange() {
      const hash = window.location.hash;
      setSearchEnabled(isLibraryRoute(hash));
      const p = parseLibraryParams(hash);
      setSearchInput(p.q || '');
      if (!isLibraryRoute(hash)) {
        setSearchOpen(false);
      }
    }
    window.addEventListener('hashchange', onHashChange);
    return function () {
      window.removeEventListener('hashchange', onHashChange);
    };
  }, []);

  // Global "/" shortcut to focus search
  useEffect(function () {
    function handleSlash(e) {
      if (!isLibraryRoute(window.location.hash)) return;
      const tag = document.activeElement ? document.activeElement.tagName : '';
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;
      if (e.key === '/' && !e.shiftKey && !e.ctrlKey && !e.metaKey) {
        e.preventDefault();
        setSearchOpen(true);
        if (searchInputRef.current) searchInputRef.current.focus();
      }
    }
    window.addEventListener('keydown', handleSlash);
    return function () {
      window.removeEventListener('keydown', handleSlash);
    };
  }, []);

  // Focus input when search opens
  useEffect(
    function () {
      if (searchOpen && searchInputRef.current) {
        searchInputRef.current.focus();
      }
    },
    [searchOpen],
  );

  // Clean up debounce on unmount
  useEffect(function () {
    return function () {
      if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    };
  }, []);

  useEffect(
    function () {
      if (!menuOpen) return;
      function handleClick(e) {
        if (menuRef.current && !menuRef.current.contains(e.target)) {
          setMenuOpen(false);
        }
      }
      document.addEventListener('click', handleClick);
      return function () {
        document.removeEventListener('click', handleClick);
      };
    },
    [menuOpen],
  );

  function toggleTheme() {
    const current = theme.value;
    const isDark =
      current === 'dark' ||
      (current === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    applyTheme(isDark ? 'light' : 'dark');
  }

  function handleShortcuts() {
    setMenuOpen(false);
    showShortcuts.value = true;
  }

  function handleSearchInput(e) {
    const val = e.target.value;
    setSearchInput(val);
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
    const trimmed = val.trim();
    if (trimmed) {
      searchDebounceRef.current = setTimeout(function () {
        nav.search(trimmed);
      }, 300);
    } else if (!val) {
      const p = parseLibraryParams(window.location.hash);
      if (p.q) nav.clearSearch();
    }
  }

  function handleSearchKeyDown(e) {
    if (e.key === 'Enter') {
      if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current);
      const trimmed = searchInput.trim();
      if (trimmed) {
        nav.search(trimmed);
      }
    }
    if (e.key === 'Escape') {
      setSearchInput('');
      const p = parseLibraryParams(window.location.hash);
      if (p.q) nav.clearSearch();
      if (searchInputRef.current) searchInputRef.current.blur();
      setSearchOpen(false);
    }
  }

  function clearSearch() {
    setSearchInput('');
    nav.clearSearch();
    setSearchOpen(false);
  }

  const isDark =
    theme.value === 'dark' ||
    (theme.value === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);

  const hasActiveQuery = parseLibraryParams(window.location.hash).q;

  return (
    <>
      <header class="header">
        <div class={'header-inner' + (searchOpen ? ' header-inner--search-open' : '')}>
          <a href="#/" class="header-logo">
            <IconLogo size={28} />
            Tasche
            {offline && (
              <span
                class="offline-badge offline-badge--offline"
                title="Offline"
                role="status"
                aria-label="Offline"
              ></span>
            )}
            {!offline && syncing === 'syncing' && (
              <span
                class="offline-badge offline-badge--syncing"
                title="Syncing..."
                role="status"
                aria-label="Syncing"
              ></span>
            )}
          </a>
          {searchEnabled && (
            <div class={'header-search' + (searchOpen ? ' header-search--open' : '')}>
              <input
                ref={searchInputRef}
                class="input header-search-input"
                type="search"
                placeholder="Search articles..."
                value={searchInput}
                onInput={handleSearchInput}
                onKeyDown={handleSearchKeyDown}
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
              />
              {hasActiveQuery && (
                <button class="header-search-clear" onClick={clearSearch} title="Clear search">
                  <IconX size={14} />
                </button>
              )}
            </div>
          )}
          <div class="header-actions">
            {syncing === 'syncing' && <span class="sync-status">Syncing...</span>}
            {searchEnabled && (
              <button
                class="btn btn-icon"
                title="Search"
                onClick={function () {
                  if (searchOpen) {
                    clearSearch();
                  } else {
                    setSearchOpen(true);
                  }
                }}
              >
                {searchOpen ? <IconX /> : <IconSearch />}
              </button>
            )}
            <div class="hamburger-menu" ref={menuRef}>
              <button
                class="btn btn-icon"
                title="Menu"
                onClick={function () {
                  setMenuOpen(!menuOpen);
                }}
              >
                <IconMenu />
              </button>
              {menuOpen && (
                <div class="hamburger-dropdown">
                  <a
                    class="hamburger-item"
                    href="#/tags"
                    onClick={function () {
                      setMenuOpen(false);
                    }}
                  >
                    <IconTag size={16} />
                    Tags
                  </a>
                  <a
                    class="hamburger-item"
                    href="#/stats"
                    onClick={function () {
                      setMenuOpen(false);
                    }}
                  >
                    <IconBarChart size={16} />
                    Stats
                  </a>
                  <a
                    class="hamburger-item"
                    href="#/settings"
                    onClick={function () {
                      setMenuOpen(false);
                    }}
                  >
                    <IconSettings size={16} />
                    Settings
                  </a>
                  {readerMode ? (
                    <div class="hamburger-theme-group">
                      <span class="hamburger-theme-label">Reader theme</span>
                      <div class="hamburger-theme-options">
                        {READER_THEME_OPTIONS.map(function (opt) {
                          return (
                            <button
                              key={opt.value}
                              class={
                                'hamburger-theme-btn' +
                                (readerPrefs.value.theme === opt.value ? ' active' : '')
                              }
                              onClick={function () {
                                updatePref('theme', opt.value);
                                setMenuOpen(false);
                              }}
                            >
                              {opt.label}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ) : (
                    <button class="hamburger-item" onClick={toggleTheme}>
                      {isDark ? <IconSun size={16} /> : <IconMoon size={16} />}
                      {isDark ? 'Light mode' : 'Dark mode'}
                    </button>
                  )}
                  <button class="hamburger-item" onClick={handleShortcuts}>
                    <IconKeyboard size={16} />
                    Keyboard shortcuts
                  </button>
                  <a
                    class="hamburger-item"
                    href="/design-language.html"
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={function () {
                      setMenuOpen(false);
                    }}
                  >
                    <IconPencil size={16} />
                    Design language
                  </a>
                </div>
              )}
            </div>
          </div>
        </div>
      </header>
      <div class={'offline-bar' + (offline ? ' visible' : '')}>
        You are offline. Some features may be unavailable.
      </div>
    </>
  );
}
