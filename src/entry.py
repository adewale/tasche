"""Cloudflare Worker entrypoint for Tasche.

This module wires the FastAPI application to the Workers runtime via the
built-in ASGI adapter and exposes the ``Default`` WorkerEntrypoint class
that Wrangler discovers automatically.

Queue messages are dispatched to the appropriate handler based on the
``type`` field in each message body.
"""

from __future__ import annotations

import json

from wide_event import begin_event, current_event, emit_event
from wrappers import SafeEnv, _to_py_safe

# ---------------------------------------------------------------------------
# HAS_PYODIDE guard — allows this module to be imported during tests even
# when the ``workers`` and ``asgi`` packages are not available.
# ---------------------------------------------------------------------------

HAS_PYODIDE = False

try:
    import asgi  # type: ignore[import-not-found]
    from workers import WorkerEntrypoint  # type: ignore[import-not-found]

    HAS_PYODIDE = True
except ImportError:

    class WorkerEntrypoint:  # type: ignore[no-redef]
        """Stub for test environments where the Workers runtime is absent."""

        env: object = None

    asgi = None  # type: ignore[assignment]

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from observability import ObservabilityMiddleware  # noqa: E402
from security import SecurityHeadersMiddleware  # noqa: E402

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Tasche",
    description="A self-hosted read-it-later service on Cloudflare Workers",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# Middleware stack — order matters!
#
# FastAPI's add_middleware() prepends each middleware, so the LAST one added
# becomes the OUTERMOST layer.  We want:
#   1. ObservabilityMiddleware  (outermost — logs every request)
#   2. SecurityHeadersMiddleware
#   3. CORSMiddleware           (innermost — adds CORS before security headers)
#
# Therefore we add them in reverse order: CORS first, security second,
# observability last.
# ---------------------------------------------------------------------------

# 3. CORSMiddleware (innermost) — covers local dev; same-origin production
#    requests don't trigger CORS preflight.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)

# 2. SecurityHeadersMiddleware — appends security headers to all responses.
app.add_middleware(SecurityHeadersMiddleware)

# 1. ObservabilityMiddleware (outermost) — emits one wide event per request.
app.add_middleware(ObservabilityMiddleware)

