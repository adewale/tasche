"""Cloudflare Worker entrypoint for Tasche.

This module wires the FastAPI application to the Workers runtime via the
built-in ASGI adapter and exposes the ``Default`` WorkerEntrypoint class
that Wrangler discovers automatically.

Queue messages are dispatched to the appropriate handler based on the
``type`` field in each message body.
"""

from __future__ import annotations

import json
import re

from wide_event import begin_event, current_event, emit_event
from wrappers import SafeEnv, _to_py_safe, is_js_null

# Audio path pattern — matched before ASGI routing so large R2 objects
# are streamed directly without loading into Python memory.
_AUDIO_PATH_RE = re.compile(r"^/api/articles/([A-Za-z0-9_-]+)/audio$")

# ---------------------------------------------------------------------------
# Pyodide guard — HAS_PYODIDE is the single source of truth defined in
# wrappers.py (the FFI boundary layer).  We import it above and use it to
# conditionally load Workers-only packages.
# ---------------------------------------------------------------------------

try:
    import asgi  # type: ignore[import-not-found]
    from workers import WorkerEntrypoint  # type: ignore[import-not-found]
except ImportError:

    class WorkerEntrypoint:  # type: ignore[no-redef]
        """Stub for test environments where the Workers runtime is absent."""

        env: object = None

    asgi = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
import os as _os  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from observability import ObservabilityMiddleware  # noqa: E402
from security import SecurityHeadersMiddleware  # noqa: E402

# Disable interactive docs (/docs, /redoc, /openapi.json) outside dev mode.
# In the Workers runtime WORKER_ENV is set via wrangler.jsonc; in local dev
# or tests it is typically absent or set to "development".
_worker_env = _os.environ.get("WORKER_ENV", "")
_is_production = _worker_env == "production"

