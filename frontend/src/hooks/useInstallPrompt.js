import { signal } from '@preact/signals';
import { useEffect } from 'preact/hooks';

const DISMISSED_KEY = 'tasche-install-dismissed';

// Reactive state
const deferredPrompt = signal(null);
export const canInstall = signal(false);
export const showIOSHint = signal(false);

// Static detection
const isStandalone =
  typeof window !== 'undefined' &&
  (window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true);

export const isIOS =
  typeof navigator !== 'undefined' &&
  navigator.standalone === undefined &&
  /iPhone|iPad|iPod/.test(navigator.userAgent) &&
  !isStandalone;

function isDismissed() {
  return localStorage.getItem(DISMISSED_KEY) === 'true';
}

export function triggerInstall() {
  const prompt = deferredPrompt.value;
  if (!prompt) return;
  prompt.prompt();
  prompt.userChoice.then(function () {
    deferredPrompt.value = null;
    canInstall.value = false;
  });
}

export function dismissInstall() {
  localStorage.setItem(DISMISSED_KEY, 'true');
  canInstall.value = false;
  showIOSHint.value = false;
}

export function useInstallPrompt() {
  useEffect(function () {
    if (isStandalone) return;

    // iOS hint
    if (isIOS && !isDismissed()) {
      showIOSHint.value = true;
    }

    // Android / desktop
    function onBeforeInstall(e) {
      e.preventDefault();
      deferredPrompt.value = e;
      if (!isDismissed()) {
        canInstall.value = true;
      }
    }

    window.addEventListener('beforeinstallprompt', onBeforeInstall);

    return function () {
      window.removeEventListener('beforeinstallprompt', onBeforeInstall);
    };
  }, []);
}
