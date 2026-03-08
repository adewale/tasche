# TTS Immersive Reading — Spec for Tasche

## Problem

Tasche generates audio for articles via Workers AI TTS, but audio playback and text reading are completely disconnected. The user presses play and stares at static text, or locks their screen and listens passively. There is no bridge between the two modes.

The best read-it-later experiences — Kindle Immersive Reading, Apple Books Read Aloud, Speechify, Readwise Reader — all solve this by synchronizing audio playback with visual text. The highlighted text acts as a focus anchor, improves comprehension, and transforms "listen later" from a background activity into an immersive one.

## What the best implementations do

### Kindle Immersive Reading
- Word-by-word highlighting synced to professional audiobook narration
- Uses pre-computed alignment maps created during Audible publishing
- Text and audio progress are bidirectionally linked (drag the audio scrubber, text follows; tap a paragraph, audio jumps)

### Apple Books Read Aloud
- Built on EPUB Media Overlays (SMIL) — each text fragment paired with `clipBegin`/`clipEnd` timestamps
- Users choose: highlight words only, sentences only, or both simultaneously
- Sentence gets a background highlight, current word gets an underline within it

### Speechify
- Uses TTS providers that return native timing data (ElevenLabs, Amazon Polly)
- Word-by-word karaoke highlighting in real-time
- Click any word to jump audio there

### Readwise Reader
- Uses Unreal Speech (which returns word/sentence timestamps natively)
- **Pauses auto-scroll when the user manually scrolls** — shows "return to position" and "jump to highlight" buttons
- Audio generated per-section on demand

### Common patterns
- Auto-scroll keeps highlighted region visible without jarring jumps
- Click/tap any sentence to seek audio there
- Speed changes don't break sync (`currentTime` stays correct at any playback rate)
- Non-active text is subtly dimmed

## Landscape: how TTS providers handle timing

| Provider | Timing support | Format |
|----------|---------------|--------|
| ElevenLabs | Character-level timestamps | `with-timestamps` endpoint returns `alignment.character_start_times_seconds[]` |
| Amazon Polly | Word + sentence marks | NDJSON: `{time, type, start, end, value}` per word/sentence |
| Azure Speech | Word boundary events | SDK fires `WordBoundary` with audio offset + text offset |
| Cartesia Sonic | Word + phoneme timestamps | SSE events with word-level timing |
| Unreal Speech | Word + sentence timestamps | `{word, start, end, text_offset}` per word |
| Google Cloud TTS | SSML marks only | Timing only for explicitly placed `<mark>` tags |
| **Deepgram Aura-2** | **None** | **Opaque audio blob, no timing metadata** |
| **OpenAI TTS** | **None** | **Opaque audio blob** |
| MeloTTS | None | Base64 audio blob |

**Cloudflare Workers AI wraps Deepgram Aura-2 and MeloTTS. Neither returns timing data. There is no hidden parameter, no streaming mode, no alternate endpoint. This is a fundamental API limitation, not a configuration gap.**

## Design decision: make Tier 1 excellent

The Whisper-based approach (running STT on our own TTS output to reverse-engineer timestamps) is architecturally dishonest — it's using a 1.5B parameter model to recover data that should have been a first-class output of the generation step. It adds cost, latency, a new failure mode, and an alignment problem (Whisper may normalize text differently than the source).

Instead, we invest in making sentence-level timing feel premium. The key insight: **every app listed above started with sentence-level highlighting.** Kindle Immersive Reading, Apple Books, and Readwise all highlight at the sentence or clause level as the primary experience. Word-level is a refinement, not the foundation.

If we later switch to a TTS provider that returns native timing (ElevenLabs, Polly, Unreal Speech, or a future Deepgram capability), word-level becomes a drop-in upgrade to the same client infrastructure.

## Architecture

### Timing data from the existing pipeline — zero extra cost

Tasche already generates audio per-chunk via `chunk_text()`, where each chunk is one or more sentences. During concatenation, we measure each chunk's audio duration by parsing frame headers. This gives us exact sentence-group timing for free.

**Getting exact duration from audio bytes:**

