"""Tests for newsletter email ingestion.

Covers email body extraction, subject extraction, sender domain parsing,
and the full process_email pipeline with mocked Cloudflare bindings.
"""

from __future__ import annotations

import email
import email.policy
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.articles.email_ingest import (
    _extract_html_body,
    _extract_sender_domain,
    _extract_subject,
    process_email,
)
from tests.conftest import MockEnv, MockR2, TrackingD1

# =========================================================================
# _extract_sender_domain
# =========================================================================


class TestExtractSenderDomain:
    def test_simple_email(self) -> None:
        """Extracts domain from a plain email address."""
        assert _extract_sender_domain("newsletter@example.com") == "example.com"

    def test_display_name_format(self) -> None:
        """Extracts domain from 'Name <email>' format."""
        assert _extract_sender_domain("Tech Weekly <tech@newsletter.io>") == "newsletter.io"

    def test_empty_string(self) -> None:
        """Returns empty string for empty input."""
        assert _extract_sender_domain("") == ""

    def test_no_at_sign(self) -> None:
        """Returns empty string when no @ present."""
        assert _extract_sender_domain("not-an-email") == ""

    def test_subdomain(self) -> None:
        """Extracts full subdomain."""
        assert _extract_sender_domain("news@mail.substack.com") == "mail.substack.com"

    def test_case_insensitive(self) -> None:
        """Domain is lowercased."""
        assert _extract_sender_domain("user@Example.COM") == "example.com"


# =========================================================================
# _extract_html_body
# =========================================================================


def _make_raw_email(
    *,
    html_body: str | None = None,
    text_body: str | None = None,
    subject: str = "Test Newsletter",
    sender: str = "news@example.com",
) -> bytes:
    """Build a raw RFC822 email message as bytes."""
    if html_body and text_body:
        msg = MIMEMultipart("alternative")
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))
    elif html_body:
        msg = MIMEText(html_body, "html")
    elif text_body:
        msg = MIMEText(text_body, "plain")
    else:
        msg = MIMEText("", "plain")

    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = "save@tasche.example.com"
    return msg.as_bytes()


class TestExtractHtmlBody:
    def test_extracts_html_from_html_only(self) -> None:
        """Extracts HTML from a single-part HTML email."""
        raw = _make_raw_email(html_body="<p>Hello World</p>")
        result = _extract_html_body(raw)
        assert "<p>Hello World</p>" in result

    def test_extracts_html_from_multipart(self) -> None:
        """Prefers HTML over plain text in multipart/alternative."""
        raw = _make_raw_email(
            html_body="<h1>HTML Version</h1>",
            text_body="Plain text version",
        )
        result = _extract_html_body(raw)
        assert "<h1>HTML Version</h1>" in result
        assert "Plain text version" not in result

    def test_falls_back_to_text(self) -> None:
        """Falls back to plain text wrapped in <pre> when no HTML part."""
        raw = _make_raw_email(text_body="Just plain text content")
        result = _extract_html_body(raw)
        assert "<pre>" in result
        assert "Just plain text content" in result

    def test_text_fallback_escapes_html(self) -> None:
        """Plain text fallback escapes HTML entities."""
        raw = _make_raw_email(text_body="Use <div> tags & more")
        result = _extract_html_body(raw)
        assert "&lt;div&gt;" in result
        assert "&amp;" in result

    def test_empty_email_returns_empty(self) -> None:
        """Empty email body returns empty string."""
        raw = _make_raw_email()
        result = _extract_html_body(raw)
        # Empty text/plain will produce <pre></pre>
        assert result == "<pre></pre>" or result == ""


# =========================================================================
# _extract_subject
# =========================================================================


