import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { parseTagsFromHash, buildTagHash } from '../../src/nav.js';

// We only test pure functions here. nav.tagFilter mutates window.location.hash
// and is tested via component integration tests.

// ---------------------------------------------------------------------------
// parseTagsFromHash
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
// buildTagHash
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
// Property-based: roundtrip
// ---------------------------------------------------------------------------
describe('parseTagsFromHash / buildTagHash roundtrip (property-based)', function () {
  // Arbitrary for URL-safe tag IDs (non-empty, no &, no =, no #, no ?)
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

  it('roundtrip: parse(build(tags)) === tags', function () {
    fc.assert(
      fc.property(fc.array(tagIdArb, { minLength: 0, maxLength: 4 }), function (tags) {
        var hash = buildTagHash(tags);
        var parsed = parseTagsFromHash(hash);
        expect(parsed).toEqual(tags);
      }),
      { numRuns: 200 },
    );
  });

  it('order is preserved through roundtrip', function () {
    fc.assert(
      fc.property(fc.array(tagIdArb, { minLength: 2, maxLength: 4 }), function (tags) {
        var hash = buildTagHash(tags);
        var parsed = parseTagsFromHash(hash);
        for (var i = 0; i < tags.length; i++) {
          expect(parsed[i]).toBe(tags[i]);
        }
      }),
      { numRuns: 200 },
    );
  });

  it('no tags produces empty array after roundtrip', function () {
    var hash = buildTagHash([]);
    expect(parseTagsFromHash(hash)).toEqual([]);
  });

  it('parse never returns more tags than were in the URL', function () {
    fc.assert(
      fc.property(fc.array(tagIdArb, { minLength: 0, maxLength: 4 }), function (tags) {
        var hash = buildTagHash(tags);
        var parsed = parseTagsFromHash(hash);
        expect(parsed.length).toBe(tags.length);
      }),
      { numRuns: 200 },
    );
  });
});