For OGG Opus (our default: `encoding=opus, container=ogg`):
- Each OGG page has a 27-byte header with `granule_position` at bytes 6-13 (little-endian int64)
- The final page's granule position = total PCM samples at 48kHz
- Duration = `(granule_position - pre_skip) / 48000.0` seconds
- Pre-skip is in the first OGG page's Opus header (bytes 10-11 of the ID header payload)
- This requires scanning page headers only — no audio decoding

For MP3 (fallback format):
- Each frame has a 4-byte header encoding sample rate and samples-per-frame
- Duration = sum of `(samples_per_frame / sample_rate)` across all frames
- Byte concatenation of MP3 chunks is trivially correct (no container state)

**Both methods are exact and require no external libraries.** Pure byte parsing in Python.

### Finer granularity within chunks

The current `chunk_text()` packs multiple sentences per chunk (up to 1900 chars). A chunk might contain 3-5 sentences. We can do better without increasing API calls:

**Track sentence boundaries within each chunk.** The `chunk_text()` function already joins sentences with spaces. If we preserve the per-sentence text alongside each chunk, we can distribute the measured chunk duration across its sentences proportionally by character count:

```python
# For a chunk with duration 4.2s containing 3 sentences:
# "Hello world." (12 chars) + "How are you?" (12 chars) + "I am fine." (10 chars)
# Total: 34 chars
# Sentence 1: 0.00s - 1.48s  (12/34 * 4.2)
# Sentence 2: 1.48s - 2.96s  (12/34 * 4.2)
# Sentence 3: 2.96s - 4.20s  (10/34 * 4.2)
```

Character-proportional distribution is imperfect (speech rate varies by word complexity, pauses between sentences, etc.) but empirically close enough for sentence-level highlighting where the visual transition happens every 2-5 seconds. The ear tolerates ±300ms of drift at sentence boundaries.

### Making it feel better than sentence-level

Three UX techniques that make sentence-level timing feel closer to word-level:

**1. Gradient sweep within the active sentence.** Instead of a static background highlight, animate a left-to-right gradient fill across the sentence, timed to its duration. The highlight "fills" the sentence as it's spoken, arriving at the right edge just as the next sentence begins. This creates a sense of continuous motion within each sentence.

```css
.tts-sentence-active {
  background: linear-gradient(90deg,
    var(--tts-highlight) var(--tts-progress),
    transparent var(--tts-progress)
  );
}
```

Where `--tts-progress` is updated via JS from `0%` to `100%` during the sentence's time window.

**2. Crossfade transitions.** Fade the outgoing sentence's highlight over 200ms while fading in the incoming sentence. This eliminates the "snap" between sentences and creates the illusion of smooth movement.

**3. Context dimming.** Subtly reduce the opacity of non-active paragraphs (not just sentences). This draws the eye to the active region without requiring word-level precision.

### Pipeline changes

```
Current pipeline:
  markdown → strip_markdown() → chunk_text() → [chunks]
    → TTS per chunk → b"".join(audio_parts) → store audio in R2

New pipeline:
  markdown → strip_markdown() → chunk_text_with_sentences() → [chunks_with_sentence_lists]
    → TTS per chunk → measure each chunk's duration from audio bytes
    → build timing manifest (sentence-level, proportionally distributed)
    → b"".join(audio_parts) → store audio in R2
    → store timing manifest in R2
```

### Timing manifest format

```json
{
  "version": 1,
  "total_duration_ms": 48200,
  "sentences": [
    { "text": "Mary had a little lamb.", "start_ms": 0, "end_ms": 1482 },
    { "text": "Its fleece was white as snow.", "start_ms": 1482, "end_ms": 2964 },
    { "text": "Everywhere that Mary went, the lamb was sure to go.", "start_ms": 2964, "end_ms": 4200 }
  ]
}
```

**Store at:** `articles/{article_id}/audio-timing.json` in R2.

### New API endpoint

```
GET /api/articles/{article_id}/audio-timing
```

Returns the timing JSON from R2. Returns 404 if no timing data exists (legacy audio). The client gracefully degrades — audio plays without highlighting, exactly like today.

