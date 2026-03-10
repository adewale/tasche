/**
 * Parse all tag= params from a hash string into an array.
 * e.g. '#/?tag=abc&tag=def' → ['abc', 'def']
 */
export function parseTagsFromHash(hash) {
  var idx = hash.indexOf('?');
  if (idx < 0) return [];
  var qs = hash.slice(idx + 1);
  var tags = [];
  var parts = qs.split('&');
  for (var i = 0; i < parts.length; i++) {
    var eqIdx = parts[i].indexOf('=');
    if (eqIdx < 0) continue;
    var key = parts[i].slice(0, eqIdx);
    if (key === 'tag') {
      tags.push(decodeURIComponent(parts[i].slice(eqIdx + 1)));
    }
  }
  return tags;
}

/**
 * Build a hash string from an array of tag IDs, preserving other params.
 * e.g. buildTagHash(['abc', 'def']) → '#/?tag=abc&tag=def'
 *      buildTagHash([], { q: 'hello' }) → '#/?q=hello'
 *      buildTagHash([]) → '#/'
 */
export function buildTagHash(tags, otherParams) {
  var parts = [];
  for (var i = 0; i < tags.length; i++) {
    parts.push('tag=' + encodeURIComponent(tags[i]));
  }
  if (otherParams) {
    var keys = Object.keys(otherParams);
    for (var k = 0; k < keys.length; k++) {
      if (otherParams[keys[k]] != null && otherParams[keys[k]] !== '') {
        parts.push(encodeURIComponent(keys[k]) + '=' + encodeURIComponent(otherParams[keys[k]]));
      }
    }
  }
  if (parts.length === 0) return '#/';
  return '#/?' + parts.join('&');
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
    if (q) {
      window.location.hash = '#/?q=' + encodeURIComponent(q);
    } else {
      window.location.hash = '#/?q=';
    }
  },
  tags: function () {
    window.location.hash = '#/tags';
  },
  tagFilter: function (tagId) {
    var current = parseTagsFromHash(window.location.hash);
    var idx = current.indexOf(tagId);
    if (idx >= 0) {
      current.splice(idx, 1);
    } else {
      if (current.length >= 4) return;
      current.push(tagId);
    }
    if (current.length === 0) {
      window.location.hash = '#/';
    } else {
      window.location.hash = buildTagHash(current);
    }
  },
  clearTagFilter: function () {
    window.location.hash = '#/';
  },
  login: function () {
    window.location.hash = '#/login';
  },
};
