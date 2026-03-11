import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import {
  parseTagsFromHash,
  parseLibraryParams,
  buildTagHash,
  buildLibraryHash,
} from '../../src/nav.js';

// ---------------------------------------------------------------------------
// Arbitraries
// ---------------------------------------------------------------------------

// URL-safe tag IDs (non-empty, no &, no =, no #, no ?)
var tagIdArb = fc
  .array(
    fc.oneof(
      fc.constantFrom('a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'x', 'y', 'z'),
      fc.constantFrom('0', '1', '2', '3', '4', '5', '6', '7', '8', '9'),
      fc.constantFrom('-', '_'),
    ),
    { minLength: 1, maxLength: 30 },
  )
  .map(function (chars) {
    return chars.join('');
  });

var filterArb = fc.constantFrom('unread', 'archived', 'favorites', 'listen', null);
var sortArb = fc.constantFrom('newest', 'oldest', 'shortest', 'longest', 'title_asc', null);
var queryArb = fc.oneof(
  fc.constant(null),
  fc
    .array(fc.constantFrom('a', 'b', 'c', 'test', 'hello', 'world'), {
      minLength: 1,
      maxLength: 3,
    })
    .map(function (words) {
      return words.join(' ');
    }),
);

// Full library params arbitrary
var libraryParamsArb = fc.record({
  tags: fc.array(tagIdArb, { minLength: 0, maxLength: 4 }),
  q: queryArb,
  filter: filterArb,
  sort: sortArb,
});

// ---------------------------------------------------------------------------
// parseTagsFromHash (backward compat)
// ---------------------------------------------------------------------------
describe('parseTagsFromHash', function () {
  it('returns empty array for empty string', function () {
    expect(parseTagsFromHash('')).toEqual([]);
  });

  it('returns empty array for hash with no query', function () {
    expect(parseTagsFromHash('#/')).toEqual([]);
  });

  it('returns empty array when no tag params present', function () {
    expect(parseTagsFromHash('#/?q=hello')).toEqual([]);
  });

  it('parses single tag', function () {
    expect(parseTagsFromHash('#/?tag=abc')).toEqual(['abc']);
  });

  it('parses two tags', function () {
    expect(parseTagsFromHash('#/?tag=abc&tag=def')).toEqual(['abc', 'def']);
  });

  it('parses tags interleaved with other params', function () {
    expect(parseTagsFromHash('#/?tag=abc&q=hello&tag=def')).toEqual(['abc', 'def']);
  });

  it('decodes URL-encoded tag IDs', function () {
    expect(parseTagsFromHash('#/?tag=a%20b')).toEqual(['a b']);
  });

  it('handles tag with special characters', function () {
    expect(parseTagsFromHash('#/?tag=c%2B%2B&tag=c%23')).toEqual(['c++', 'c#']);
  });

  it('returns empty array for malformed query with no = sign', function () {
    expect(parseTagsFromHash('#/?tagfoo')).toEqual([]);
  });

  it('parses four tags (maximum supported)', function () {
    expect(parseTagsFromHash('#/?tag=a&tag=b&tag=c&tag=d')).toEqual(['a', 'b', 'c', 'd']);
  });
});

// ---------------------------------------------------------------------------
// parseLibraryParams
// ---------------------------------------------------------------------------
describe('parseLibraryParams', function () {
  it('returns defaults for empty string', function () {
    expect(parseLibraryParams('')).toEqual({ tags: [], q: null, filter: null, sort: null });
  });

  it('returns defaults for bare hash', function () {
    expect(parseLibraryParams('#/')).toEqual({ tags: [], q: null, filter: null, sort: null });
  });

  it('parses q param', function () {
    var p = parseLibraryParams('#/?q=hello');
    expect(p.q).toBe('hello');
    expect(p.tags).toEqual([]);
    expect(p.filter).toBeNull();
  });

  it('parses filter param', function () {
    var p = parseLibraryParams('#/?filter=archived');
    expect(p.filter).toBe('archived');
  });

  it('parses sort param', function () {
    var p = parseLibraryParams('#/?sort=oldest');
    expect(p.sort).toBe('oldest');
  });

  it('parses all params together', function () {
    var p = parseLibraryParams('#/?tag=abc&tag=def&q=hello&filter=unread&sort=oldest');
    expect(p.tags).toEqual(['abc', 'def']);
    expect(p.q).toBe('hello');
    expect(p.filter).toBe('unread');
    expect(p.sort).toBe('oldest');
  });

  it('treats empty q as null', function () {
    var p = parseLibraryParams('#/?q=');
    expect(p.q).toBeNull();
  });

  it('treats empty filter as null', function () {
    var p = parseLibraryParams('#/?filter=');
    expect(p.filter).toBeNull();
  });

  it('ignores unknown params', function () {
    var p = parseLibraryParams('#/?foo=bar&tag=abc');
    expect(p.tags).toEqual(['abc']);
    expect(p.q).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// buildLibraryHash
// ---------------------------------------------------------------------------
describe('buildLibraryHash', function () {
  it('returns #/ for empty params', function () {
    expect(buildLibraryHash({ tags: [] })).toBe('#/');
    expect(buildLibraryHash({})).toBe('#/');
  });

  it('builds tags only', function () {
    expect(buildLibraryHash({ tags: ['abc', 'def'] })).toBe('#/?tag=abc&tag=def');
  });

  it('builds q only', function () {
    expect(buildLibraryHash({ q: 'hello' })).toBe('#/?q=hello');
  });

  it('builds filter only', function () {
    expect(buildLibraryHash({ filter: 'archived' })).toBe('#/?filter=archived');
  });

  it('builds sort only', function () {
    expect(buildLibraryHash({ sort: 'oldest' })).toBe('#/?sort=oldest');
  });

  it('builds all params', function () {
    var hash = buildLibraryHash({ tags: ['abc'], q: 'test', filter: 'unread', sort: 'oldest' });
    expect(hash).toBe('#/?tag=abc&q=test&filter=unread&sort=oldest');
  });

  it('omits null/undefined values', function () {
    expect(buildLibraryHash({ tags: [], q: null, filter: null, sort: null })).toBe('#/');
  });

  it('encodes special characters in q', function () {
    expect(buildLibraryHash({ q: 'hello world' })).toBe('#/?q=hello%20world');
  });
});

// ---------------------------------------------------------------------------
// buildTagHash (backward compat alias)
// ---------------------------------------------------------------------------
describe('buildTagHash', function () {
  it('returns #/ for empty tags and no other params', function () {
    expect(buildTagHash([])).toBe('#/');
  });

  it('builds single tag hash', function () {
    expect(buildTagHash(['abc'])).toBe('#/?tag=abc');
  });

  it('builds multi-tag hash', function () {
    expect(buildTagHash(['abc', 'def'])).toBe('#/?tag=abc&tag=def');
  });

  it('includes other params after tags', function () {
    expect(buildTagHash(['abc'], { q: 'hello' })).toBe('#/?tag=abc&q=hello');
  });

  it('skips null/empty other params', function () {
    expect(buildTagHash(['abc'], { q: null, sort: '' })).toBe('#/?tag=abc');
  });

  it('returns hash with only other params when no tags', function () {
    expect(buildTagHash([], { q: 'hello' })).toBe('#/?q=hello');
  });

  it('encodes special characters in tags', function () {
    expect(buildTagHash(['c++'])).toBe('#/?tag=c%2B%2B');
  });
});

// ---------------------------------------------------------------------------
// Property-based: full roundtrip (parse ∘ build = identity)
// ---------------------------------------------------------------------------
describe('parseLibraryParams / buildLibraryHash roundtrip (property-based)', function () {
  it('roundtrip: parse(build(params)) preserves all fields', function () {
    fc.assert(
      fc.property(libraryParamsArb, function (params) {
        var hash = buildLibraryHash(params);
        var parsed = parseLibraryParams(hash);
        expect(parsed.tags).toEqual(params.tags);
        expect(parsed.q).toBe(params.q);
        expect(parsed.filter).toBe(params.filter);
        expect(parsed.sort).toBe(params.sort);
      }),
      { numRuns: 500 },
    );
  });

  it('tags order is preserved through roundtrip', function () {
    fc.assert(
      fc.property(fc.array(tagIdArb, { minLength: 2, maxLength: 4 }), function (tags) {
        var hash = buildLibraryHash({ tags: tags });
        var parsed = parseLibraryParams(hash);
        for (var i = 0; i < tags.length; i++) {
          expect(parsed.tags[i]).toBe(tags[i]);
        }
      }),
      { numRuns: 200 },
    );
  });

  it('empty params produce #/ and parse back to defaults', function () {
    var hash = buildLibraryHash({ tags: [] });
    expect(hash).toBe('#/');
    var parsed = parseLibraryParams(hash);
    expect(parsed.tags).toEqual([]);
    expect(parsed.q).toBeNull();
    expect(parsed.filter).toBeNull();
    expect(parsed.sort).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Property-based: state machine transitions preserve other params
// ---------------------------------------------------------------------------
describe('URL state machine properties (property-based)', function () {
  it('setting q preserves tags, filter, sort', function () {
    fc.assert(
      fc.property(libraryParamsArb, queryArb, function (initial, newQ) {
        var hash = buildLibraryHash(initial);
        var p = parseLibraryParams(hash);
        p.q = newQ;
        var newHash = buildLibraryHash(p);
        var result = parseLibraryParams(newHash);
        expect(result.tags).toEqual(initial.tags);
        expect(result.filter).toBe(initial.filter);
        expect(result.sort).toBe(initial.sort);
        expect(result.q).toBe(newQ);
      }),
      { numRuns: 300 },
    );
  });

  it('setting filter preserves tags, q, sort', function () {
    fc.assert(
      fc.property(libraryParamsArb, filterArb, function (initial, newFilter) {
        var hash = buildLibraryHash(initial);
        var p = parseLibraryParams(hash);
        p.filter = newFilter;
        var newHash = buildLibraryHash(p);
        var result = parseLibraryParams(newHash);
        expect(result.tags).toEqual(initial.tags);
        expect(result.q).toBe(initial.q);
        expect(result.sort).toBe(initial.sort);
        expect(result.filter).toBe(newFilter);
      }),
      { numRuns: 300 },
    );
  });

  it('setting sort preserves tags, q, filter', function () {
    fc.assert(
      fc.property(libraryParamsArb, sortArb, function (initial, newSort) {
        var hash = buildLibraryHash(initial);
        var p = parseLibraryParams(hash);
        p.sort = newSort;
        var newHash = buildLibraryHash(p);
        var result = parseLibraryParams(newHash);
        expect(result.tags).toEqual(initial.tags);
        expect(result.q).toBe(initial.q);
        expect(result.filter).toBe(initial.filter);
        expect(result.sort).toBe(newSort);
      }),
      { numRuns: 300 },
    );
  });

  it('adding a tag preserves q, filter, sort', function () {
    fc.assert(
      fc.property(libraryParamsArb, tagIdArb, function (initial, newTag) {
        var hash = buildLibraryHash(initial);
        var p = parseLibraryParams(hash);
        p.tags = p.tags.concat(newTag);
        var newHash = buildLibraryHash(p);
        var result = parseLibraryParams(newHash);
        expect(result.q).toBe(initial.q);
        expect(result.filter).toBe(initial.filter);
        expect(result.sort).toBe(initial.sort);
        expect(result.tags).toEqual(initial.tags.concat(newTag));
      }),
      { numRuns: 300 },
    );
  });

  it('removing a tag preserves q, filter, sort', function () {
    fc.assert(
      fc.property(
        fc.array(tagIdArb, { minLength: 1, maxLength: 4 }),
        queryArb,
        filterArb,
        sortArb,
        function (tags, q, filter, sort) {
          var initial = { tags: tags, q: q, filter: filter, sort: sort };
          var hash = buildLibraryHash(initial);
          var p = parseLibraryParams(hash);
          // Remove first tag
          p.tags = p.tags.slice(1);
          var newHash = buildLibraryHash(p);
          var result = parseLibraryParams(newHash);
          expect(result.q).toBe(q);
          expect(result.filter).toBe(filter);
          expect(result.sort).toBe(sort);
          expect(result.tags).toEqual(tags.slice(1));
        },
      ),
      { numRuns: 200 },
    );
  });

  it('clearing all params produces #/', function () {
    fc.assert(
      fc.property(libraryParamsArb, function (initial) {
        var hash = buildLibraryHash(initial);
        var p = parseLibraryParams(hash);
        p.tags = [];
        p.q = null;
        p.filter = null;
        p.sort = null;
        expect(buildLibraryHash(p)).toBe('#/');
      }),
      { numRuns: 200 },
    );
  });

  it('param order in hash is always tag, q, filter, sort', function () {
    fc.assert(
      fc.property(libraryParamsArb, function (params) {
        var hash = buildLibraryHash(params);
        if (hash === '#/') return; // no params to check order
        var qs = hash.slice(3); // strip '#/?'
        var keys = qs.split('&').map(function (part) {
          return part.split('=')[0];
        });
        // Extract position of first occurrence of each key type
        var firstTag = keys.indexOf('tag');
        var firstQ = keys.indexOf('q');
        var firstFilter = keys.indexOf('filter');
        var firstSort = keys.indexOf('sort');
        // If present, they must be in order: tag < q < filter < sort
        var positions = [
          { key: 'tag', pos: firstTag },
          { key: 'q', pos: firstQ },
          { key: 'filter', pos: firstFilter },
          { key: 'sort', pos: firstSort },
        ].filter(function (x) {
          return x.pos >= 0;
        });
        for (var i = 1; i < positions.length; i++) {
          expect(positions[i].pos).toBeGreaterThan(positions[i - 1].pos);
        }
      }),
      { numRuns: 200 },
    );
  });

  it('parseTagsFromHash agrees with parseLibraryParams.tags', function () {
    fc.assert(
      fc.property(libraryParamsArb, function (params) {
        var hash = buildLibraryHash(params);
        expect(parseTagsFromHash(hash)).toEqual(parseLibraryParams(hash).tags);
      }),
      { numRuns: 200 },
    );
  });
});