### Backend implementation detail

New function in `tts/processing.py`:

```python
def chunk_text_with_sentences(text: str, max_chars: int = 1900) -> list[dict]:
    """Like chunk_text(), but preserves per-sentence boundaries.

    Returns a list of dicts:
      [{"text": "Full chunk text...", "sentences": ["Sentence 1.", "Sentence 2."]}, ...]
    """
    sentences = split_sentences(text)
    chunks = []
    current_sentences = []
    current_len = 0

    for sentence in sentences:
        added = len(sentence) + (1 if current_sentences else 0)
        if current_sentences and current_len + added > max_chars:
            chunks.append({
                "text": " ".join(current_sentences),
                "sentences": current_sentences,
            })
            current_sentences = [sentence]
            current_len = len(sentence)
        else:
            current_sentences.append(sentence)
            current_len += added

    if current_sentences:
        chunks.append({
            "text": " ".join(current_sentences),
            "sentences": current_sentences,
        })

    return chunks
```

Duration measurement (OGG Opus):

```python
def _ogg_duration_seconds(data: bytes) -> float:
    """Extract duration from OGG Opus audio by reading page headers.

    Scans for OGG page headers (magic bytes 'OggS') and reads
    granule_position from each. The final page's granule position
    gives total samples at 48kHz. Pre-skip is read from the first
    page's Opus ID header.
    """
    pre_skip = 0
    last_granule = 0
    i = 0

    while i < len(data) - 27:
        if data[i:i+4] != b'OggS':
            i += 1
            continue
        # granule_position: bytes 6-13, little-endian int64
        granule = int.from_bytes(data[i+6:i+14], 'little', signed=True)
        if granule >= 0:
            last_granule = granule

        # Read pre_skip from first Opus ID header
        if pre_skip == 0:
            # Page header: 27 bytes + segment table
            num_segments = data[i+26]
            header_size = 27 + num_segments
            payload_start = i + header_size
            # Check for 'OpusHead' magic
            if (payload_start + 12 <= len(data)
                    and data[payload_start:payload_start+8] == b'OpusHead'):
                pre_skip = int.from_bytes(
                    data[payload_start+10:payload_start+12], 'little'
                )

        # Skip to next page (scan forward for next 'OggS')
        i += 27
        # Fast skip: jump past segment table
        if i - 27 + 26 < len(data):
            num_segments = data[i - 27 + 26]
            i += num_segments

    if last_granule <= pre_skip:
        return 0.0
    return (last_granule - pre_skip) / 48000.0
```

Build the manifest during concatenation:

```python
timing_sentences = []
cumulative_ms = 0

for chunk_info, chunk_audio in zip(chunks_with_sentences, audio_parts):
    chunk_duration_ms = _ogg_duration_seconds(chunk_audio) * 1000
    total_chars = sum(len(s) for s in chunk_info["sentences"])

    for sentence in chunk_info["sentences"]:
        proportion = len(sentence) / total_chars if total_chars > 0 else 1.0
        sentence_duration_ms = chunk_duration_ms * proportion
        timing_sentences.append({
            "text": sentence,
            "start_ms": round(cumulative_ms),
            "end_ms": round(cumulative_ms + sentence_duration_ms),
        })
        cumulative_ms += sentence_duration_ms

manifest = {
    "version": 1,
    "total_duration_ms": round(cumulative_ms),
    "sentences": timing_sentences,
}
```

## Frontend design

### Text preparation

When audio starts playing and timing data is available:

1. Fetch `/api/articles/{id}/audio-timing` (cache in memory for the session)
2. Walk the reader's DOM tree, identify text nodes
3. Match timing sentences to DOM text using fuzzy string matching (the stripped-markdown text may differ slightly from the rendered HTML text)
4. Wrap matched regions in `<span class="tts-sentence" data-idx="0">` elements
5. Cache the mapping: `sentenceSpans[idx] → { el, start_ms, end_ms }`

**Only wrap text when the user plays audio.** Don't modify the DOM for readers who aren't listening. Remove spans when audio stops.

### Highlighting during playback

