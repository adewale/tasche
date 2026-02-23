"""Newsletter email ingestion handler for Tasche.

Processes incoming emails from Cloudflare Email Workers and creates articles
from newsletter content. The email handler runs inside the same Python Worker
and receives the ``ForwardableEmailMessage`` JS object from the runtime.

Key properties of the email message object:
- ``message.from`` — envelope sender address (string)
- ``message.to`` — envelope recipient address (string)
- ``message.headers`` — JS Headers object with ``get()`` method
- ``message.raw`` — ReadableStream containing the full RFC822 email
- ``message.rawSize`` — size of the raw message (number)
"""

from __future__ import annotations

import email
import email.policy
import json
import secrets
import traceback
from datetime import UTC, datetime
from email.message import EmailMessage

from articles.email_cleanup import clean_email_html, extract_first_url
from articles.extraction import (
    calculate_reading_time,
    count_words,
    html_to_markdown,
)
from articles.storage import store_content
from wrappers import SafeEnv, consume_readable_stream


def _extract_sender_domain(sender: str) -> str:
    """Extract the domain from an email address.

    Parameters
    ----------
    sender:
        An email address string, e.g. ``"newsletter@example.com"``
        or ``"Name <newsletter@example.com>"``.

    Returns
    -------
    str
        The domain portion (e.g. ``"example.com"``), or an empty string
        if extraction fails.
    """
    # Handle "Name <email@domain.com>" format
    if "<" in sender and ">" in sender:
        addr = sender.split("<")[1].split(">")[0].strip()
    else:
        addr = sender.strip()

    if "@" in addr:
        return addr.split("@")[1].lower()
    return ""


def _extract_html_body(raw_bytes: bytes) -> str:
    """Parse a raw RFC822 email and extract the HTML body.

    Tries HTML parts first, then falls back to plain text wrapped in
    ``<pre>`` tags. Uses Python's built-in ``email`` module which is
    available in Pyodide.

    Parameters
    ----------
    raw_bytes:
        The raw email bytes (full RFC822 message including headers).

    Returns
    -------
    str
        The email body as HTML. Empty string if no body could be extracted.
    """
    msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)

    # For multipart messages, walk all parts looking for HTML
    if msg.is_multipart():
        html_body = ""
        text_body = ""
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/html" and not html_body:
                payload = part.get_content()
                if isinstance(payload, str):
                    html_body = payload
                elif isinstance(payload, bytes):
                    html_body = payload.decode("utf-8", errors="replace")
            elif content_type == "text/plain" and not text_body:
                payload = part.get_content()
                if isinstance(payload, str):
                    text_body = payload
                elif isinstance(payload, bytes):
                    text_body = payload.decode("utf-8", errors="replace")

        if html_body:
            return html_body
        if text_body:
            # Wrap plain text in basic HTML for consistent processing
            escaped = text_body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            return f"<pre>{escaped}</pre>"
        return ""

    # Single-part message
    content_type = msg.get_content_type()
    payload = msg.get_content()

    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    elif not isinstance(payload, str):
        return ""

    if content_type == "text/html":
        return payload
    if content_type == "text/plain":
        escaped = payload.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return f"<pre>{escaped}</pre>"

    return ""


def _extract_subject(msg: EmailMessage | None, headers_get=None) -> str:
    """Extract the email subject from headers.

    Tries the JS Headers object first (``headers_get``), then falls back
    to the parsed email message object.

    Parameters
    ----------
    msg:
        Parsed email.message.EmailMessage, or None.
    headers_get:
        A callable ``get(name)`` from the JS Headers object, or None.

    Returns
    -------
    str
        The email subject, or "Newsletter" as fallback.
    """
    # Try JS headers first (more reliable for the envelope)
    if headers_get is not None:
        try:
            subject = headers_get("subject")
            if subject:
                return str(subject).strip()
        except Exception:
            pass

    # Fall back to parsed email
    if msg is not None:
        subject = msg.get("Subject", "")
        if subject:
            return str(subject).strip()

    return "Newsletter"


