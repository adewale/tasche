/**
 * Parse all library params from a hash string.
 * e.g. '#/?tag=abc&tag=def&q=hello&filter=unread&sort=oldest'
 *   → { tags: ['abc', 'def'], q: 'hello', filter: 'unread', sort: 'oldest' }
 */
export function parseLibraryParams(hash) {
  var idx = hash.indexOf('?');
  if (idx < 0) return { tags: [], q: null, filter: null, sort: null };
  var qs = hash.slice(idx + 1);
  var tags = [];
  var q = null;
  var filter = null;
  var sort = null;
  var parts = qs.split('&');
  for (var i = 0; i < parts.length; i++) {
    var eqIdx = parts[i].indexOf('=');
    if (eqIdx < 0) continue;
    var key = parts[i].slice(0, eqIdx);
    var val = decodeURIComponent(parts[i].slice(eqIdx + 1));
    if (key === 'tag') {
      tags.push(val);
    } else if (key === 'q') {
      q = val || null;
    } else if (key === 'filter') {
      filter = val || null;
    } else if (key === 'sort') {
      sort = val || null;
    }
  }
  return { tags: tags, q: q, filter: filter, sort: sort };
}

/**
 * Parse all tag= params from a hash string into an array.
 * Thin wrapper around parseLibraryParams for backward compatibility.
 */
export function parseTagsFromHash(hash) {
  return parseLibraryParams(hash).tags;
}

/**
 * Build a hash string from library params.
 * e.g. buildLibraryHash({ tags: ['abc'], q: 'hello', filter: 'unread' })
 *   → '#/?tag=abc&q=hello&filter=unread'
 * Omits null/empty values. Returns '#/' when all params are empty.
 */
export function buildLibraryHash(params) {
  var parts = [];
  var tags = params.tags || [];
  for (var i = 0; i < tags.length; i++) {
    parts.push('tag=' + encodeURIComponent(tags[i]));
  }
  if (params.q) {
    parts.push('q=' + encodeURIComponent(params.q));
  }
  if (params.filter) {
    parts.push('filter=' + encodeURIComponent(params.filter));
  }
  if (params.sort) {
    parts.push('sort=' + encodeURIComponent(params.sort));
  }
  if (parts.length === 0) return '#/';
  return '#/?' + parts.join('&');
}

/**
 * Backward-compatible alias for buildLibraryHash.
 */
export function buildTagHash(tags, otherParams) {
  var params = { tags: tags };
  if (otherParams) {
    if (otherParams.q) params.q = otherParams.q;
    if (otherParams.filter) params.filter = otherParams.filter;
    if (otherParams.sort) params.sort = otherParams.sort;
  }
  return buildLibraryHash(params);
}

/**
 * Read the current library params from window.location.hash.
 */
function currentParams() {
  return parseLibraryParams(window.location.hash);
}

export var nav = {
  library: function () {
    window.location.hash = '#/';
  },
  article: function (id) {
    window.location.hash = '#/article/' + id;
  },
  articleMarkdown: function (id) {
    window.location.hash = '#/article/' + id + '/markdown';
  },
  search: function (q) {
    var p = currentParams();
    p.q = q || null;
    window.location.hash = buildLibraryHash(p);
  },
  clearSearch: function () {
    var p = currentParams();
    p.q = null;
    window.location.hash = buildLibraryHash(p);
  },
  setFilter: function (filterKey) {
    var p = currentParams();
    p.filter = filterKey || null;
    window.location.hash = buildLibraryHash(p);
  },
  setSort: function (sortKey) {
    var p = currentParams();
    p.sort = sortKey || null;
    window.location.hash = buildLibraryHash(p);
    try {
      localStorage.setItem('tasche_sort', sortKey || 'newest');
    } catch (_e) {
      // localStorage unavailable
    }
  },
  tags: function () {
    window.location.hash = '#/tags';
  },
  tagFilter: function (tagId) {
    var p = currentParams();
    var idx = p.tags.indexOf(tagId);
    if (idx >= 0) {
      p.tags.splice(idx, 1);
    } else {
      if (p.tags.length >= 4) return;
      p.tags.push(tagId);
    }
    window.location.hash = buildLibraryHash(p);
  },
  clearTagFilter: function () {
    var p = currentParams();
    p.tags = [];
    window.location.hash = buildLibraryHash(p);
  },
  login: function () {
    window.location.hash = '#/login';
  },
};
