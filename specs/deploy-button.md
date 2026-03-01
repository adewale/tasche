# Deploy Button Spec

> Make the "Deploy to Cloudflare" button produce a **working** Tasche instance with minimal post-deploy steps.

**Status:** Implemented
**Date:** 2026-02-27 (implemented 2026-03-01)

---

## Goal

A user clicks the Deploy button, fills in prompted values on Cloudflare's setup page, and lands on a functional Tasche instance within 5 minutes. The only irreducible manual steps are:

1. **Creating a GitHub OAuth App** (external to Cloudflare)
2. **(Optional)** Deploying the readability-worker for better content extraction

Everything else — resource provisioning, D1 migrations, frontend build, secret configuration — should be handled by the deploy flow or prompted during setup.

---

## How the Deploy Button Works

When a user clicks the button, Cloudflare's deploy flow performs these steps:

1. **Fork repository** into the user's GitHub account
2. **Parse `wrangler.jsonc`** to determine required Cloudflare resources
3. **Auto-provision resources** — D1 databases, R2 buckets, KV namespaces, Queues, Workers AI bindings are created automatically (no resource IDs needed in config)
4. **Show setup page** — prompts for environment variables and secrets:
   - **Vars** from `wrangler.jsonc` `vars` section (shown with current defaults)
   - **Secrets** from `.dev.vars.example` (user fills in values)
   - **Descriptions** from `package.json` `cloudflare.bindings` (shown as help text)
5. **Run build command** — executes `npm run build` from root `package.json`
6. **Run deploy command** — executes `npm run deploy` from root `package.json`
7. **Enable Workers Builds** — CI/CD auto-deploys on every push to main

### What it does NOT do

- Deploy Service Binding target Workers (e.g. readability-worker)
- Run D1 migrations automatically (must be in the deploy script)
- Support `--env` flags (always targets the default environment)
- Handle multi-worker monorepos in a single click

### Sources

