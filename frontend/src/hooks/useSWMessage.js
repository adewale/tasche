import { useEffect } from 'preact/hooks';

export function useSWMessage(handler) {
  useEffect(
    function () {
      if (!('serviceWorker' in navigator)) return;
      navigator.serviceWorker.addEventListener('message', handler);
      return function () {
        navigator.serviceWorker.removeEventListener('message', handler);
      };
    },
    [handler],
  );
}