# ---------------------------------------------------------------------------
# Health-check route (always available)
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Simple liveness probe."""
    return {"status": "ok"}


@app.get("/api/health/config")
async def health_config(request: Request) -> dict:
    """Verify that all required bindings and secrets are configured.

    Returns a list of checks with their status (ok/missing) and an overall
    status: ``ok`` (everything present), ``degraded`` (optional items missing),
    or ``error`` (required items missing).
    """
    env = request.scope.get("env")

    # (name, required, description)
    _BINDINGS = [
        ("DB", True, "D1 database"),
        ("CONTENT", True, "R2 bucket for article content"),
        ("SESSIONS", True, "KV namespace for auth sessions"),
        ("ARTICLE_QUEUE", True, "Queue for async processing"),
        ("AI", True, "Workers AI binding for TTS"),
    ]
    _VARS = [
        ("SITE_URL", True, "Base URL for auth callbacks and bookmarklet"),
        ("ALLOWED_EMAILS", True, "Comma-separated list of allowed emails"),
        ("GITHUB_CLIENT_ID", True, "GitHub OAuth app client ID"),
        ("GITHUB_CLIENT_SECRET", True, "GitHub OAuth app client secret"),
        ("CF_ACCOUNT_ID", False, "Cloudflare account ID for Browser Rendering"),
        ("CF_API_TOKEN", False, "Cloudflare API token for Browser Rendering"),
    ]

    checks = []
    has_required_missing = False
    has_optional_missing = False

    for name, required, description in _BINDINGS + _VARS:
        val = getattr(env, name, None) if env else None
        present = val is not None and val != ""
        status = "ok" if present else "missing"

        if not present:
            if required:
                has_required_missing = True
            else:
                has_optional_missing = True

        checks.append({
            "name": name,
            "required": required,
            "status": status,
            "description": description,
        })

    if has_required_missing:
        overall = "error"
    elif has_optional_missing:
        overall = "degraded"
    else:
        overall = "ok"

    return {"status": overall, "checks": checks}


# ---------------------------------------------------------------------------
# Router includes — placeholders for later phases
# ---------------------------------------------------------------------------
# Phase 2: auth router
from auth.routes import router as auth_router  # noqa: E402

app.include_router(auth_router, prefix="/api/auth", tags=["auth"])

# Phase 3: articles router
from articles.routes import router as articles_router  # noqa: E402

app.include_router(articles_router, prefix="/api/articles", tags=["articles"])

# Data export router
from articles.export import router as export_router  # noqa: E402

app.include_router(export_router, prefix="/api/export", tags=["export"])

# Phase 5: tags router
from tags.routes import article_tags_router  # noqa: E402
from tags.routes import router as tags_router  # noqa: E402

app.include_router(tags_router, prefix="/api/tags", tags=["tags"])
app.include_router(article_tags_router, prefix="/api/articles", tags=["tags"])

# Tag rules router (auto-tagging)
from tags.rules import router as tag_rules_router  # noqa: E402

app.include_router(tag_rules_router, prefix="/api/tag-rules", tags=["tag-rules"])

# Phase 5: search router
from search.routes import router as search_router  # noqa: E402

app.include_router(search_router, prefix="/api/search", tags=["search"])

# Phase 6: TTS router
from tts.routes import router as tts_router  # noqa: E402

app.include_router(tts_router, prefix="/api/articles", tags=["tts"])

# Stats router
from stats.routes import router as stats_router  # noqa: E402

app.include_router(stats_router, prefix="/api/stats", tags=["stats"])


# ---------------------------------------------------------------------------
# Queue message handlers
# ---------------------------------------------------------------------------


async def _handle_article_processing(message_body: dict, env: object) -> None:
    """Process an article-processing queue message.

    Delegates to the content processing pipeline (Phase 4).
    """
    from articles.processing import process_article

    article_id = message_body.get("article_id")
    original_url = message_body.get("url", "")

    evt = current_event()
    if evt:
        evt.set("article_id", article_id)

    if not article_id or not original_url:
        if evt:
            evt.set("outcome", "skipped")
            evt.set("skip_reason", "missing article_id or url")
        return

    await process_article(article_id, original_url, env)


async def _handle_tts_generation(message_body: dict, env: object) -> None:
    """Process a TTS generation queue message.

    Delegates to the TTS processing pipeline (Phase 6).
    """
    from tts.processing import process_tts

    article_id = message_body.get("article_id")
    user_id = message_body.get("user_id")

    evt = current_event()
    if evt:
        evt.set("article_id", article_id)

    if not article_id or not user_id:
        if evt:
            evt.set("outcome", "skipped")
            evt.set("skip_reason", "missing article_id or user_id")
        return

    await process_tts(article_id, env, user_id=user_id)


QUEUE_HANDLERS: dict[str, object] = {
    "article_processing": _handle_article_processing,
    "tts_generation": _handle_tts_generation,
}


# ---------------------------------------------------------------------------
# Worker entrypoint
# ---------------------------------------------------------------------------


class Default(WorkerEntrypoint):
    """Primary Worker entrypoint discovered by Wrangler.

    * ``fetch`` — delegates HTTP requests to the FastAPI app via the ASGI adapter.
    * ``queue`` — processes batched queue messages for article processing and TTS.
    """

    async def fetch(self, request: object) -> object:  # type: ignore[override]
        """Handle an incoming HTTP request.

        API routes (``/api/``) are handled by the FastAPI app via ASGI.
        All other requests are served from static assets (ASSETS binding),
        with a fallback to ``/index.html`` for SPA client-side routing.
        """
        from js import URL  # type: ignore[import-not-found]
        from js import Request as JsRequest  # type: ignore[import-not-found]

        url = URL.new(request.url)
        path = url.pathname

        # API routes → FastAPI (wrap env so handlers get Safe* bindings)
        if path.startswith("/api/"):
            return await asgi.fetch(app, request.js_object, SafeEnv(self.env))

        # Static assets → ASSETS binding with SPA fallback
        asset_resp = await self.env.ASSETS.fetch(request.js_object)
        if asset_resp.status != 404:
            return asset_resp

        # SPA fallback: serve index.html for unmatched paths
        # Construct a proper Request to preserve headers (e.g. Accept, cookies)
        index_url = URL.new("/index.html", request.url)
        index_request = JsRequest.new(index_url, request.js_object)
        return await self.env.ASSETS.fetch(index_request)

    async def scheduled(self, event: object) -> None:
        """Handle a Cron Trigger event.

        Runs periodic health checks on articles whose original_status is
        'unknown' or hasn't been checked in 30+ days, and refreshes all
        active RSS/Atom feed subscriptions.
        """
        from datetime import UTC, datetime

        evt = begin_event("scheduled")
        try:
            from articles.health import check_original_url

            env = SafeEnv(self.env)
            db = env.DB

            rows = await db.prepare(
                "SELECT id, original_url FROM articles "
                "WHERE (original_status = 'unknown' "
                "OR last_checked_at IS NULL "
                "OR last_checked_at < datetime('now', '-30 days')) "
                "ORDER BY last_checked_at ASC NULLS FIRST "
                "LIMIT 10"
            ).all()

            checked = 0
            for row in rows:
                try:
                    new_status = await check_original_url(row["original_url"])
                except Exception:
                    new_status = "unknown"

                now = datetime.now(UTC).isoformat()
                await (
                    db.prepare(
                        "UPDATE articles SET original_status = ?, last_checked_at = ?, "
                        "updated_at = ? WHERE id = ?"
                    )
                    .bind(new_status, now, now, row["id"])
                    .run()
                )
                checked += 1

            evt.set("articles_checked", checked)
            evt.set("outcome", "success")

        except Exception as exc:
            evt.set("outcome", "error")
            evt.set("error.type", type(exc).__name__)
            evt.set("error.message", str(exc)[:500])
        finally:
            emit_event(evt, force=True)

    async def queue(self, batch: object, env: object = None, ctx: object = None) -> None:  # type: ignore[override]
        """Handle a batch of queue messages.

        Each message is expected to have a JSON body with at least a ``type``
        field that maps to one of the registered ``QUEUE_HANDLERS``.

        Parameters
        ----------
        batch:
            The ``MessageBatch`` object from the Workers runtime.  Contains a
            ``messages`` iterable, each with a ``.body`` attribute.
        env:
            Worker env bindings (also available as ``self.env``).
        ctx:
            Execution context (also available as ``self.ctx``).
        """
        # Prefer the explicitly-passed env (raw handler signature) over
        # self.env which WorkerEntrypoint may or may not populate for queue
        # invocations.  Wrap in SafeEnv so handlers get Safe* bindings.
        worker_env = SafeEnv(env if env is not None else self.env)

        for message in batch.messages:  # type: ignore[attr-defined]
            evt = None
            try:
                raw_body = message.body
                body = _to_py_safe(raw_body)
                if isinstance(body, str):
                    body = json.loads(body)
                msg_type = body.get("type", "unknown")

                evt = begin_event("queue", queue_message_type=msg_type)

                handler = QUEUE_HANDLERS.get(msg_type)
                if handler is None:
                    evt.set("outcome", "skipped")
                    evt.set("skip_reason", "unknown_type")
                    message.ack()
                    continue

                await handler(body, worker_env)
                # Pipeline sets outcome; default to success if not set
                if "outcome" not in evt._fields:
                    evt.set("outcome", "success")
                message.ack()

            except Exception as exc:
                if evt:
                    evt.set("outcome", "error")
                    evt.set("error.type", type(exc).__name__)
                    evt.set("error.message", str(exc)[:500])
                message.retry()
            finally:
                if evt:
                    emit_event(evt, force=True)
