# TTS Model Comparison

Tasche uses Cloudflare Workers AI for text-to-speech. The model is configurable via the `TTS_MODEL` env var in `wrangler.jsonc`.

## Supported Models

| Key | Model ID | Provider | Price | Output | Languages |
|-----|----------|----------|-------|--------|-----------|
| `melotts` (default) | `@cf/myshell-ai/melotts` | MyShell | $0.0002/audio min | Base64 JSON | EN, ES, FR, ZH, JP, KR |
| `aura-2-en` | `@cf/deepgram/aura-2-en` | Deepgram | $0.030/1K chars | ReadableStream (MP3) | EN |
| `aura-2-es` | `@cf/deepgram/aura-2-es` | Deepgram | $0.030/1K chars | EN, ES |
| `aura-1` | `@cf/deepgram/aura-1` | Deepgram | $0.015/1K chars | ReadableStream (MP3) | EN |

## Cost Comparison

For a typical 9-minute article (~1,350 words, ~7,000 characters):

| Model | Calculation | Cost |
|-------|-------------|------|
| MeloTTS | 9 min × $0.0002/min | **$0.0018** |
| Aura 1 | 7K chars × $0.015/1K | **$0.105** |
| Aura 2 | 7K chars × $0.030/1K | **$0.210** |

MeloTTS is ~115x cheaper than Aura 2 for the same article.

## Configuration

Set `TTS_MODEL` in `wrangler.jsonc` vars (or via `wrangler secret put`):

```jsonc
"vars": { "TTS_MODEL": "melotts" }
```

Valid values:
- `melotts` — MyShell MeloTTS (default, cheapest)
- `aura-2-en` — Deepgram Aura 2 English (highest quality)
- `aura-2-es` — Deepgram Aura 2 Spanish
- `aura-1` — Deepgram Aura 1 (mid-range)
- Any raw Workers AI model ID (e.g. `@cf/deepgram/aura-2-en`)

## API Differences

The code dispatches automatically based on the model:

**MeloTTS:** Sends `{"prompt": text, "lang": "en"}`, receives `{"audio": "<base64>"}` which is decoded to bytes.

**Deepgram Aura (1 & 2):** Sends `{"text": text}`, receives a ReadableStream of MP3 bytes.

Both paths chunk text at ~1,900 characters (below the 2,000-char API limit) and concatenate the resulting audio.

## Quality Notes

- **Aura 2** has the most natural pacing, expressiveness, and context awareness. Best for long-form articles.
- **Aura 1** is slightly less expressive but half the price of Aura 2.
- **MeloTTS** is a lightweight multilingual model. Quality is adequate for article consumption at a fraction of the cost.

Start with MeloTTS for cost efficiency. Switch to Aura 2 if audio quality is a priority.
