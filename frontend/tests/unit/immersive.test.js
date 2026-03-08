import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { binarySearchSentence, initImmersive, destroyImmersive } from '../../src/immersive.js';

describe('binarySearchSentence', function () {
  var sentences = [
    { text: 'First.', start_ms: 0, end_ms: 1000 },
    { text: 'Second.', start_ms: 1000, end_ms: 2500 },
    { text: 'Third.', start_ms: 2500, end_ms: 4000 },
    { text: 'Fourth.', start_ms: 4000, end_ms: 5000 },
  ];

  it('finds the first sentence at time 0', function () {
    expect(binarySearchSentence(sentences, 0)).toBe(0);
  });

  it('finds the first sentence at time 500', function () {
    expect(binarySearchSentence(sentences, 500)).toBe(0);
  });

  it('finds the second sentence at boundary', function () {
    expect(binarySearchSentence(sentences, 1000)).toBe(1);
  });

  it('finds the third sentence in the middle', function () {
    expect(binarySearchSentence(sentences, 3000)).toBe(2);
  });

  it('finds the last sentence', function () {
    expect(binarySearchSentence(sentences, 4500)).toBe(3);
  });

  it('returns -1 after all sentences end', function () {
    expect(binarySearchSentence(sentences, 5000)).toBe(-1);
    expect(binarySearchSentence(sentences, 6000)).toBe(-1);
  });

  it('returns -1 for negative time', function () {
    expect(binarySearchSentence(sentences, -100)).toBe(-1);
  });

  it('returns -1 for empty sentences array', function () {
    expect(binarySearchSentence([], 500)).toBe(-1);
  });

  it('handles single sentence', function () {
    var single = [{ text: 'Only.', start_ms: 0, end_ms: 1000 }];
    expect(binarySearchSentence(single, 0)).toBe(0);
    expect(binarySearchSentence(single, 999)).toBe(0);
    expect(binarySearchSentence(single, 1000)).toBe(-1);
  });

  it('handles time exactly at sentence end', function () {
    // At end_ms of sentence 0 (1000), should be in sentence 1
    expect(binarySearchSentence(sentences, 999)).toBe(0);
    expect(binarySearchSentence(sentences, 1000)).toBe(1);
  });
});

describe('initImmersive and destroyImmersive', function () {
  var contentEl;
  var audioEl;
  var timing;

  beforeEach(function () {
    // Create a content element with text
    contentEl = document.createElement('article');
    contentEl.innerHTML = '<p>First sentence. Second sentence. Third sentence.</p>';
    document.body.appendChild(contentEl);

    // Create a mock audio element
    audioEl = {
      currentTime: 0,
      paused: true,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };

    timing = {
      version: 1,
      total_duration_ms: 6000,
      sentences: [
        { text: 'First sentence.', start_ms: 0, end_ms: 2000 },
        { text: 'Second sentence.', start_ms: 2000, end_ms: 4000 },
        { text: 'Third sentence.', start_ms: 4000, end_ms: 6000 },
      ],
    };
  });

  afterEach(function () {
    destroyImmersive();
    document.body.innerHTML = '';
  });

  it('wraps matched sentences in spans', function () {
    initImmersive(contentEl, timing, audioEl);
    var spans = contentEl.querySelectorAll('.tts-sentence');
    expect(spans.length).toBeGreaterThan(0);
  });

  it('sets data-idx on wrapped spans', function () {
    initImmersive(contentEl, timing, audioEl);
    var spans = contentEl.querySelectorAll('.tts-sentence');
    // At least the first span should have data-idx="0"
    var indices = Array.from(spans).map(function (s) {
      return s.dataset.idx;
    });
    expect(indices).toContain('0');
  });

  it('registers timeupdate listener on audio element', function () {
    initImmersive(contentEl, timing, audioEl);
    var calls = audioEl.addEventListener.mock.calls;
    var events = calls.map(function (c) {
      return c[0];
    });
    expect(events).toContain('timeupdate');
    expect(events).toContain('play');
    expect(events).toContain('pause');
  });

  it('creates return-to-audio button in DOM', function () {
    initImmersive(contentEl, timing, audioEl);
    var btn = document.querySelector('.tts-return-btn');
    expect(btn).not.toBeNull();
  });

  it('destroyImmersive removes all spans', function () {
    initImmersive(contentEl, timing, audioEl);
    expect(contentEl.querySelectorAll('.tts-sentence').length).toBeGreaterThan(0);

    destroyImmersive();
    expect(contentEl.querySelectorAll('.tts-sentence').length).toBe(0);
  });

  it('destroyImmersive removes return button', function () {
    initImmersive(contentEl, timing, audioEl);
    expect(document.querySelector('.tts-return-btn')).not.toBeNull();

    destroyImmersive();
    expect(document.querySelector('.tts-return-btn')).toBeNull();
  });

  it('destroyImmersive removes event listeners', function () {
    initImmersive(contentEl, timing, audioEl);
    destroyImmersive();

    var removeCalls = audioEl.removeEventListener.mock.calls;
    var events = removeCalls.map(function (c) {
      return c[0];
    });
    expect(events).toContain('timeupdate');
    expect(events).toContain('play');
    expect(events).toContain('pause');
  });

  it('preserves original text content after destroy', function () {
    var originalText = contentEl.textContent;
    initImmersive(contentEl, timing, audioEl);
    destroyImmersive();
    expect(contentEl.textContent).toBe(originalText);
  });

  it('handles null timing gracefully', function () {
    var cleanup = initImmersive(contentEl, null, audioEl);
    expect(typeof cleanup).toBe('function');
    expect(contentEl.querySelectorAll('.tts-sentence').length).toBe(0);
  });

  it('handles empty sentences array', function () {
    var emptyTiming = { version: 1, total_duration_ms: 0, sentences: [] };
    initImmersive(contentEl, emptyTiming, audioEl);
    expect(contentEl.querySelectorAll('.tts-sentence').length).toBe(0);
  });

  it('handles content with inline formatting', function () {
    contentEl.innerHTML = '<p><strong>First</strong> sentence. Second sentence.</p>';
    var formattedTiming = {
      version: 1,
      total_duration_ms: 4000,
      sentences: [
        { text: 'First sentence.', start_ms: 0, end_ms: 2000 },
        { text: 'Second sentence.', start_ms: 2000, end_ms: 4000 },
      ],
    };
    // Should not throw
    initImmersive(contentEl, formattedTiming, audioEl);
    // May or may not match (surroundContents fails across elements), but shouldn't crash
  });

  it('calling initImmersive twice cleans up first session', function () {
    initImmersive(contentEl, timing, audioEl);
    var firstSpans = contentEl.querySelectorAll('.tts-sentence').length;

    // Reset content for second init
    contentEl.innerHTML = '<p>First sentence. Second sentence. Third sentence.</p>';
    initImmersive(contentEl, timing, audioEl);

    // Should only have one set of return buttons
    var buttons = document.querySelectorAll('.tts-return-btn');
    expect(buttons.length).toBe(1);
  });
});
