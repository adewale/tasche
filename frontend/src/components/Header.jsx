import { useState, useEffect, useRef } from 'preact/hooks';
import { isOffline, syncStatus, theme, applyTheme, showShortcuts } from '../state.js';
import {
  IconSearch,
  IconTag,
  IconSettings,
  IconBarChart,
  IconHelpCircle,
  IconKeyboard,
  IconMoon,
  IconSun,
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
            <a href="#/stats" class="btn btn-icon" title="Stats">
              <IconBarChart />
            </a>
            <a href="#/tags" class="btn btn-icon" title="Tags">
              <IconTag />
            </a>
            <a href="#/settings" class="btn btn-icon" title="Settings">
              <IconSettings />
            </a>
            <div class="help-menu" ref={menuRef}>
              <button
                class="btn btn-icon"
                title="Help"
                onClick={function () {
                  setMenuOpen(!menuOpen);
                }}
              >
                <IconHelpCircle />
              </button>
              {menuOpen && (
                <div class="help-menu-dropdown">
                  <button class="help-menu-item" onClick={handleShortcuts}>
                    <IconKeyboard size={16} />
                    Keyboard shortcuts
                  </button>
                  <button class="help-menu-item" onClick={toggleTheme}>
                    {isDark ? <IconSun size={16} /> : <IconMoon size={16} />}
                    {isDark ? 'Light mode' : 'Dark mode'}
                  </button>
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
