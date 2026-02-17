# Tasche

A self-hosted read-it-later service built on Cloudflare Python Workers. Save articles, read them offline, and listen to them as audio -- all running in your own Cloudflare account.

[![Deploy to Cloudflare](https://deploy.workers.cloudflare.com/button)](https://deploy.workers.cloudflare.com/?url=https://github.com/adewale/tasche)

## Features

- **Save articles by URL** with automatic content extraction and archival
- **Full-text search** across your entire library (FTS5)
- **Listen Later** -- generate audio versions of articles via Workers AI TTS
- **Offline reading** -- PWA with service worker caching
- **Tags and organization** for your saved articles
- **Bookmarklet and share target** for quick saving from any browser
- **Self-hosted** -- your data stays in your Cloudflare account

## Quick Start (< 5 minutes)

### 1. Deploy to Cloudflare

Click the deploy button above, or deploy manually:

```bash
git clone https://github.com/adewale/tasche.git
cd tasche
```

### 2. Build the frontend

```bash
cd frontend && npm install && npm run build && cd ..
```

### 3. Create a GitHub OAuth App

1. Go to [github.com/settings/developers](https://github.com/settings/developers)
2. Click **OAuth Apps** > **New OAuth App**
3. Fill in:
   - **Application name:** Tasche
   - **Homepage URL:** `https://tasche.yourdomain.com`
   - **Authorization callback URL:** `https://tasche.yourdomain.com/api/auth/callback`
4. Click **Register application** and generate a client secret

### 4. Set secrets

```bash
uv run pywrangler secret put GITHUB_CLIENT_ID
uv run pywrangler secret put GITHUB_CLIENT_SECRET
```

### 5. Configure your domain

Edit `wrangler.jsonc` and update the `production` environment:

```jsonc
"production": {
  "vars": { "SITE_URL": "https://tasche.yourdomain.com" },
  "routes": [{ "pattern": "tasche.yourdomain.com", "custom_domain": true }]
}
```

### 6. Deploy

```bash
uv run pywrangler deploy --env production
```

Visit your domain, log in with GitHub, and start saving articles.

## Development

```bash
# Install Python dependencies
uv sync

# Install frontend dependencies
cd frontend && npm install && cd ..

# Build frontend (outputs to ./assets/)
cd frontend && npm run build && cd ..

# Local development server (uses Miniflare for D1/R2/KV/Queues)
uv run pywrangler dev

# Run all tests
uv run pytest tests/ -x -q

# Run unit tests only
uv run pytest tests/unit/ -x -q

# Lint
uv run ruff check src/ tests/

# Format
uv run ruff format src/ tests/
```

**Note:** Use `pywrangler` (not regular `wrangler`) for Python Workers with packages. Packages are defined in `pyproject.toml`, not `requirements.txt`.

## Architecture

Tasche runs entirely on the Cloudflare Developer Platform:

| Service | Binding | Purpose |
|---------|---------|---------|
| **Python Workers** | -- | FastAPI API + queue consumer |
| **D1** | `DB` | Articles, users, tags, FTS5 search |
| **R2** | `CONTENT` | Archived HTML, markdown, images, audio |
| **KV** | `SESSIONS` | Auth sessions with 7-day TTL |
| **Queues** | `ARTICLE_QUEUE` | Async article processing and TTS |
| **Workers AI** | -- | Text-to-speech (@cf/deepgram/aura-2-en) |

**Data flow:** Save URL > API creates article (pending) > Queue consumer fetches page > Readability extracts content > Images converted to WebP > HTML + Markdown stored in R2 > FTS5 indexed in D1.

**Frontend:** Preact SPA built with Vite, served as Workers Static Assets. PWA with offline support via service worker.

See [specs/tasche-spec.md](specs/tasche-spec.md) for the full product specification.

## PWA Icons

The repository includes placeholder PWA icons at `frontend/public/static/icon-192.png` and `frontend/public/static/icon-512.png`. To use your own icons, replace these files with 192x192 and 512x512 PNG images. An SVG source file is provided at `frontend/public/static/icon.svg` that can be used to generate PNGs with any SVG-to-PNG tool.

## Cost

Approximately **$5/month** on the Cloudflare Workers Paid plan, paid directly to Cloudflare. The free tier (100K requests/day) covers light personal use.

## License

MIT
