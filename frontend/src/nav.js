/**
 * Parse all library params from a hash string.
 * e.g. '#/?tag=abc&tag=def&q=hello&filter=unread&sort=oldest'
 *   → { tags: ['abc', 'def'], q: 'hello', filter: 'unread', sort: 'oldest' }
 */
export function parseLibraryParams(hash) {
  const idx = hash.indexOf('?');
  if (idx < 0) return { tags: [], q: null, filter: null, sort: null };
  const qs = hash.slice(idx + 1);
  const tags = [];
  let q = null;
  let filter = null;
  let sort = null;
  const parts = qs.split('&');
  for (let i = 0; i < parts.length; i++) {
    const eqIdx = parts[i].indexOf('=');
    if (eqIdx < 0) continue;
    const key = parts[i].slice(0, eqIdx);
    const val = decodeURIComponent(parts[i].slice(eqIdx + 1));
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
 * Build a hash string from library params.
 * e.g. buildLibraryHash({ tags: ['abc'], q: 'hello', filter: 'unread' })
 *   → '#/?tag=abc&q=hello&filter=unread'
 * Omits null/empty values. Returns '#/' when all params are empty.
 */
export function buildLibraryHash(params) {
  const parts = [];
  const tags = params.tags || [];
  for (let i = 0; i < tags.length; i++) {
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
 * Read the current library params from window.location.hash.
 */
function currentParams() {
  return parseLibraryParams(window.location.hash);
}

export const nav = {
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
    const p = currentParams();
    p.q = q || null;
    window.location.hash = buildLibraryHash(p);
  },
  clearSearch: function () {
    const p = currentParams();
    p.q = null;
    window.location.hash = buildLibraryHash(p);
  },
  setFilter: function (filterKey) {
    const p = currentParams();
    p.filter = filterKey || null;
    window.location.hash = buildLibraryHash(p);
  },
  setSort: function (sortKey) {
    const p = currentParams();
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
    const p = currentParams();
    const idx = p.tags.indexOf(tagId);
    if (idx >= 0) {
      p.tags.splice(idx, 1);
    } else {
      if (p.tags.length >= 4) return;
      p.tags.push(tagId);
    }
    window.location.hash = buildLibraryHash(p);
  },
  clearTagFilter: function () {
    const p = currentParams();
    p.tags = [];
    window.location.hash = buildLibraryHash(p);
  },
  login: function () {
    window.location.hash = '#/login';
  },
};
