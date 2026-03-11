import { describe, it, expect } from 'vitest';
import fc from 'fast-check';
import { binarySearchSentence } from '../../src/immersive.js';
import { formatDate, formatTime } from '../../src/utils.js';

// ---------------------------------------------------------------------------
// Custom arbitrary: generates contiguous, non-overlapping sentence arrays
// ---------------------------------------------------------------------------
var sentencesArb = fc
  .array(
    fc.record({
      text: fc.string({ minLength: 1 }),
      start_ms: fc.nat(),
      end_ms: fc.nat(),
    }),
    { minLength: 1, maxLength: 20 },
  )
  .map(function (arr) {
    var offset = 0;
    return arr.map(function (s) {
      var duration = (s.end_ms % 5000) + 100; // 100ms to 5100ms
      var result = { text: s.text, start_ms: offset, end_ms: offset + duration };
      offset += duration;
      return result;
    });
  });

// ---------------------------------------------------------------------------
// binarySearchSentence
// ---------------------------------------------------------------------------
describe('binarySearchSentence (property-based)', function () {
  it('returns -1 for an empty array', function () {
    fc.assert(
      fc.property(fc.nat(), function (ms) {
        expect(binarySearchSentence([], ms)).toBe(-1);
      }),
      { numRuns: 200 },
    );
  });

  it('returns -1 or a valid index within bounds', function () {
    fc.assert(
      fc.property(sentencesArb, fc.nat(), function (sentences, ms) {
        var result = binarySearchSentence(sentences, ms);
        if (result !== -1) {
          expect(result).toBeGreaterThanOrEqual(0);
          expect(result).toBeLessThan(sentences.length);
        }
      }),
      { numRuns: 200 },
    );
  });

  it('if result >= 0, then start_ms <= ms < end_ms', function () {
    fc.assert(
      fc.property(sentencesArb, fc.nat(), function (sentences, ms) {
        var result = binarySearchSentence(sentences, ms);
        if (result >= 0) {
          expect(sentences[result].start_ms).toBeLessThanOrEqual(ms);
          expect(ms).toBeLessThan(sentences[result].end_ms);
        }
      }),
      { numRuns: 200 },
    );
  });

  it('returns -1 for negative ms when first sentence starts at >= 0', function () {
    fc.assert(
      fc.property(sentencesArb, fc.integer({ min: -1000000, max: -1 }), function (sentences, ms) {
        // All generated sentences start at offset >= 0
        var result = binarySearchSentence(sentences, ms);
        expect(result).toBe(-1);
      }),
      { numRuns: 200 },
    );
  });

  it('returns -1 for ms >= last sentence end_ms', function () {
    fc.assert(
      fc.property(sentencesArb, fc.nat(), function (sentences, extra) {
        var lastEnd = sentences[sentences.length - 1].end_ms;
        var ms = lastEnd + extra;
        var result = binarySearchSentence(sentences, ms);
        expect(result).toBe(-1);
      }),
      { numRuns: 200 },
    );
  });

  it('for contiguous sentences, every ms in [0, total_end) returns a valid index', function () {
    fc.assert(
      fc.property(
        sentencesArb,
        fc.double({ min: 0, max: 1, noNaN: true }),
        function (sentences, frac) {
          var totalEnd = sentences[sentences.length - 1].end_ms;
          // Pick a random ms within [0, totalEnd) using the fraction
          var ms = Math.floor(frac * totalEnd);
          if (ms >= totalEnd) ms = totalEnd - 1;
          if (ms < 0) ms = 0;
          var result = binarySearchSentence(sentences, ms);
          expect(result).toBeGreaterThanOrEqual(0);
          expect(result).toBeLessThan(sentences.length);
        },
      ),
      { numRuns: 200 },
    );
  });

  it('adjacent boundary: ms exactly at end_ms[i] == start_ms[i+1] returns i+1', function () {
    fc.assert(
      fc.property(
        sentencesArb.filter(function (s) {
          return s.length >= 2;
        }),
        fc.nat(),
        function (sentences, rawIdx) {
          var idx = rawIdx % (sentences.length - 1); // pick a valid boundary
          var boundary = sentences[idx].end_ms; // == sentences[idx+1].start_ms
          var result = binarySearchSentence(sentences, boundary);
          expect(result).toBe(idx + 1);
        },
      ),
      { numRuns: 200 },
    );
  });
});

