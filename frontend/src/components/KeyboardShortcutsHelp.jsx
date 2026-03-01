import { useEffect } from 'preact/hooks';
import { IconX } from './Icons.jsx';

var LIBRARY_SHORTCUTS = [
  { keys: ['j'], description: 'Move selection down' },
  { keys: ['k'], description: 'Move selection up' },
  { keys: ['o', 'Enter'], description: 'Open selected article' },
  { keys: ['a'], description: 'Archive / unarchive selected' },
  { keys: ['s'], description: 'Toggle favourite on selected' },
  { keys: ['d'], description: 'Delete selected article' },
  { keys: ['/'], description: 'Search articles' },
  { keys: ['n'], description: 'Save a new article' },
  { keys: ['?'], description: 'Show / hide this help' },
];

var READER_SHORTCUTS = [
  { keys: ['Esc', 'h'], description: 'Back to library' },
  { keys: ['a'], description: 'Archive / unarchive article' },
  { keys: ['s'], description: 'Toggle favourite' },
  { keys: ['m'], description: 'Cycle view: Original / Rendered / Source' },
];

function ShortcutRow({ shortcut }) {
  return (
    <div class="shortcuts-row">
      <div class="shortcuts-keys">
        {shortcut.keys.map(function (key, i) {
          return (
            <span key={i}>
              {i > 0 && <span class="shortcuts-or">or</span>}
              <kbd class="shortcuts-kbd">{key}</kbd>
            </span>
          );
        })}
      </div>
      <div class="shortcuts-desc">{shortcut.description}</div>
    </div>
  );
}

export function KeyboardShortcutsHelp({ onClose }) {
  useEffect(
    function () {
      function handleEscape(e) {
        if (e.key === 'Escape') {
          e.preventDefault();
          onClose();
        }
      }
      window.addEventListener('keydown', handleEscape);
      return function () {
        window.removeEventListener('keydown', handleEscape);
      };
    },
    [onClose],
  );

  function handleOverlayClick(e) {
    if (e.target === e.currentTarget) {
      onClose();
    }
  }

  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
    <div class="shortcuts-overlay" onClick={handleOverlayClick}>
      <div class="shortcuts-panel">
        <div class="shortcuts-header">
          <h2 class="shortcuts-title">Keyboard Shortcuts</h2>
          <button class="btn btn-icon" onClick={onClose} title="Close">
            <IconX size={18} />
          </button>
        </div>
        <div class="shortcuts-sections">
          <div class="shortcuts-section">
            <h3 class="shortcuts-section-title">Library</h3>
            {LIBRARY_SHORTCUTS.map(function (s, i) {
              return <ShortcutRow key={i} shortcut={s} />;
            })}
          </div>
          <div class="shortcuts-section">
            <h3 class="shortcuts-section-title">Reader</h3>
            {READER_SHORTCUTS.map(function (s, i) {
              return <ShortcutRow key={i} shortcut={s} />;
            })}
          </div>
        </div>
      </div>
    </div>
  );
}