class TestExtractSubject:
    def test_from_js_headers(self) -> None:
        """Extracts subject from JS Headers get() function."""

        def mock_get(name):
            if name == "subject":
                return "Newsletter: Weekly Digest"
            return None

        result = _extract_subject(None, headers_get=mock_get)
        assert result == "Newsletter: Weekly Digest"

    def test_from_parsed_email(self) -> None:
        """Extracts subject from parsed email message."""
        raw = _make_raw_email(subject="Test Subject Line")
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        result = _extract_subject(msg, headers_get=None)
        assert result == "Test Subject Line"

    def test_fallback_to_newsletter(self) -> None:
        """Returns 'Newsletter' when no subject available."""
        result = _extract_subject(None, headers_get=None)
        assert result == "Newsletter"

    def test_js_headers_takes_priority(self) -> None:
        """JS Headers subject takes priority over parsed email subject."""

        def mock_get(name):
            if name == "subject":
                return "JS Subject"
            return None

        raw = _make_raw_email(subject="Parsed Subject")
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        result = _extract_subject(msg, headers_get=mock_get)
        assert result == "JS Subject"

    def test_falls_back_when_js_headers_empty(self) -> None:
        """Falls back to parsed email when JS headers return empty."""

        def mock_get(name):
            return ""

        raw = _make_raw_email(subject="Parsed Subject")
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        result = _extract_subject(msg, headers_get=mock_get)
        assert result == "Parsed Subject"


# =========================================================================
# process_email — integration tests
# =========================================================================


class _MockEmailMessage:
    """Simulates a Cloudflare ForwardableEmailMessage JS object."""

    def __init__(
        self,
        *,
        sender: str = "newsletter@example.com",
        to: str = "save@tasche.example.com",
        subject: str = "Weekly Tech Digest",
        html_body: str | None = None,
        text_body: str | None = None,
    ) -> None:
        # Note: 'from' is a Python keyword, so we use setattr
        self.to = to
        self._subject = subject

        raw_bytes = _make_raw_email(
            html_body=html_body or "<p>Default newsletter content.</p>",
            text_body=text_body,
            subject=subject,
            sender=sender,
        )

        # Simulate a ReadableStream that resolves to bytes
        self.raw = _MockReadableStream(raw_bytes)
        self.rawSize = len(raw_bytes)

        # Simulate JS Headers object
        self.headers = _MockHeaders({"subject": subject})

        # Set 'from' via setattr since it's a Python keyword
        object.__setattr__(self, "from", sender)

    def __getattr__(self, name):
        if name == "from":
            return object.__getattribute__(self, "from")
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")