```js
var currentIdx = -1;

audio.addEventListener('timeupdate', function () {
  var ms = audio.currentTime * 1000;

  // Binary search for active sentence (sorted by start_ms)
  var idx = binarySearchSentence(timing.sentences, ms);
  if (idx === currentIdx) return;

  // Remove previous highlight
  if (currentIdx >= 0 && sentenceSpans[currentIdx]) {
    sentenceSpans[currentIdx].el.classList.remove('tts-sentence-active');
    sentenceSpans[currentIdx].el.style.removeProperty('--tts-progress');
  }

  // Apply new highlight
  currentIdx = idx;
  if (idx >= 0 && sentenceSpans[idx]) {
    sentenceSpans[idx].el.classList.add('tts-sentence-active');
  }
});

// Gradient sweep: update progress within active sentence at 60fps
function updateSweep() {
  if (currentIdx >= 0 && sentenceSpans[currentIdx]) {
    var s = timing.sentences[currentIdx];
    var ms = audio.currentTime * 1000;
    var progress = Math.min(1, Math.max(0, (ms - s.start_ms) / (s.end_ms - s.start_ms)));
    sentenceSpans[currentIdx].el.style.setProperty(
      '--tts-progress', (progress * 100) + '%'
    );
  }
  if (!audio.paused) requestAnimationFrame(updateSweep);
}
```

### CSS

```css
/* Active sentence — gradient sweep from left to right */
.tts-sentence-active {
  background: linear-gradient(90deg,
    var(--tts-highlight-bg, rgba(66, 133, 244, 0.10)) var(--tts-progress, 0%),
    transparent var(--tts-progress, 0%)
  );
  border-radius: 2px;
  transition: background-position 60ms linear;
}

/* Context dimming (togglable) */
.tts-playing .reader-content {
  color: var(--text-muted);
  transition: color 300ms ease;
}
.tts-playing .tts-sentence-active {
  color: var(--text);
}

/* Crossfade between sentences */
.tts-sentence {
  transition: opacity 200ms ease, color 200ms ease;
}

/* Theme-specific highlight colors */
[data-reader-theme="light"] { --tts-highlight-bg: rgba(66, 133, 244, 0.10); }
[data-reader-theme="sepia"] { --tts-highlight-bg: rgba(139, 90, 43, 0.12); }
[data-reader-theme="dark"]  { --tts-highlight-bg: rgba(130, 177, 255, 0.12); }
```

### Auto-scroll (Readwise pattern)

```js
var userScrolledAway = false;
var programmaticScroll = false;
var returnButtonVisible = false;

window.addEventListener('scroll', function () {
  if (programmaticScroll) return;
  userScrolledAway = true;
  returnButtonVisible = true;
});

// During timeupdate:
if (!userScrolledAway && activeSentenceEl) {
  programmaticScroll = true;
  activeSentenceEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
  requestAnimationFrame(() => { programmaticScroll = false; });
}
```

When `userScrolledAway` is true, show a floating pill button: "Return to audio". Tapping it scrolls to the current highlight and resets `userScrolledAway`. The button auto-hides after 8 seconds of inactivity.

### Click-to-seek

Every sentence span gets a click handler during audio playback:

```js
span.addEventListener('click', function () {
  audio.currentTime = timing.sentences[this.dataset.idx].start_ms / 1000;
});
```

Cursor becomes a pointer on `.tts-sentence` elements during playback.

### Reader toolbar integration

Add an "Immersive" toggle to the ReaderToolbar (only visible when audio is playing or ready):

```
[Immersive: Off | On]
```

When on: highlighting, auto-scroll, click-to-seek, and context dimming are all active. When off: audio plays but text is static (current behavior).

Persist preference in `readerPrefs`.

## Data model changes

### R2 storage

New key: `articles/{article_id}/audio-timing.json`

### D1 schema

No schema change needed. The presence/absence of the timing file in R2 is sufficient. The API endpoint returns 404 when no timing exists, and the client gracefully degrades.

## Implementation phases

### Phase A: Backend timing generation