// ---------------------------------------------------------------------------
// formatDate
// ---------------------------------------------------------------------------
describe('formatDate (property-based)', function () {
  it('always returns a string', function () {
    fc.assert(
      fc.property(
        fc.oneof(
          fc.string(),
          fc.constant(null),
          fc.constant(undefined),
          fc.integer(),
          fc.constant(true),
          fc.constant(false),
          fc.constant(''),
          fc.constant(0),
          fc.constant(NaN),
        ),
        function (input) {
          var result = formatDate(input);
          expect(typeof result).toBe('string');
        },
      ),
      { numRuns: 200 },
    );
  });

  it('returns empty string for null, undefined, and empty string', function () {
    expect(formatDate(null)).toBe('');
    expect(formatDate(undefined)).toBe('');
    expect(formatDate('')).toBe('');
  });

  it('returns empty string for invalid date strings', function () {
    fc.assert(
      fc.property(
        fc.string().filter(function (s) {
          return isNaN(new Date(s).getTime());
        }),
        function (input) {
          expect(formatDate(input)).toBe('');
        },
      ),
      { numRuns: 200 },
    );
  });

  it('valid ISO date returns a non-empty string', function () {
    // Generate valid timestamps as integers to avoid fast-check producing invalid Date objects
    var min = new Date('2000-01-01').getTime();
    var max = new Date('2030-12-31').getTime();
    fc.assert(
      fc.property(fc.integer({ min: min, max: max }), function (ts) {
        var result = formatDate(new Date(ts).toISOString());
        expect(result.length).toBeGreaterThan(0);
      }),
      { numRuns: 200 },
    );
  });

  it('never throws for any input', function () {
    fc.assert(
      fc.property(
        fc.oneof(
          fc.string(),
          fc.constant(null),
          fc.constant(undefined),
          fc.integer(),
          fc.constant(true),
          fc.constant(false),
          fc.constant(''),
        ),
        function (input) {
          expect(function () {
            formatDate(input);
          }).not.toThrow();
        },
      ),
      { numRuns: 200 },
    );
  });

  it('recent dates produce relative time strings', function () {
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 59 }), function (minutesAgo) {
        var d = new Date(Date.now() - minutesAgo * 60000);
        var result = formatDate(d.toISOString());
        // Should be like "Xm ago"
        expect(result).toMatch(/^\d+m ago$/);
      }),
      { numRuns: 200 },
    );
  });
});

// ---------------------------------------------------------------------------
// formatTime
// ---------------------------------------------------------------------------
describe('formatTime (property-based)', function () {
  it('always returns a string matching M:SS or MM:SS pattern', function () {
    fc.assert(
      fc.property(fc.double({ min: 0, max: 100000, noNaN: true }), function (seconds) {
        var result = formatTime(seconds);
        if (seconds === 0) {
          expect(result).toBe('0:00');
        } else {
          expect(result).toMatch(/^\d+:\d{2}$/);
        }
      }),
      { numRuns: 200 },
    );
  });

  it('minutes part equals Math.floor(seconds / 60)', function () {
    fc.assert(
      fc.property(fc.double({ min: 0.001, max: 100000, noNaN: true }), function (seconds) {
        var result = formatTime(seconds);
        var parts = result.split(':');
        var expectedMinutes = Math.floor(seconds / 60);
        expect(parseInt(parts[0], 10)).toBe(expectedMinutes);
      }),
      { numRuns: 200 },
    );
  });

  it('seconds part is always between 00 and 59', function () {
    fc.assert(
      fc.property(fc.double({ min: 0.001, max: 100000, noNaN: true }), function (seconds) {
        var result = formatTime(seconds);
        var parts = result.split(':');
        var secPart = parseInt(parts[1], 10);
        expect(secPart).toBeGreaterThanOrEqual(0);
        expect(secPart).toBeLessThanOrEqual(59);
      }),
      { numRuns: 200 },
    );
  });

  it('returns 0:00 for 0', function () {
    expect(formatTime(0)).toBe('0:00');
  });

  it('returns 0:00 for NaN', function () {
    expect(formatTime(NaN)).toBe('0:00');
  });

  it('returns 0:00 for falsy values', function () {
    fc.assert(
      fc.property(fc.constantFrom(0, null, undefined, false, '', NaN), function (input) {
        expect(formatTime(input)).toBe('0:00');
      }),
      { numRuns: 200 },
    );
  });

  it('returns --:-- for Infinity', function () {
    expect(formatTime(Infinity)).toBe('--:--');
    expect(formatTime(-Infinity)).toBe('--:--');
  });

  it('seconds part matches Math.floor(seconds % 60)', function () {
    fc.assert(
      fc.property(fc.integer({ min: 1, max: 100000 }), function (seconds) {
        var result = formatTime(seconds);
        var parts = result.split(':');
        var expectedSec = Math.floor(seconds % 60);
        expect(parseInt(parts[1], 10)).toBe(expectedSec);
      }),
      { numRuns: 200 },
    );
  });
});
