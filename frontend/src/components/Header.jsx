import { useState, useEffect, useRef } from 'preact/hooks';
import { isOffline, syncStatus, theme, applyTheme, showShortcuts } from '../state.js';
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
} from './Icons.jsx';

export function Header() {
  const offline = isOffline.value;
  const syncing = syncStatus.value;
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef(null);

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
    var current = theme.value;
    var isDark =
      current === 'dark' ||
      (current === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);
    applyTheme(isDark ? 'light' : 'dark');
  }

  function handleShortcuts() {
    setMenuOpen(false);
    showShortcuts.value = true;
  }

  var isDark =
    theme.value === 'dark' ||
    (theme.value === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches);

  return (
    <>
      <header class="header">
        <div class="header-inner">
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
          <div class="header-actions">
            {syncing === 'syncing' && <span class="sync-status">Syncing...</span>}
            <a href="#/search" class="btn btn-icon" title="Search">
              <IconSearch />
            </a>
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
                  <button class="hamburger-item" onClick={toggleTheme}>
                    {isDark ? <IconSun size={16} /> : <IconMoon size={16} />}
                    {isDark ? 'Light mode' : 'Dark mode'}
                  </button>
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