1. Add `_ogg_duration_seconds()` to `tts/processing.py` — pure byte parsing, no dependencies
2. Refactor `chunk_text()` → `chunk_text_with_sentences()` preserving sentence lists per chunk
3. After TTS concatenation, build timing manifest from measured chunk durations
4. Store `audio-timing.json` in R2 alongside the audio
5. Add `GET /api/articles/{id}/audio-timing` endpoint in `tts/routes.py`
6. Unit tests: timing manifest accuracy, OGG duration parsing, proportional distribution

### Phase B: Frontend sentence highlighting

1. Fetch timing data when audio starts (lazy, cached)
2. DOM text matching — map timing sentences to rendered content
3. Wrap matched sentences in spans
4. `timeupdate` handler with binary search for active sentence
5. Gradient sweep animation at 60fps via `requestAnimationFrame`
6. Click-to-seek on sentence spans
7. Auto-scroll with user-scroll detection
8. "Return to audio" floating button
9. Immersive toggle in ReaderToolbar
10. Theme-aware highlight colors

### Phase C: Polish

1. Context dimming toggle
2. Crossfade transitions between sentences
3. Handle edge cases: very short sentences (<0.5s), very long sentences (>10s)
4. Timing for articles with existing audio: regenerate audio to get timing, or accept no-highlight gracefully
5. Visual indicator on article cards when immersive reading is available

## Future: word-level upgrade path

If Tasche switches TTS providers in the future, the manifest format extends naturally:

```json
{
  "version": 2,
  "total_duration_ms": 48200,
  "sentences": [
    {
      "text": "Mary had a little lamb.",
      "start_ms": 0,
      "end_ms": 3200,
      "words": [
        { "text": "Mary", "start_ms": 0, "end_ms": 450 },
        { "text": "had", "start_ms": 480, "end_ms": 720 }
      ]
    }
  ]
}
```

The frontend already maps timing entries to DOM spans. Adding word-level spans within sentence spans, and a second highlight class (`.tts-word-active`), is a mechanical extension. The auto-scroll, click-to-seek, and toolbar integration all work unchanged.

Candidate providers that return native word-level timing:
- **ElevenLabs** — character-level timestamps, highest quality voices
- **Amazon Polly** — word + sentence speech marks, battle-tested
- **Azure Speech** — `WordBoundary` SDK events, extensive language support
- **Unreal Speech** — word timestamps, cost-effective (what Readwise uses)
- **Cartesia Sonic** — word + phoneme timing, low latency

Any of these can be called from the queue handler via `http_fetch()`. The manifest format is provider-agnostic.

## Cost analysis

| Component | Cost | Notes |
|-----------|------|-------|
| Sentence-level timing | **Free** | Duration extracted by parsing existing audio bytes |
| R2 storage for timing JSON | Negligible | <5KB per article |
| Additional TTS API calls | None | Same number of chunks as today |
| Frontend JS overhead | Minimal | Binary search + rAF loop during playback only |

## Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Character-proportional timing drifts >500ms | Acceptable for sentence-level; gradient sweep visually masks drift |
| DOM text doesn't match stripped-markdown text | Fuzzy matching with Levenshtein distance; skip unmatched sentences |
| Wrapping spans breaks article layout | Use `display: inline` spans; test all reader themes and fonts |
| `timeupdate` too infrequent for smooth sweep | Gradient progress updated via `requestAnimationFrame`, not `timeupdate` |
| Large articles (50K+ words) | Lazy-load timing by visible section; binary search stays O(log n) |
| OGG Opus pages without granule position | Fall back to character-count estimation (current `_estimate_duration` logic) |

## Success criteria

1. User plays audio and sees the current sentence highlighted without any setup or extra cost
2. Gradient sweep creates the illusion of continuous motion within each sentence
3. Highlighting stays in sync across all playback speeds (0.75x–2x)
4. User taps a sentence and audio jumps there
5. User scrolls away during playback; a "return to audio" button appears
6. Articles with existing audio (no timing data) work identically to today
7. Highlighting works across all reader themes (auto/light/sepia/dark) and font settings
8. The manifest format supports future word-level data as a non-breaking extension