class _MockReadableStream:
    """Simulates a JS ReadableStream that can be consumed via arrayBuffer()."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    async def arrayBuffer(self) -> bytes:
        return self._data

    def to_py(self):
        return self._data


class _MockHeaders:
    """Simulates a JS Headers object."""

    def __init__(self, headers: dict[str, str]) -> None:
        self._headers = headers

    def get(self, name: str) -> str | None:
        return self._headers.get(name.lower())


class TestProcessEmail:
    async def test_creates_article_from_newsletter(self) -> None:
        """Full pipeline: email is parsed, cleaned, and stored as an article."""
        user_row = {"id": "user_001"}

        def result_fn(sql, params):
            if "SELECT id FROM users" in sql:
                return [user_row]
            return []

        db = TrackingD1(result_fn=result_fn)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        message = _MockEmailMessage(
            sender="digest@technews.com",
            subject="Weekly Tech Digest",
            html_body="""
            <html><body>
                <h1>Weekly Tech Digest</h1>
                <p>Here are this week's top stories in technology.</p>
                <a href="https://example.com/ai-story">AI breakthrough</a>
                <img src="https://pixel.mailchimp.com/open.gif" width="1" height="1">
                <script>track('open')</script>
            </body></html>
            """,
        )

        await process_email(message, env)

        # Verify article was inserted into D1
        inserts = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("INSERT INTO articles")
        ]
        assert len(inserts) == 1, f"Expected 1 INSERT, got {len(inserts)}"

        sql, params = inserts[0]
        # Verify key fields
        assert "user_001" in params  # user_id
        assert "technews.com" in params  # domain
        assert "Weekly Tech Digest" in params  # title
        assert "'ready'" in sql  # status is 'ready'

    async def test_stores_html_in_r2(self) -> None:
        """Cleaned HTML is stored in R2 under the correct key."""
        user_row = {"id": "user_001"}

        def result_fn(sql, params):
            if "SELECT id FROM users" in sql:
                return [user_row]
            return []

        db = TrackingD1(result_fn=result_fn)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        message = _MockEmailMessage(
            html_body="<p>Newsletter content for R2 storage test.</p>",
        )

        await process_email(message, env)

        # Find the stored content.html key
        html_keys = [k for k in r2._store if k.endswith("/content.html")]
        assert len(html_keys) == 1, f"Expected 1 content.html in R2, got {len(html_keys)}"

        stored_html = r2._store[html_keys[0]].decode("utf-8")
        assert "Newsletter content" in stored_html

    async def test_removes_tracking_from_stored_html(self) -> None:
        """Tracking pixels are removed from the stored HTML."""
        user_row = {"id": "user_001"}

        def result_fn(sql, params):
            if "SELECT id FROM users" in sql:
                return [user_row]
            return []

        db = TrackingD1(result_fn=result_fn)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        message = _MockEmailMessage(
            html_body="""
            <p>Content here.</p>
            <img src="https://pixel.mailchimp.com/track.gif" width="1" height="1">
            """,
        )

        await process_email(message, env)

        html_keys = [k for k in r2._store if k.endswith("/content.html")]
        assert len(html_keys) == 1
        stored_html = r2._store[html_keys[0]].decode("utf-8")
        assert "pixel.mailchimp.com" not in stored_html
        assert "Content here" in stored_html

    async def test_uses_first_url_as_original_url(self) -> None:
        """When the email has links, the first content URL is used as original_url."""
        user_row = {"id": "user_001"}

        def result_fn(sql, params):
            if "SELECT id FROM users" in sql:
                return [user_row]
            return []

        db = TrackingD1(result_fn=result_fn)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        message = _MockEmailMessage(
            html_body="""
            <p>Read <a href="https://blog.example.com/great-article">this article</a>.</p>
            """,
        )

        await process_email(message, env)

        inserts = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("INSERT INTO articles")
        ]
        assert len(inserts) == 1
        _sql, params = inserts[0]
        # original_url should be the first link found
        assert "https://blog.example.com/great-article" in params

    async def test_falls_back_to_mailto_url(self) -> None:
        """When no links in email, original_url falls back to mailto:sender."""
        user_row = {"id": "user_001"}

        def result_fn(sql, params):
            if "SELECT id FROM users" in sql:
                return [user_row]
            return []

        db = TrackingD1(result_fn=result_fn)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        message = _MockEmailMessage(
            sender="digest@example.com",
            html_body="<p>No links in this newsletter.</p>",
        )

        await process_email(message, env)

        inserts = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("INSERT INTO articles")
        ]
        assert len(inserts) == 1
        _sql, params = inserts[0]
        assert "mailto:digest@example.com" in params

    async def test_skips_when_no_users(self) -> None:
        """When no users exist in the database, the email is skipped."""
        db = TrackingD1()  # returns [] for all queries
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        message = _MockEmailMessage()

        await process_email(message, env)

        # No INSERT should have been executed
        inserts = [(sql, params) for sql, params in db.executed if sql.strip().startswith("INSERT")]
        assert len(inserts) == 0

    async def test_plain_text_email_wrapped_in_pre(self) -> None:
        """Plain text emails are wrapped in <pre> tags."""
        user_row = {"id": "user_001"}

        def result_fn(sql, params):
            if "SELECT id FROM users" in sql:
                return [user_row]
            return []

        db = TrackingD1(result_fn=result_fn)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        message = _MockEmailMessage(
            text_body="Plain text newsletter content.\nLine two.",
            html_body=None,
        )
        # Rebuild the raw bytes with text only
        raw_bytes = _make_raw_email(
            text_body="Plain text newsletter content.\nLine two.",
            subject="Plain Newsletter",
            sender="newsletter@example.com",
        )
        message.raw = _MockReadableStream(raw_bytes)
        message.rawSize = len(raw_bytes)

        await process_email(message, env)

        # Article should still be created
        inserts = [
            (sql, params)
            for sql, params in db.executed
            if sql.strip().startswith("INSERT INTO articles")
        ]
        assert len(inserts) == 1

    async def test_sql_param_count_matches(self) -> None:
        """Every SQL statement has matching placeholder and param counts."""
        import re

        user_row = {"id": "user_001"}

        def result_fn(sql, params):
            if "SELECT id FROM users" in sql:
                return [user_row]
            return []

        db = TrackingD1(result_fn=result_fn)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        message = _MockEmailMessage(
            html_body="<p>Content for SQL param test.</p>",
        )

        await process_email(message, env)

        for sql, params in db.executed:
            expected = len(re.findall(r"\?", sql))
            actual = len(params)
            assert expected == actual, (
                f"SQL placeholder/param mismatch: {expected} placeholders but "
                f"{actual} params.\nSQL: {sql!r}\nParams: {params!r}"
            )