async def process_email(message: object, env: object) -> None:
    """Process an incoming email and create an article from it.

    This is the main entry point called by the Worker's ``email()`` handler.

    Parameters
    ----------
    message:
        The ``ForwardableEmailMessage`` JS object from the Workers runtime.
        Properties: ``from``, ``to``, ``headers``, ``raw``, ``rawSize``.
    env:
        Worker environment object (will be wrapped in SafeEnv).
    """
    safe_env = SafeEnv(env)
    db = safe_env.DB
    r2 = safe_env.CONTENT

    # Extract envelope fields from the JS message object
    # Note: message.from is a reserved word in Python, accessed via getattr
    sender = str(getattr(message, "from", "") or "")

    # Get subject from JS headers
    headers = getattr(message, "headers", None)
    headers_get = getattr(headers, "get", None) if headers is not None else None

    try:
        # Read the raw email stream
        raw_stream = getattr(message, "raw", None)
        if raw_stream is None:
            print(
                json.dumps(
                    {
                        "event": "email_ingest_error",
                        "error": "No raw stream available on email message",
                        "sender": sender,
                    }
                )
            )
            return

        raw_bytes = await consume_readable_stream(raw_stream)
        if not raw_bytes:
            print(
                json.dumps(
                    {
                        "event": "email_ingest_error",
                        "error": "Empty email body",
                        "sender": sender,
                    }
                )
            )
            return

        # Parse the raw email
        parsed_msg = email.message_from_bytes(raw_bytes, policy=email.policy.default)

        # Extract subject
        subject = _extract_subject(parsed_msg, headers_get)

        # Extract HTML body
        html_body = _extract_html_body(raw_bytes)
        if not html_body:
            print(
                json.dumps(
                    {
                        "event": "email_ingest_error",
                        "error": "No HTML or text body found in email",
                        "sender": sender,
                        "subject": subject,
                    }
                )
            )
            return

        # Clean the HTML
        clean_html = clean_email_html(html_body)
        if not clean_html:
            print(
                json.dumps(
                    {
                        "event": "email_ingest_error",
                        "error": "Email body was empty after cleanup",
                        "sender": sender,
                        "subject": subject,
                    }
                )
            )
            return

        # Determine the URL to associate with this article
        # Try to find the first real URL in the email body, fall back to mailto:
        first_url = extract_first_url(html_body)
        original_url = first_url or f"mailto:{sender}"

        # Extract domain
        sender_domain = _extract_sender_domain(sender)

        # Generate article ID
        article_id = secrets.token_urlsafe(16)
        now = datetime.now(UTC).isoformat()

        # Look up the user — for single-user Tasche, get the first user
        user_row = await db.prepare("SELECT id FROM users ORDER BY created_at ASC LIMIT 1").first()

        if user_row is None:
            print(
                json.dumps(
                    {
                        "event": "email_ingest_error",
                        "error": "No users found in database",
                        "sender": sender,
                        "subject": subject,
                    }
                )
            )
            return

        user_id = user_row["id"]

        # Convert to markdown
        markdown = html_to_markdown(clean_html)
        word_count = count_words(markdown)
        reading_time = calculate_reading_time(word_count)

        # Store content.html in R2
        keys = await store_content(r2, article_id, clean_html)
        html_key = keys["html_key"]

        # Create the article in D1 with status 'ready' (no fetch needed)
        await (
            db.prepare(
                "INSERT INTO articles "
                "(id, user_id, original_url, domain, title, excerpt, "
                "word_count, reading_time_minutes, markdown_content, "
                "html_key, status, reading_status, is_favorite, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', 'unread', 0, ?, ?)"
            )
            .bind(
                article_id,
                user_id,
                original_url,
                sender_domain,
                subject,
                markdown[:300] if markdown else "",
                word_count,
                reading_time,
                markdown,
                html_key,
                now,
                now,
            )
            .run()
        )

        # Apply auto-tag rules
        try:
            from articles.processing import apply_auto_tags

            await apply_auto_tags(safe_env, article_id, sender_domain, subject, original_url)
        except Exception:
            # Non-fatal: auto-tagging failure should not block ingestion
            print(
                json.dumps(
                    {
                        "event": "email_auto_tag_failed",
                        "article_id": article_id,
                        "error": traceback.format_exc()[-500:],
                    }
                )
            )

        print(
            json.dumps(
                {
                    "event": "email_ingested",
                    "article_id": article_id,
                    "sender": sender,
                    "subject": subject,
                    "domain": sender_domain,
                    "word_count": word_count,
                    "original_url": original_url,
                }
            )
        )

    except Exception:
        print(
            json.dumps(
                {
                    "event": "email_ingest_error",
                    "sender": sender,
                    "error": traceback.format_exc()[-1000:],
                }
            )
        )
