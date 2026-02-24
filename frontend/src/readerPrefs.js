/**
 * Reader preferences — signal + localStorage persistence.
 *
 * Preferences are expressed as CSS custom properties applied
 * inline to the reader content container. The CSS rules use
 * var() with fallbacks matching current defaults, so the page
 * looks identical until the user interacts.
 */

import { signal, effect } from '@preact/signals';

var STORAGE_KEY = 'tasche-reader-prefs';

var DEFAULTS = {
  fontSize: 'medium',
  lineHeight: 'comfortable',
  contentWidth: 'medium',
  fontFamily: 'serif',
  theme: 'auto',
  contentMode: 'html',
};

function loadPrefs() {
  try {
    var stored = localStorage.getItem(STORAGE_KEY);
    if (stored) {
      var parsed = JSON.parse(stored);
      return Object.assign({}, DEFAULTS, parsed);
    }
  } catch (e) {
    // Corrupted or missing
  }
  return Object.assign({}, DEFAULTS);
}

export var readerPrefs = signal(loadPrefs());

// Auto-persist on change
effect(function () {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(readerPrefs.value));
  } catch (e) {
    // Storage full or unavailable
  }
});

export function updatePref(key, value) {
  var next = Object.assign({}, readerPrefs.value);
  next[key] = value;
  readerPrefs.value = next;
}

// Maps preference values to CSS custom property values
var PREF_VALUES = {
  fontSize: {
    small:  { '--reader-font-size': '1.0625rem', '--reader-font-size-desktop': '1.125rem' },
    medium: { '--reader-font-size': '1.1875rem', '--reader-font-size-desktop': '1.25rem' },
    large:  { '--reader-font-size': '1.375rem', '--reader-font-size-desktop': '1.4375rem' },
  },
  lineHeight: {
    compact:     { '--reader-line-height': '1.6' },
    comfortable: { '--reader-line-height': '1.8' },
    spacious:    { '--reader-line-height': '2.0' },
  },
  contentWidth: {
    narrow: { '--reader-max-width': '580px' },
    medium: { '--reader-max-width': '680px' },
    wide:   { '--reader-max-width': '800px' },
  },
  fontFamily: {
    serif:        { '--reader-font-family': 'Georgia, "Times New Roman", serif' },
    'sans-serif': { '--reader-font-family': '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif' },
  },
};

export function getReaderStyle(prefs) {
  var style = {};
  for (var key in prefs) {
    var mapping = PREF_VALUES[key];
    if (mapping && mapping[prefs[key]]) {
      Object.assign(style, mapping[prefs[key]]);
    }
  }
  return style;
}
