import { useEffect } from 'preact/hooks';

export function useKeyboardShortcuts(keyMap, dependencies) {
  useEffect(function () {
    function handleKeyDown(e) {
      var tagName = document.activeElement ? document.activeElement.tagName : '';
      if (tagName === 'INPUT' || tagName === 'TEXTAREA' || tagName === 'SELECT') {
        return;
      }
      var handler = keyMap[e.key];
      if (handler) {
        e.preventDefault();
        handler(e);
      }
    }
    window.addEventListener('keydown', handleKeyDown);
    return function () {
      window.removeEventListener('keydown', handleKeyDown);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, dependencies);
}
