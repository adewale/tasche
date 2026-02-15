"""Cloudflare Worker entrypoint for Tasche.

This module wires the FastAPI application to the Workers runtime via the
built-in ASGI adapter and exposes the ``Default`` WorkerEntrypoint class
that Wrangler discovers automatically.

Queue messages are dispatched to the appropriate handler based on the
``type`` field in each message body.
"""

from __future__ import annotations

import json
import traceback

from wrappers import _to_py_safe

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

from fastapi import FastAPI  # noqa: E402

from observability import ObservabilityMiddleware  # noqa: E402

# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Tasche",
    description="A self-hosted read-it-later service on Cloudflare Workers",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# Observability middleware (Phase 8) — added before routers so it wraps all
# routes and emits one wide event (canonical log line) per request.
# ---------------------------------------------------------------------------

app.add_middleware(ObservabilityMiddleware)

# ---------------------------------------------------------------------------
# Health-check route (always available)
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health() -> dict[str, str]:
    """Simple liveness probe."""
    return {"status": "ok"}


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

# Phase 5: search router
from search.routes import router as search_router  # noqa: E402

app.include_router(search_router, prefix="/api/search", tags=["search"])

# Phase 6: TTS router
from tts.routes import router as tts_router  # noqa: E402

app.include_router(tts_router, prefix="/api/articles", tags=["tts"])


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

    if not article_id or not original_url:
        print(
            json.dumps({
                "event": "article_processing",
                "article_id": article_id,
                "status": "skipped",
                "reason": "missing article_id or url",
            })
        )
        return

    await process_article(article_id, original_url, env)


async def _handle_tts_generation(message_body: dict, env: object) -> None:
    """Process a TTS generation queue message.

    Delegates to the TTS processing pipeline (Phase 6).
    """
    from tts.processing import process_tts

    article_id = message_body.get("article_id")

    if not article_id:
        print(
            json.dumps({
                "event": "tts_generation",
                "article_id": article_id,
                "status": "skipped",
                "reason": "missing article_id",
            })
        )
        return

    await process_tts(article_id, env)


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

        Parameters
        ----------
        request:
            The incoming ``Request`` object from the Workers runtime.  We pass
            ``request.js_object`` (the raw JS Request) to the ASGI adapter.
        """
        return await asgi.fetch(app, request.js_object, self.env)

    async def queue(self, batch: object) -> None:  # type: ignore[override]
        """Handle a batch of queue messages.

        Each message is expected to have a JSON body with at least a ``type``
        field that maps to one of the registered ``QUEUE_HANDLERS``.

        Parameters
        ----------
        batch:
            The ``MessageBatch`` object from the Workers runtime.  Contains a
            ``messages`` iterable, each with a ``.body`` attribute.
        """
        for message in batch.messages:  # type: ignore[attr-defined]
            try:
                raw_body = message.body
                if hasattr(raw_body, "to_py"):
                    body = raw_body.to_py()
                elif isinstance(raw_body, str):
                    body = json.loads(raw_body)
                else:
                    body = _to_py_safe(raw_body)
                msg_type = body.get("type", "unknown")

                handler = QUEUE_HANDLERS.get(msg_type)
                if handler is None:
                    print(
                        json.dumps(
                            {
                                "event": "queue_unknown_type",
                                "type": msg_type,
                                "status": "skipped",
                            }
                        )
                    )
                    message.ack()
                    continue

                await handler(body, self.env)
                message.ack()

            except Exception:
                print(
                    json.dumps(
                        {
                            "event": "queue_error",
                            "error": traceback.format_exc(),
                        }
                    )
                )
                message.retry()