- [Deploy to Cloudflare buttons](https://developers.cloudflare.com/workers/platform/deploy-buttons/)
- [Deploy a Workers application in seconds](https://blog.cloudflare.com/deploy-workers-applications-in-seconds/)
- [Deploy buttons now support environment variables and secrets](https://developers.cloudflare.com/changelog/2025-07-01-workers-deploy-button-supports-environment-variables-and-secrets/)
- [Automatic resource provisioning for KV, R2, and D1](https://developers.cloudflare.com/changelog/post/2025-10-24-automatic-resource-provisioning/)
- [Workers Builds configuration](https://developers.cloudflare.com/workers/ci-cd/builds/configuration/)
- [D1 Migrations](https://developers.cloudflare.com/d1/reference/migrations/)

---

## Previous State — What Was Broken (all fixed)

The deploy button previously produced a broken instance. All issues below have been resolved:

| # | Gap | Fix | Commit |
|---|-----|-----|--------|
| 1 | No root `package.json` | Created with build/deploy scripts | `4cbde1e` |
| 2 | D1 migrations not applied | Deploy script runs migrations first | `4cbde1e` |
| 3 | `DISABLE_AUTH=true` in default env | Removed from default and staging envs | `84bb779` |
| 4 | `SITE_URL` is a placeholder | Auto-detection from Host header | `4cbde1e` |
| 5 | `ALLOWED_EMAILS` is empty | Moved to secret, prompted during deploy | `dfa6bd5` |
| 6 | READABILITY service binding in default env | Removed from default env | `4cbde1e` |
| 7 | `deploy.json` is stale | Deleted | `4cbde1e` |
| 8 | `.dev.vars.example` incomplete | Added ALLOWED_EMAILS, DISABLE_AUTH | `cf07ef6` |
| 9 | No first-boot guidance | Setup checklist on login page via `/api/health/config` | `b830642` |

---

## Changes Required

### 1. Create root `package.json`

The deploy button reads `package.json` for build/deploy scripts and binding descriptions. Tasche currently has no root `package.json` (it's a Python project using `pyproject.toml`).

```json
{
  "name": "tasche",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "build": "cd frontend && npm install && npm run build",
    "deploy": "npx wrangler d1 migrations apply DB --remote && npx pywrangler deploy",
    "db:migrate": "npx wrangler d1 migrations apply DB --remote"
  },
  "cloudflare": {
    "bindings": {
      "GITHUB_CLIENT_ID": {
        "description": "GitHub OAuth App **Client ID**. Create one at [github.com/settings/developers](https://github.com/settings/developers). Set the Callback URL to `<your-worker-url>/api/auth/callback`."
      },
      "GITHUB_CLIENT_SECRET": {
        "description": "GitHub OAuth App **Client Secret** from the same OAuth App."
      },
      "ALLOWED_EMAILS": {
        "description": "Your GitHub email address (the one you log in with). Comma-separated for multiple users."
      },
      "CF_ACCOUNT_ID": {
        "description": "*(Optional)* Cloudflare Account ID — enables Browser Rendering for JS-heavy pages. Find it on the Workers dashboard sidebar."
      },
      "CF_API_TOKEN": {
        "description": "*(Optional)* Cloudflare API token — enables Browser Rendering. Create at dash.cloudflare.com/profile/api-tokens."
      }
    }
  }
}
```

**Design decisions:**

- **D1 migrations use the binding name `DB`**, not the database name. Per Cloudflare's recommendation, binding names are stable across deployments while database names are user-chosen during provisioning.
- **`--remote` flag** is essential — applies migrations to the deployed D1 instance, not a local one.
- **`cloudflare.bindings` descriptions** support Markdown formatting and are shown in the deploy setup UI.

**Open question: `pywrangler` availability.** Workers Builds has Node.js and Python. The `pywrangler` command requires `workers-py` to be installed. If `npx pywrangler` doesn't resolve, the fallback is:
```json
"deploy": "npx wrangler d1 migrations apply DB --remote && pip install workers-py && pywrangler deploy"
```
This needs validation in the Workers Builds environment before implementation.

### 2. Update `.dev.vars.example`

The deploy button reads this file to determine which secrets to prompt for. Currently only lists GitHub OAuth credentials.

```env
# GitHub OAuth credentials (required)
# Create at https://github.com/settings/developers
# Set Callback URL to: <your-worker-url>/api/auth/callback
GITHUB_CLIENT_ID=your_github_client_id
GITHUB_CLIENT_SECRET=your_github_client_secret

# Your GitHub email for login (required)
ALLOWED_EMAILS=your@email.com

# Browser Rendering (optional — enables JS-heavy page screenshots)
# CF_ACCOUNT_ID=your_account_id
# CF_API_TOKEN=your_api_token
```

**Note:** `ALLOWED_EMAILS` is a secret (set via `npx wrangler secret put ALLOWED_EMAILS`), not a var in `wrangler.jsonc`. Adding it to `.dev.vars.example` ensures local dev works and the deploy button prompts the user.

### 3. Update `wrangler.jsonc` default environment

Current default env has three problems:

```jsonc
// CURRENT (broken for deploy button):
"vars": { "DISABLE_AUTH": "true", "ALLOWED_EMAILS": "", "SITE_URL": "https://tasche.<your-subdomain>.workers.dev" }
```

**Changes:**

| Field | Current | New | Why |
|-------|---------|-----|-----|
| `DISABLE_AUTH` | `"true"` | **Remove** | Deploy-button users need auth enabled. Keep only in staging env. |
| `SITE_URL` | Placeholder | `""` (empty) | Auto-detection fills it in from the Host header (see §4). |
| `ALLOWED_EMAILS` | `""` | **Remove from vars** | Now a secret; user sets via `npx wrangler secret put ALLOWED_EMAILS`. |
| `services` (READABILITY) | Present | **Remove** from default env | Undeployed Worker causes binding error. Keep in production/staging only. |

```jsonc
// NEW:
"vars": { "SITE_URL": "" }
```

The staging env retains `DISABLE_AUTH: "true"` and the READABILITY service binding. The production env retains the READABILITY service binding.

### 4. SITE_URL auto-detection

The biggest friction point. Users can't know their workers.dev URL before deploying, but `SITE_URL` drives OAuth callbacks and cookie security.

**Solution:** Derive `site_url` from the request's `Host` header when the configured value is empty or contains the placeholder string.

**Files affected:**

- `src/auth/routes.py` — `login()`, `callback()`, `logout()` all read `SITE_URL`
- Cookie `secure` flag derivation (currently `site_url.startswith("https://")`)
- `src/entry.py` — `/api/health/config` should report "auto-detected" instead of "missing"

**Implementation pattern:**

```python
def _get_site_url(env, request: Request) -> str:
    """Resolve SITE_URL from config or auto-detect from request Host header."""
    site_url = env.get("SITE_URL", "")
    if not site_url or "<your-subdomain>" in site_url:
        host = request.headers.get("host", "")
        scheme = request.url.scheme or "https"
        site_url = f"{scheme}://{host}"
    return site_url.rstrip("/")
```

Extract this as a shared helper used by all three auth routes. The auto-detected URL is correct for workers.dev deployments and custom domains alike.

**Caveat:** The GitHub OAuth App callback URL must still match. The user sets this when creating the OAuth App. Auto-detection ensures the _app's own_ redirect_uri matches what it sends to GitHub, but the OAuth App config is manual.

### 5. Make READABILITY service binding optional in default env

The readability-worker must be deployed separately. Deploy-button users won't have it.

**Current behavior:** `src/articles/extraction.py` already falls back to BeautifulSoup when the Readability service binding is absent or fails. This fallback produces ~70% fidelity compared to Mozilla Readability.

**Change:** Remove the `services` array from the default environment in `wrangler.jsonc`. The binding is simply absent for deploy-button users. No code changes needed — the fallback path already works.

**Post-deploy enhancement:** Document how to deploy the readability-worker and add the service binding for better extraction quality. The readability-worker lives at `readability-worker/` in the same repo.

### 6. Delete `deploy.json`

The `deploy.json` file at the project root is an older deploy-button configuration format. The current deploy button reads `wrangler.jsonc` directly. The `deploy.json` is stale (missing AI binding, missing service binding) and keeping both files risks divergence.

**Action:** Delete `deploy.json`.

### 7. Update README deploy button URL

Current:
```
https://deploy.workers.cloudflare.com/?url=https://github.com/adewale/tasche
```

New:
```
https://deploy.workers.cloudflare.com/?url=https://github.com/adewale/tasche&paid=true
```

The `&paid=true` parameter indicates that Tasche requires the Workers Paid plan ($5/month). D1, R2, Queues, and Workers AI all require it. This sets user expectations before they start the deploy flow.

### 8. Update `.gitignore`

The root `.gitignore` has `package-lock.json` (ignore all) with `!frontend/package-lock.json` (allow frontend). Creating a root `package.json` will generate a root `package-lock.json` that Workers Builds needs for reproducible installs.

**Add:** `!/package-lock.json` to un-ignore the root lockfile.

### 9. First-boot setup checklist (frontend)

After deploy, if auth secrets are missing, clicking "Sign in with GitHub" returns a 500 error with no guidance.

**Solution:** The Login view calls `/api/health/config` on mount. If `status === "error"`, show a setup checklist instead of the sign-in button.

**Checklist items:**

| Item | Source | Required |
|------|--------|----------|
| GitHub OAuth App created | GITHUB_CLIENT_ID present | Yes |
| Client secret set | GITHUB_CLIENT_SECRET present | Yes |
| Email whitelist configured | ALLOWED_EMAILS non-empty | Yes |
| Readability Worker deployed | READABILITY binding present | No |
| Browser Rendering enabled | CF_ACCOUNT_ID + CF_API_TOKEN present | No |

Each item shows its status (configured / missing) with a brief instruction for the missing ones. The checklist links to the GitHub OAuth App creation page and shows the callback URL the user needs to set.

---

## Post-Deploy Manual Steps (Irreducible)

These steps cannot be automated by the deploy button and must be documented clearly in both the README and the first-boot checklist:

### Step 1: Create GitHub OAuth App

1. Go to [github.com/settings/developers](https://github.com/settings/developers)
2. Click "New OAuth App"
3. Set **Homepage URL** to your Worker URL (shown after deploy completes)
4. Set **Authorization callback URL** to `<worker-url>/api/auth/callback`
5. Copy the **Client ID**
6. Generate and copy a **Client Secret**

### Step 2: Set secrets (if not prompted during deploy)

```bash
npx wrangler secret put GITHUB_CLIENT_ID
npx wrangler secret put GITHUB_CLIENT_SECRET
npx wrangler secret put ALLOWED_EMAILS
```

### Step 3: (Optional) Deploy readability-worker

For better content extraction on complex pages:

```bash
cd readability-worker
npm install
npx wrangler deploy
```

Then add the service binding to `wrangler.jsonc` and redeploy the main Worker:

```jsonc
"services": [{ "binding": "READABILITY", "service": "readability-worker", "entrypoint": "ReadabilityService" }]
```

### Step 4: (Optional) Enable Browser Rendering

For JS-heavy page screenshots and scraping:

```bash
npx wrangler secret put CF_ACCOUNT_ID
npx wrangler secret put CF_API_TOKEN
```

---

## Workers Builds CI/CD

After the initial deploy, Workers Builds is automatically configured. Every push to the main branch triggers:

1. `npm run build` — builds the Preact frontend into `./assets/`
2. `npm run deploy` — applies D1 migrations + deploys via pywrangler

Pull requests get preview deployments with unique URLs.

---

## Open Questions

These need validation before or during implementation:

### 1. Is `pywrangler` available in Workers Builds?

Workers Builds provides Node.js and Python. `pywrangler` is installed via `pip install workers-py` or `uv pip install workers-py`. If `npx pywrangler` doesn't resolve, the deploy script must install it first.

**Validation step:** Push to a Workers Builds-enabled repo and check if `npx pywrangler --version` succeeds. If not, test `pip install workers-py && pywrangler deploy`.

### 2. Should committed `assets/` stay?

Currently the built frontend is committed to `./assets/`. With the deploy button running `npm run build`, committed assets become potentially stale. Options:

- **Keep committed** (current): Manual deploys still work without a build step. Deploy button overwrites with fresh build. Stale assets in git are a minor issue.
- **Gitignore**: Forces build before every deploy. Cleaner git history. Breaks manual `pywrangler deploy` without a prior build.

**Recommendation:** Keep committed for now. The deploy button's build step overwrites them, and manual deploys continue to work.

### 3. Should READABILITY stay in-repo or move to a separate repo?

Currently at `readability-worker/` in the same repo. The deploy button can only deploy one Worker, so it's a manual post-deploy step either way.

**Recommendation:** Keep in-repo. It's small (37 lines), shares the same lifecycle, and is easier for users to find and deploy.

---

## Implementation Sequence

| # | Change | Files | Dependency |
|---|--------|-------|------------|
| 1 | SITE_URL auto-detection | `src/auth/routes.py`, `src/entry.py` | None |
| 2 | wrangler.jsonc cleanup | `wrangler.jsonc` | None |
| 3 | Create root `package.json` | `package.json` (new) | None |
| 4 | Update `.dev.vars.example` | `.dev.vars.example` | None |
| 5 | Update `.gitignore` | `.gitignore` | After #3 |
| 6 | Delete `deploy.json` | `deploy.json` (delete) | None |
| 7 | Update README button URL | `README.md` | None |
| 8 | First-boot setup checklist | `frontend/src/views/Login.jsx` | After #1 |
| 9 | Validate in Workers Builds | — | After #1-7 |
| 10 | End-to-end test | — | After #9 |

---

## Testing Strategy

1. **Unit tests** — SITE_URL auto-detection with empty/placeholder/real values
2. **Unit tests** — `/api/health/config` with partial config
3. **Frontend tests** — Setup checklist renders when health returns `"error"`
4. **Manual validation** — Click deploy button on a clean Cloudflare account
5. **Workers Builds validation** — Verify `pywrangler` availability
6. **Smoke test** — Full flow: deploy → set secrets → OAuth login → save article
