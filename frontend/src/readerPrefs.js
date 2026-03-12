/**
 * Reader preferences — signal + localStorage persistence.
 *
 * Preferences are expressed as CSS custom properties applied
 * inline to the reader content container. The CSS rules use
 * var() with fallbacks matching current defaults, so the page
 * looks identical until the user interacts.
 */

import { signal, effect } from '@preact/signals';

const STORAGE_KEY = 'tasche-reader-prefs';

const DEFAULTS = {
  fontSize: 'medium',
  lineHeight: 'comfortable',
  contentWidth: 'medium',
  fontFamily: 'serif',
  theme: 'auto',
  contentMode: 'html',
  immersive: 'on',
};

function loadPrefs() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      return Object.assign({}, DEFAULTS, parsed);
    }
  } catch (_e) {
    // Corrupted or missing
  }
  return Object.assign({}, DEFAULTS);
}

export const readerPrefs = signal(loadPrefs());

// Auto-persist on change
effect(function () {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(readerPrefs.value));
  } catch (_e) {
    // Storage full or unavailable
  }
});

export function updatePref(key, value) {
  const next = Object.assign({}, readerPrefs.value);
  next[key] = value;
  readerPrefs.value = next;
}

// Maps preference values to CSS custom property values
const PREF_VALUES = {
  fontSize: {
    small: { '--reader-font-size': '1.0625rem', '--reader-font-size-desktop': '1.125rem' },
    medium: { '--reader-font-size': '1.1875rem', '--reader-font-size-desktop': '1.25rem' },
    large: { '--reader-font-size': '1.375rem', '--reader-font-size-desktop': '1.4375rem' },
  },
  lineHeight: {
    compact: { '--reader-line-height': '1.6' },
    comfortable: { '--reader-line-height': '1.8' },
    spacious: { '--reader-line-height': '2.0' },
  },
  contentWidth: {
    narrow: { '--reader-max-width': '580px' },
    medium: { '--reader-max-width': '680px' },
    wide: { '--reader-max-width': '800px' },
  },
  fontFamily: {
    serif: { '--reader-font-family': 'Georgia, "Times New Roman", serif' },
    'sans-serif': {
      '--reader-font-family':
        '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
    },
  },
};

export function getReaderStyle(prefs) {
  const style = {};
  for (const key in prefs) {
    const mapping = PREF_VALUES[key];
    if (mapping && mapping[prefs[key]]) {
      Object.assign(style, mapping[prefs[key]]);
    }
  }
  return style;
}
