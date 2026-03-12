/**
 * TTS Immersive Reading — sentence highlighting synchronized with audio.
 *
 * Matches timing data sentences to rendered DOM text, wraps them in
 * spans, and highlights the active sentence during audio playback
 * with a gradient sweep animation.
 */

let sentenceSpans = [];
let cleanupFns = [];
let currentIdx = -1;
let rafId = null;
let userScrolledAway = false;
let programmaticScroll = false;
let returnBtnEl = null;
let scrollTimer = null;

/**
 * Binary search for the active sentence given current time in ms.
 * Returns the index into the sentences array, or -1 if none match.
 */
export function binarySearchSentence(sentences, ms) {
  let lo = 0;
  let hi = sentences.length - 1;
  let result = -1;

  while (lo <= hi) {
    const mid = (lo + hi) >>> 1;
    if (sentences[mid].start_ms <= ms) {
      result = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }

  // Verify the found sentence actually contains this time
  if (result >= 0 && ms >= sentences[result].end_ms) {
    return -1;
  }
  return result;
}

/**
 * Match timing sentences to DOM text nodes and wrap them in spans.
 *
 * Uses a forward scan through concatenated text content to find
 * each sentence, then wraps the corresponding DOM range in a span.
 */
function matchSentencesToDOM(contentEl, sentences) {
  // Collect all text nodes
  const walker = document.createTreeWalker(contentEl, NodeFilter.SHOW_TEXT, null);
  let fullText = '';
  let nodeOffsets = []; // { node, startOffset (in fullText) }

  let node;
  while ((node = walker.nextNode())) {
    nodeOffsets.push({ node: node, start: fullText.length });
    fullText += node.textContent;
  }

  if (!fullText.trim()) return [];

  const spans = [];
  let searchFrom = 0;

  for (let i = 0; i < sentences.length; i++) {
    const sentText = sentences[i].text;
    // Normalize whitespace for matching
    const normalized = sentText.replace(/\s+/g, ' ').trim();
    if (normalized.length < 3) {
      spans.push(null);
      continue;
    }

    // Find this sentence in the full text
    const idx = _findSentenceInText(fullText, normalized, searchFrom);
    if (idx === -1) {
      spans.push(null);
      continue;
    }

    const endIdx = idx + normalized.length;
    searchFrom = endIdx;

    // Create a range spanning the matched text
    const range = document.createRange();
    let startSet = false;
    let endSet = false;

    for (let j = 0; j < nodeOffsets.length; j++) {
      const no = nodeOffsets[j];
      const nodeEnd = no.start + no.node.textContent.length;

      if (!startSet && idx >= no.start && idx < nodeEnd) {
        range.setStart(no.node, idx - no.start);
        startSet = true;
      }
      if (startSet && !endSet && endIdx > no.start && endIdx <= nodeEnd) {
        range.setEnd(no.node, endIdx - no.start);
        endSet = true;
        break;
      }
    }

    if (!startSet || !endSet) {
      spans.push(null);
      continue;
    }

    // Wrap the range in a span
    try {
      const span = document.createElement('span');
      span.className = 'tts-sentence';
      span.dataset.idx = String(i);

      // extractContents + insertNode handles cross-element ranges
      // where surroundContents would throw
      const fragment = range.extractContents();
      span.appendChild(fragment);
      range.insertNode(span);

      // Rebuild text node list for remaining matches
      if (i < sentences.length - 1) {
        fullText = '';
        nodeOffsets = [];
        const w2 = document.createTreeWalker(contentEl, NodeFilter.SHOW_TEXT, null);
        let n2;
        while ((n2 = w2.nextNode())) {
          nodeOffsets.push({ node: n2, start: fullText.length });
          fullText += n2.textContent;
        }
        searchFrom = 0;
        // Find where we are in the new text
        const lastSpanText = span.textContent;
        const refIdx = fullText.indexOf(lastSpanText, Math.max(0, searchFrom - lastSpanText.length));
        if (refIdx >= 0) {
          searchFrom = refIdx + lastSpanText.length;
        }
      }

      spans.push(span);
    } catch (_e) {
      // extractContents can still fail in edge cases
      spans.push(null);
    }
  }

  return spans;
}

/**
 * Find sentence text in the full concatenated text.
 * Tries exact match first, then normalized whitespace match.
 */
function _findSentenceInText(fullText, sentenceNormalized, fromIndex) {
  // Try direct indexOf
  const idx = fullText.indexOf(sentenceNormalized, fromIndex);
  if (idx >= 0) return idx;

  // Try with collapsed whitespace in the full text
  // Build a mapping from collapsed positions to original positions
  let collapsed = '';
  const posMap = [];
  let inSpace = false;
  for (let k = fromIndex; k < fullText.length; k++) {
    const ch = fullText[k];
    if (/\s/.test(ch)) {
      if (!inSpace) {
        collapsed += ' ';
        posMap.push(k);
        inSpace = true;
      }
    } else {
      collapsed += ch;
      posMap.push(k);
      inSpace = false;
    }
  }

  const cIdx = collapsed.indexOf(sentenceNormalized);
  if (cIdx >= 0 && cIdx < posMap.length) {
    return posMap[cIdx];
  }

  return -1;
}

/**
 * Initialize immersive reading mode.
 *
 * @param {HTMLElement} contentEl - The reader content container
 * @param {Object} timing - The timing manifest from the API
 * @param {HTMLAudioElement} audioEl - The audio element
 * @returns {Function} Cleanup function to destroy immersive mode
 */
export function initImmersive(contentEl, timing, audioEl) {
  // Clean up any previous session
  destroyImmersive();

  if (!timing || !timing.sentences || !timing.sentences.length) return destroyImmersive;
  if (!contentEl || !audioEl) return destroyImmersive;

  // Match sentences to DOM and wrap in spans
  sentenceSpans = matchSentencesToDOM(contentEl, timing.sentences);

  // Set up timeupdate handler
  function onTimeUpdate() {
    const ms = audioEl.currentTime * 1000;
    const idx = binarySearchSentence(timing.sentences, ms);

    if (idx === currentIdx) return;

    // Remove previous highlight
    if (currentIdx >= 0 && sentenceSpans[currentIdx]) {
      sentenceSpans[currentIdx].classList.remove('tts-sentence-active');
      sentenceSpans[currentIdx].style.removeProperty('--tts-progress');
    }

    currentIdx = idx;

    // Apply new highlight
    if (idx >= 0 && sentenceSpans[idx]) {
      sentenceSpans[idx].classList.add('tts-sentence-active');

      // Auto-scroll
      if (!userScrolledAway) {
        programmaticScroll = true;
        sentenceSpans[idx].scrollIntoView({ behavior: 'smooth', block: 'center' });
        setTimeout(function () {
          programmaticScroll = false;
        }, 500);
      }
    }
  }

  // Gradient sweep at 60fps
  function sweepLoop() {
    if (currentIdx >= 0 && sentenceSpans[currentIdx] && !audioEl.paused) {
      const s = timing.sentences[currentIdx];
      const ms = audioEl.currentTime * 1000;
      const progress = Math.min(1, Math.max(0, (ms - s.start_ms) / (s.end_ms - s.start_ms)));
      sentenceSpans[currentIdx].style.setProperty('--tts-progress', progress * 100 + '%');
    }
    if (!audioEl.paused) {
      rafId = requestAnimationFrame(sweepLoop);
    }
  }

  function onPlay() {
    rafId = requestAnimationFrame(sweepLoop);
  }

  function onPause() {
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = null;
    }
  }

  // User scroll detection
  function onScroll() {
    if (programmaticScroll) return;
    userScrolledAway = true;
    if (returnBtnEl) returnBtnEl.classList.add('visible');
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(function () {
      userScrolledAway = false;
      if (returnBtnEl) returnBtnEl.classList.remove('visible');
    }, 8000);
  }

  // Click-to-seek
  function onSentenceClick(e) {
    const span = e.target.closest('.tts-sentence');
    if (!span || !span.dataset.idx) return;
    const idx = parseInt(span.dataset.idx, 10);
    if (idx >= 0 && idx < timing.sentences.length) {
      audioEl.currentTime = timing.sentences[idx].start_ms / 1000;
      // Reset scroll tracking
      userScrolledAway = false;
      if (returnBtnEl) returnBtnEl.classList.remove('visible');
    }
  }

  // Create "Return to audio" button
  returnBtnEl = document.createElement('button');
  returnBtnEl.className = 'tts-return-btn';
  returnBtnEl.textContent = 'Return to audio';
  returnBtnEl.addEventListener('click', function () {
    if (currentIdx >= 0 && sentenceSpans[currentIdx]) {
      userScrolledAway = false;
      programmaticScroll = true;
      sentenceSpans[currentIdx].scrollIntoView({ behavior: 'smooth', block: 'center' });
      setTimeout(function () {
        programmaticScroll = false;
      }, 500);
    }
    returnBtnEl.classList.remove('visible');
  });
  document.body.appendChild(returnBtnEl);

  // Attach event listeners
  audioEl.addEventListener('timeupdate', onTimeUpdate);
  audioEl.addEventListener('play', onPlay);
  audioEl.addEventListener('pause', onPause);
  window.addEventListener('scroll', onScroll);
  contentEl.addEventListener('click', onSentenceClick);

  // Start sweep if already playing
  if (!audioEl.paused) {
    onPlay();
  }

  // Store cleanup references
  cleanupFns.push(
    function () {
      audioEl.removeEventListener('timeupdate', onTimeUpdate);
    },
    function () {
      audioEl.removeEventListener('play', onPlay);
    },
    function () {
      audioEl.removeEventListener('pause', onPause);
    },
    function () {
      window.removeEventListener('scroll', onScroll);
    },
    function () {
      contentEl.removeEventListener('click', onSentenceClick);
    },
  );

  return destroyImmersive;
}

/**
 * Destroy immersive mode — unwrap spans, remove listeners, reset state.
 */
export function destroyImmersive() {
  // Cancel animation frame
  if (rafId) {
    cancelAnimationFrame(rafId);
    rafId = null;
  }

  // Run cleanup functions
  for (let i = 0; i < cleanupFns.length; i++) {
    try {
      cleanupFns[i]();
    } catch (_e) {
      /* ignore */
    }
  }
  cleanupFns = [];

  // Unwrap sentence spans (replace span with its children)
  for (let j = 0; j < sentenceSpans.length; j++) {
    const span = sentenceSpans[j];
    if (!span || !span.parentNode) continue;
    const parent = span.parentNode;
    while (span.firstChild) {
      parent.insertBefore(span.firstChild, span);
    }
    parent.removeChild(span);
    parent.normalize(); // Merge adjacent text nodes
  }
  sentenceSpans = [];
  currentIdx = -1;

  // Remove return button
  if (returnBtnEl && returnBtnEl.parentNode) {
    returnBtnEl.parentNode.removeChild(returnBtnEl);
  }
  returnBtnEl = null;
  userScrolledAway = false;

  if (scrollTimer) {
    clearTimeout(scrollTimer);
    scrollTimer = null;
  }
}
