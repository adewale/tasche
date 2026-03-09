/**
 * Utility functions shared across the app.
 */

export function escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

export function formatDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const now = new Date();
  const diff = now - d;
  const mins = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days = Math.floor(diff / 86400000);

  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  if (hours < 24) return hours + 'h ago';
  if (days < 7) return days + 'd ago';

  return d.toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: d.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
  });
}

export function formatTime(seconds) {
  if (seconds == null || isNaN(seconds)) return '0:00';
  if (!isFinite(seconds)) return '--:--';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return m + ':' + (s < 10 ? '0' : '') + s;
}

/**
 * Split text into segments with highlighting information.
 * Returns an array of { text, highlighted } objects.
 * Matches whole words or word prefixes (case-insensitive).
 */
export function highlightTerms(text, query) {
  if (!text || !query) return [{ text: text || '', highlighted: false }];

  var terms = query
    .trim()
    .split(/\s+/)
    .filter(function (t) {
      return t.length > 0;
    });

  if (terms.length === 0) return [{ text: text, highlighted: false }];

  // Escape regex special chars in each term, match at word boundary
  var escaped = terms.map(function (t) {
    return t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  });

  // Match word boundary + any term (prefix matching)
  var pattern = new RegExp('(\\b(?:' + escaped.join('|') + ')\\w*)', 'gi');
  var segments = [];
  var lastIndex = 0;
  var match;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      segments.push({ text: text.slice(lastIndex, match.index), highlighted: false });
    }
    segments.push({ text: match[0], highlighted: true });
    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    segments.push({ text: text.slice(lastIndex), highlighted: false });
  }

  return segments.length > 0 ? segments : [{ text: text, highlighted: false }];
}

export function getBookmarkletCode() {
  var origin = window.location.origin;
  return (
    "javascript:void(open('" +
    origin +
    "/bookmarklet?url='+encodeURIComponent(location.href)+'&title='+encodeURIComponent(document.title),'" +
    "Tasche','toolbar=no,width=420,height=480'))"
  );
}
