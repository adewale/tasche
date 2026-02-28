# Tasche

A self-hosted read-it-later service built on Cloudflare Python Workers. Save articles, read them offline, and listen to them as audio -- all running in your own Cloudflare account.

[![Deploy to Cloudflare](https://deploy.workers.cloudflare.com/button)](https://deploy.workers.cloudflare.com/?url=https://github.com/adewale/tasche&paid=true)

## Features

- **Save articles by URL** with automatic content extraction and archival
- **Full-text search** across your entire library (FTS5)
- **Listen Later** -- generate audio versions of articles via Workers AI TTS
- **Offline reading** -- PWA with service worker caching
- **Tags and organization** for your saved articles
- **Bookmarklet and share target** for quick saving from any browser
- **Self-hosted** -- your data stays in your Cloudflare account

## Quick Start (< 5 minutes)

### Option A: Deploy button (fastest)

Click the **Deploy to Cloudflare** button above. After the deploy completes, your instance will be live at `https://tasche-<id>.workers.dev`. Visit it and you'll see a setup checklist. Three manual steps are needed:

1. **Create a GitHub OAuth App** at [github.com/settings/developers](https://github.com/settings/developers):
   - **Homepage URL:** your workers.dev URL (shown after deploy)
   - **Authorization callback URL:** `<your-url>/api/auth/callback`

2. **Set the OAuth secrets:**
   ```bash
   npx wrangler secret put GITHUB_CLIENT_ID
   npx wrangler secret put GITHUB_CLIENT_SECRET
   ```

3. **Set your email whitelist** (use the email on your GitHub account):
   ```bash
   npx wrangler secret put ALLOWED_EMAILS
   ```

Reload the page — the checklist clears and you can sign in.

### Option B: Deploy to workers.dev (CLI)

```bash
git clone https://github.com/adewale/tasche.git
cd tasche

# Build frontend
cd frontend && npm install && npm run build && cd ..

# Set your SITE_URL to your workers.dev subdomain
# Edit wrangler.jsonc: set vars.SITE_URL to "https://tasche.<your-subdomain>.workers.dev"

# Set GitHub OAuth secrets
uv run pywrangler secret put GITHUB_CLIENT_ID
uv run pywrangler secret put GITHUB_CLIENT_SECRET

# Deploy (uses workers.dev URL — no custom domain needed)
uv run pywrangler deploy
```

Create a GitHub OAuth App at [github.com/settings/developers](https://github.com/settings/developers):
- **Homepage URL:** `https://tasche.<your-subdomain>.workers.dev`
- **Callback URL:** `https://tasche.<your-subdomain>.workers.dev/api/auth/callback`

### Option C: Deploy to a custom domain

```bash
git clone https://github.com/adewale/tasche.git
cd tasche

# Build frontend
cd frontend && npm install && npm run build && cd ..

# Configure your domain in wrangler.jsonc production env
# Edit the "production" section: set SITE_URL and routes pattern
```

Edit `wrangler.jsonc`:

```jsonc
"production": {
  "vars": { "SITE_URL": "https://tasche.yourdomain.com" },
  "routes": [{ "pattern": "tasche.yourdomain.com", "custom_domain": true }]
}
```

```bash
# Set secrets
uv run pywrangler secret put GITHUB_CLIENT_ID --env production
uv run pywrangler secret put GITHUB_CLIENT_SECRET --env production

# Deploy
uv run pywrangler deploy --env production
```

Create a GitHub OAuth App with:
- **Homepage URL:** `https://tasche.yourdomain.com`
- **Callback URL:** `https://tasche.yourdomain.com/api/auth/callback`

### Browser Rendering (optional)

For JS-heavy pages, Tasche uses the Cloudflare Browser Rendering REST API for screenshots and content extraction. Without it, article processing falls back to plain HTTP fetches.

To enable:
1. In the Cloudflare dashboard, go to **Workers & Pages > Browser Rendering**
2. Enable Browser Rendering for your account
3. Set your account ID and API token as secrets:

```bash
uv run pywrangler secret put CF_ACCOUNT_ID
uv run pywrangler secret put CF_API_TOKEN
```

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
| **Workers AI** | -- | Text-to-speech (configurable via `TTS_MODEL`, default: MeloTTS) |

**Data flow:** Save URL > API creates article (pending) > Queue consumer fetches page > Readability extracts content > Images converted to WebP > HTML + Markdown stored in R2 > FTS5 indexed in D1.

**Frontend:** Preact SPA built with Vite, served as Workers Static Assets. PWA with offline support via service worker.

See [specs/tasche-spec.md](specs/tasche-spec.md) for the full product specification.

## PWA Icons

The repository includes placeholder PWA icons at `frontend/public/static/icon-192.png` and `frontend/public/static/icon-512.png`. To use your own icons, replace these files with 192x192 and 512x512 PNG images. An SVG source file is provided at `frontend/public/static/icon.svg` that can be used to generate PNGs with any SVG-to-PNG tool.

## Cost

Approximately **$5/month** on the Cloudflare Workers Paid plan, paid directly to Cloudflare. The free tier (100K requests/day) covers light personal use.

## License

MIT
