# TTS Model Comparison

Tasche uses Cloudflare Workers AI for text-to-speech. The model is configurable via the `TTS_MODEL` env var in `wrangler.jsonc`.

## Supported Models

| Key | Model ID | Provider | Price | Output | Languages |
|-----|----------|----------|-------|--------|-----------|
| `aura-2-en` (default) | `@cf/deepgram/aura-2-en` | Deepgram | $0.030/1K chars | ReadableStream (Opus/OGG) | EN |
| `aura-2-es` | `@cf/deepgram/aura-2-es` | Deepgram | $0.030/1K chars | ReadableStream (Opus/OGG) | EN, ES |
| `aura-1` | `@cf/deepgram/aura-1` | Deepgram | $0.015/1K chars | ReadableStream | EN |
| `melotts` | `@cf/myshell-ai/melotts` | MyShell | $0.0002/audio min | Base64 JSON | EN, ES, FR, ZH, JP, KR |

## Cost Comparison

For a typical 9-minute article (~1,350 words, ~7,000 characters):

| Model | Calculation | Cost |
|-------|-------------|------|
| MeloTTS | 9 min × $0.0002/min | **$0.0018** |
| Aura 1 | 7K chars × $0.015/1K | **$0.105** |
| Aura 2 | 7K chars × $0.030/1K | **$0.210** |

MeloTTS is ~115x cheaper than Aura 2 for the same article, but Aura 2's
quality is dramatically better — which is why `aura-2-en` is the shipped
default (set in all `wrangler.jsonc` environments and as the code fallback
in `src/tts/processing.py`).

## Configuration

Set `TTS_MODEL` in `wrangler.jsonc` vars (or via `wrangler secret put`):

```jsonc
"vars": { "TTS_MODEL": "aura-2-en" }
```

Valid values:
- `aura-2-en` — Deepgram Aura 2 English (default, highest quality)
- `aura-2-es` — Deepgram Aura 2 Spanish
- `aura-1` — Deepgram Aura 1 (mid-range)
- `melotts` — MyShell MeloTTS (cheapest, multilingual)
- Any raw Workers AI model ID (e.g. `@cf/deepgram/aura-2-en`)

## API Differences

The code dispatches automatically based on the model:

**MeloTTS:** Sends `{"prompt": text, "lang": "en"}`, receives `{"audio": "<base64>"}` which is decoded to bytes.

**Deepgram Aura 2:** Sends `{"text": chunk}` with `speaker` (voice, default
`athena`), `encoding: "opus"`, and `container: "ogg"`; receives a
ReadableStream of OGG Opus bytes. Chunks are re-muxed into a single logical
OGG stream and stored at `articles/{id}/audio.ogg`.

**Deepgram Aura 1:** Sends `{"text": chunk}`; receives a ReadableStream
(format detected from magic bytes at store time).

All paths chunk text at ~1,900 characters (below the 2,000-char API limit)
and concatenate the resulting audio. The stored file extension
(`audio.ogg` / `audio.wav` / `audio.mp3`) is always detected from the magic
bytes of the produced audio, not assumed from the model.

## Quality Notes

- **Aura 2** has the most natural pacing, expressiveness, and context awareness. Best for long-form articles.
- **Aura 1** is slightly less expressive but half the price of Aura 2.
- **MeloTTS** is a lightweight multilingual model. Quality is adequate for article consumption at a fraction of the cost.

The default is Aura 2 for quality. Switch to MeloTTS via `TTS_MODEL` if cost matters more than naturalness.