app = FastAPI(
    title="Tasche",
    description="A self-hosted read-it-later service on Cloudflare Workers",
    version="0.1.0",
    docs_url=None if _is_production else "/docs",
    redoc_url=None if _is_production else "/redoc",
    openapi_url=None if _is_production else "/openapi.json",
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

# 3. CORSMiddleware (innermost) — local dev only; production requests
#    are same-origin (bookmarklet uses window.open popup on Tasche's origin).
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

    Authenticated callers receive a detailed list of checks with their status
    (ok/missing) and an overall status.  Unauthenticated callers receive only
    the overall status without binding names, descriptions, or environment
    details -- preventing information leakage about the deployment.
    """
    from auth.dependencies import get_current_user

    # Try to authenticate -- if it fails, return a minimal response.
    is_authenticated = False
    try:
        await get_current_user(request)
        is_authenticated = True
    except Exception:
        pass

    env = request.scope.get("env")

    # (name, required, description)
    _BINDINGS = [
        ("DB", True, "D1 database"),
        ("CONTENT", True, "R2 bucket for article content"),
        ("SESSIONS", True, "KV namespace for auth sessions"),
        ("ARTICLE_QUEUE", True, "Queue for async processing"),
        ("AI", True, "Workers AI binding for TTS"),
        ("READABILITY", False, "Better content extraction (falls back to built-in parser)"),
    ]
    _VARS = [
        ("SITE_URL", False, "Base URL for auth callbacks (auto-detected if empty)"),
        ("ALLOWED_EMAILS", True, "Your GitHub email address (comma-separated for multiple users)"),
        ("GITHUB_CLIENT_ID", True, "GitHub OAuth app client ID"),
        ("GITHUB_CLIENT_SECRET", True, "GitHub OAuth app client secret"),
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

        checks.append(
            {
                "name": name,
                "required": required,
                "status": status,
                "description": description,
            }
        )

    if has_required_missing:
        overall = "error"
    elif has_optional_missing:
        overall = "degraded"
    else:
        overall = "ok"

    # Unauthenticated: return only the overall status (no binding details).
    if not is_authenticated:
        return {"status": overall}

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

# Phase 5: tags router
from tags.routes import article_tags_router  # noqa: E402
from tags.routes import router as tags_router  # noqa: E402

app.include_router(tags_router, prefix="/api/tags", tags=["tags"])
app.include_router(article_tags_router, prefix="/api/articles", tags=["tags"])

# Phase 5: search is unified into the articles list endpoint (?q= param).

# Phase 6: TTS router
from tts.routes import router as tts_router  # noqa: E402

app.include_router(tts_router, prefix="/api/articles", tags=["tts"])

# Preferences router
from preferences.routes import router as preferences_router  # noqa: E402

app.include_router(preferences_router, prefix="/api/preferences", tags=["preferences"])

# Stats router
from stats.routes import router as stats_router  # noqa: E402

app.include_router(stats_router, prefix="/api/stats", tags=["stats"])


# ---------------------------------------------------------------------------
# Queue message handlers
# ---------------------------------------------------------------------------


async def _handle_article_processing(message_body: dict, env: object) -> None:
    """Process an article-processing queue message.

    Delegates to the content processing pipeline (Phase 4).
    When ``requeue_tts`` is set in the message, chains a TTS generation
    job after text processing completes successfully — ensuring markdown
    content is available before TTS starts.
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

    # Chain TTS generation after successful text processing
    if message_body.get("requeue_tts"):
        tts_voice = message_body.get("tts_voice", "athena")
        user_id = message_body.get("user_id")
        if user_id:
            await env.ARTICLE_QUEUE.send(
                {
                    "type": "tts_generation",
                    "article_id": article_id,
                    "user_id": user_id,
                    "tts_voice": tts_voice,
                }
            )


async def _handle_tts_generation(message_body: dict, env: object) -> None:
    """Process a TTS generation queue message.

    Delegates to the TTS processing pipeline (Phase 6).
    """
    from tts.processing import process_tts

    article_id = message_body.get("article_id")
    user_id = message_body.get("user_id")
    tts_voice = message_body.get("tts_voice")

    evt = current_event()
    if evt:
        evt.set("article_id", article_id)

    if not article_id or not user_id:
        if evt:
            evt.set("outcome", "skipped")
            evt.set("skip_reason", "missing article_id or user_id")
        return

    await process_tts(article_id, env, user_id=user_id, tts_voice=tts_voice)


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

        Audio files bypass ASGI entirely — R2 objects (49 MB+ WAV) are
        streamed directly as JS Responses to avoid loading into Python
        memory across the Pyodide FFI boundary.
        """
        from js import URL  # type: ignore[import-not-found]
        from js import Request as JsRequest  # type: ignore[import-not-found]

        url = URL.new(request.url)
        path = url.pathname

        # Audio streaming: bypass ASGI so large R2 bodies never enter Python
        audio_match = _AUDIO_PATH_RE.match(path)
        if audio_match:
            return await self._serve_audio(request, audio_match.group(1))

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

    async def _authenticate_raw_request(
        self, request: object, env: SafeEnv
    ) -> tuple[str | None, str | None]:
        """Authenticate a raw JS request outside the ASGI boundary.

        Reuses ``auth.session`` logic (same as ``auth.dependencies.get_current_user``)
        but operates on raw JS request headers instead of a FastAPI ``Request``.

        Returns
        -------
        tuple[str | None, str | None]
            ``(user_id, None)`` on success, or ``(None, error_detail)`` on failure.
        """
        from auth.session import COOKIE_NAME, get_session

        disable_auth = getattr(self.env, "DISABLE_AUTH", None)
        raw_worker_env = getattr(self.env, "WORKER_ENV", None)
        if str(disable_auth) == "true" and str(raw_worker_env) != "production":
            return ("dev", None)

        cookie_raw = request.headers.get("cookie")
        cookie_header = str(cookie_raw) if cookie_raw else ""
        session_id = None
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith(COOKIE_NAME + "="):
                session_id = part[len(COOKIE_NAME) + 1 :]
                break

        if not session_id:
            return (None, "Not authenticated")

        user_data = await get_session(env.SESSIONS, session_id)
        if user_data is None:
            return (None, "Invalid or expired session")

        return (user_data.get("user_id"), None)

    async def _serve_audio(self, request: object, article_id: str) -> object:
        """Stream audio from R2, bypassing ASGI.

        Audio files can be 49 MB+ (uncompressed WAV from MeloTTS).
        Loading them into Python memory through the Pyodide FFI boundary
        (JS ReadableStream → Python bytes → ASGI Response → JS Response)
        crashes the Worker.  This handler passes the R2 ReadableStream
        directly to a JS Response — the audio data never enters Python.

        Auth is delegated to ``_authenticate_raw_request`` which shares
        the same session logic as ``auth.dependencies.get_current_user``.
        The FastAPI handler in ``tts.routes`` still exists for unit tests
        (which run in CPython via TestClient and never hit this code path).
        """
        import js  # type: ignore[import-not-found]

        env = SafeEnv(self.env)

        def _json_resp(detail: str, status: int) -> object:
            h = js.Headers.new()
            h.set("Content-Type", "application/json")
            init = js.Object.new()
            init.status = status
            init.headers = h
            return js.Response.new(json.dumps({"detail": detail}), init)

        # --- Auth (shared helper) ---
        user_id, auth_error = await self._authenticate_raw_request(request, env)
        if auth_error:
            status = 401
            return _json_resp(auth_error, status)

        # --- Article lookup ---
        article = await (
            env.DB.prepare(
                "SELECT audio_status, audio_key FROM articles WHERE id = ? AND user_id = ?"
            )
            .bind(article_id, user_id)
            .first()
        )

        if not article:
            return _json_resp("Article not found", 404)

        audio_status = article.get("audio_status")
        if audio_status != "ready":
            if audio_status in ("pending", "generating"):
                return _json_resp("Audio is still being generated", 409)
            return _json_resp("No audio available for this article", 404)

        audio_key = article.get("audio_key")
        if not audio_key:
            return _json_resp("No audio available for this article", 404)

        # --- R2 stream (raw binding — preserves JS ReadableStream) ---
        # We use the raw R2 binding here intentionally: SafeR2.get() would
        # pull the body into Python memory, but audio files can be 49 MB+.
        # The raw binding lets us pass the R2 ReadableStream directly to a
        # JS Response without the data ever entering Python.
        r2_obj = await self.env.CONTENT.get(audio_key)
        if r2_obj is None or is_js_null(r2_obj):
            return _json_resp("Audio file not found", 404)

        if audio_key.endswith(".ogg"):
            media_type = "audio/ogg"
        elif audio_key.endswith(".wav"):
            media_type = "audio/wav"
        else:
            media_type = "audio/mpeg"

        h = js.Headers.new()
        h.set("Content-Type", media_type)
        h.set("Cache-Control", "public, max-age=86400, immutable")
        r2_size = getattr(r2_obj, "size", None)
        if r2_size is not None and not is_js_null(r2_size):
            h.set("Content-Length", str(int(r2_size)))

        init = js.Object.new()
        init.status = 200
        init.headers = h
        return js.Response.new(r2_obj.body, init)

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

                enqueued_at = body.get("enqueued_at")
                if enqueued_at:
                    from datetime import UTC, datetime

                    try:
                        enq_time = datetime.fromisoformat(enqueued_at)
                        wait_ms = (datetime.now(UTC) - enq_time).total_seconds() * 1000
                        evt.set("queue.wait_ms", round(wait_ms, 2))
                    except (ValueError, TypeError):
                        pass

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
                    emit_event(evt)
