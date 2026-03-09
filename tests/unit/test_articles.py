"""Tests for Phase 3 — Article CRUD API (src/articles/routes.py).

Covers creating, listing, retrieving, updating, and deleting articles,
as well as authentication enforcement on all endpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from src.articles.routes import router
from tests.conftest import (
    ArticleFactory,
    MockD1,
    MockEnv,
    MockQueue,
    MockR2,
    TrackingD1,
    make_test_helpers,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_make_app, _authenticated_client = make_test_helpers((router, "/api/articles"))


# ---------------------------------------------------------------------------
# POST /api/articles — Create article
# ---------------------------------------------------------------------------


class TestCreateArticle:
    async def test_creates_article_and_enqueues_job(self) -> None:
        """POST /api/articles inserts into D1 and sends to ARTICLE_QUEUE."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/article", "title": "My Article"},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["status"] == "pending"

        # Verify D1 insert was called
        insert_calls = [c for c in calls if c["sql"].startswith("INSERT")]
        assert len(insert_calls) == 1

        # Verify queue message was sent
        assert len(queue.messages) == 1
        msg = queue.messages[0]
        assert msg["type"] == "article_processing"
        assert msg["url"] == "https://example.com/article"

    async def test_reprocesses_duplicate_url(self) -> None:
        """POST /api/articles re-processes when URL already exists."""
        existing = ArticleFactory.create(
            user_id="user_001",
            original_url="https://example.com/existing",
        )

        updates = []

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                return [existing]
            if "UPDATE articles SET status" in sql:
                updates.append(params)
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/existing"},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["updated"] is True
        assert data["id"] == existing["id"]
        assert len(updates) == 1

    async def test_reprocess_cleans_up_all_r2_content(self) -> None:
        """POST /api/articles re-processing cleans up ALL old R2 content including audio."""
        existing = ArticleFactory.create(
            id="art_cleanup",
            user_id="user_001",
            original_url="https://example.com/cleanup",
        )

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                return [existing]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        # Pre-populate R2 with old content + audio
        await r2.put("articles/art_cleanup/content.html", b"<p>old</p>")
        await r2.put("articles/art_cleanup/images/abc.webp", b"IMG")
        await r2.put("articles/art_cleanup/audio.ogg", b"AUDIO")
        await r2.put("articles/art_cleanup/audio-timing.json", b"TIMING")

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/cleanup"},
        )

        assert resp.status_code == 201

        # All old content should be cleaned up (text AND audio)
        assert await r2.get("articles/art_cleanup/content.html") is None
        assert await r2.get("articles/art_cleanup/images/abc.webp") is None
        assert await r2.get("articles/art_cleanup/audio.ogg") is None
        assert await r2.get("articles/art_cleanup/audio-timing.json") is None

    async def test_finds_duplicate_via_final_url(self) -> None:
        """POST /api/articles detects duplicate when submitted URL matches final_url."""
        # Scenario: article was saved with original_url="https://example.com/old"
        # but after processing, its final_url was set to "https://example.com/redirected".
        # Now the user submits "https://example.com/redirected" — should find the duplicate.
        existing = ArticleFactory.create(
            id="existing_art",
            user_id="user_001",
            original_url="https://example.com/old",
            final_url="https://example.com/redirected",
        )

        updates = []

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                # The submitted URL is "https://example.com/redirected" which does NOT
                # match original_url, but DOES match final_url.
                # The SQL is: WHERE user_id = ? AND (original_url = ?
                # OR final_url = ? OR canonical_url = ?)
                # params are: [user_id, url, url, url]
                submitted_url = params[1]  # the URL being checked
                if (
                    submitted_url == existing["original_url"]
                    or submitted_url == existing["final_url"]
                    or submitted_url == existing["canonical_url"]
                ):
                    return [existing]
                return []
            if "UPDATE articles SET status" in sql:
                updates.append(params)
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/redirected"},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["updated"] is True
        assert data["id"] == "existing_art"
        assert data.get("created_at") == existing["created_at"]

    async def test_finds_duplicate_via_canonical_url(self) -> None:
        """POST /api/articles detects duplicate when submitted URL matches canonical_url."""
        # Scenario: article was saved, processing set canonical_url to a clean URL.
        # User submits that clean canonical URL — should detect it as duplicate.
        existing = ArticleFactory.create(
            id="existing_canon",
            user_id="user_001",
            original_url="https://example.com/page?utm_source=twitter",
            final_url="https://example.com/page?utm_source=twitter",
            canonical_url="https://example.com/page",
        )

        updates = []

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                submitted_url = params[1]
                if (
                    submitted_url == existing["original_url"]
                    or submitted_url == existing["final_url"]
                    or submitted_url == existing["canonical_url"]
                ):
                    return [existing]
                return []
            if "UPDATE articles SET status" in sql:
                updates.append(params)
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/page"},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["updated"] is True
        assert data["id"] == "existing_canon"
        assert data.get("created_at") == existing["created_at"]

    async def test_reprocess_enqueues_with_submitted_url(self) -> None:
        """When re-processing, the queue message uses the newly submitted URL, not original_url."""
        existing = ArticleFactory.create(
            id="existing_art_2",
            user_id="user_001",
            original_url="https://example.com/original-page",
            final_url="https://example.com/final-page",
        )

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                submitted_url = params[1]
                if (
                    submitted_url == existing["original_url"]
                    or submitted_url == existing["final_url"]
                    or submitted_url == existing["canonical_url"]
                ):
                    return [existing]
                return []
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        # User submits the final_url, not the original_url
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/final-page"},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["updated"] is True

        # The queue message should use the submitted URL (final-page),
        # which will be re-fetched by the processing pipeline
        assert len(queue.messages) == 1
        msg = queue.messages[0]
        assert msg["url"] == "https://example.com/final-page"
        assert msg["article_id"] == "existing_art_2"

    async def test_create_article_with_real_url(self) -> None:
        """POST /api/articles succeeds with a real-world URL."""
        url = "https://okayfail.com/2025/in-praise-of-dhh.html"
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": url},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["status"] == "pending"

        # Verify queue was sent with the correct URL
        assert len(queue.messages) == 1
        assert queue.messages[0]["url"] == url

    async def test_duplicate_with_wrapped_d1_result(self) -> None:
        """POST /api/articles handles D1 .first() returning a result wrapper.

        In Pyodide, D1's .first() may return the full result wrapper
        {results: [...], success, meta} instead of just the row.
        The duplicate check must still extract the article ID correctly.
        """
        existing = ArticleFactory.create(
            id="wrapped_art",
            user_id="user_001",
            original_url="https://okayfail.com/2025/test.html",
        )
        updates = []

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                # Simulate what Pyodide D1 .first() might return:
                # the result wrapper instead of the row itself.
                # d1_first() must unwrap this before returning.
                return [existing]
            if "UPDATE articles SET status" in sql:
                updates.append(params)
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://okayfail.com/2025/test.html"},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == "wrapped_art"
        assert data["updated"] is True

    async def test_create_article_sql_param_counts_match(self) -> None:
        """Every SQL statement executed during article creation has matching placeholders/params."""
        import re

        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/test-params"},
        )

        assert resp.status_code == 201

        for call in calls:
            sql = call["sql"]
            params = call["params"]
            expected = len(re.findall(r"\?", sql))
            actual = len(params)
            assert expected == actual, (
                f"SQL placeholder/param mismatch: {expected} placeholders but {actual} params.\n"
                f"SQL: {sql!r}\n"
                f"Params: {params!r}"
            )

    async def test_url_normalization_preserves_query_params(self) -> None:
        """POST /api/articles preserves URL query parameters during validation."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/article?utm_source=twitter&ref=123"},
        )

        assert resp.status_code == 201

        # Verify the queue message preserves the full URL with query params
        assert len(queue.messages) == 1
        msg = queue.messages[0]
        assert "utm_source=twitter" in msg["url"]
        assert "ref=123" in msg["url"]

    async def test_rejects_invalid_url(self) -> None:
        """POST /api/articles returns 422 for an invalid URL."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/articles",
            json={"url": "ftp://not-allowed.com/file"},
        )

        assert resp.status_code == 422

    async def test_rejects_empty_url(self) -> None:
        """POST /api/articles returns 422 when url is empty."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/articles",
            json={"url": ""},
        )

        assert resp.status_code == 422

    async def test_listen_later_sets_audio_status_pending(self) -> None:
        """POST /api/articles with listen_later=true sets audio_status to pending."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/listen", "listen_later": True},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"

        # Verify the INSERT includes audio_status
        insert_calls = [c for c in calls if c["sql"].startswith("INSERT")]
        assert len(insert_calls) == 1
        assert "audio_status" in insert_calls[0]["sql"]
        assert "'pending'" in insert_calls[0]["sql"]

    async def test_listen_later_false_omits_audio_status(self) -> None:
        """POST /api/articles without listen_later does not set audio_status."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/no-listen"},
        )

        assert resp.status_code == 201

        insert_calls = [c for c in calls if c["sql"].startswith("INSERT")]
        assert len(insert_calls) == 1
        assert "audio_status" not in insert_calls[0]["sql"]

    async def test_listen_later_on_duplicate_sets_audio_status(self) -> None:
        """POST /api/articles with listen_later=true on duplicate sets audio_status."""
        existing = ArticleFactory.create(
            user_id="user_001",
            original_url="https://example.com/dup-listen",
        )

        updates: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                return [existing]
            if "UPDATE articles SET" in sql:
                updates.append({"sql": sql, "params": params})
                return []
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/dup-listen", "listen_later": True},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["updated"] is True

        # Verify the UPDATE includes audio_status
        assert len(updates) == 1
        assert "audio_status" in updates[0]["sql"]

    async def test_creates_article_with_tag_ids(self) -> None:
        """POST /api/articles with tag_ids inserts into article_tags."""
        calls: list[dict[str, Any]] = []
        user_tags = [
            {"id": "tag_aaa", "user_id": "user_001", "name": "python"},
            {"id": "tag_bbb", "user_id": "user_001", "name": "testing"},
        ]

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            # Return tag row when validating tag ownership
            if "FROM tags WHERE id" in sql:
                return [t for t in user_tags if t["id"] == params[0]]
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, _session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={
                "url": "https://example.com/tagged",
                "title": "Tagged Article",
                "tag_ids": ["tag_aaa", "tag_bbb"],
            },
        )

        assert resp.status_code == 201

        tag_inserts = [c for c in calls if "INSERT" in c["sql"] and "article_tags" in c["sql"]]
        assert len(tag_inserts) == 2
        inserted_tag_ids = {c["params"][1] for c in tag_inserts}
        assert inserted_tag_ids == {"tag_aaa", "tag_bbb"}

    async def test_tag_ids_skips_invalid_tags(self) -> None:
        """POST /api/articles with unknown tag_ids silently skips them."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            # No tags belong to this user
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, _session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={
                "url": "https://example.com/bad-tags",
                "tag_ids": ["nonexistent_tag"],
            },
        )

        assert resp.status_code == 201

        tag_inserts = [c for c in calls if "INSERT" in c["sql"] and "article_tags" in c["sql"]]
        assert len(tag_inserts) == 0

    async def test_tag_ids_skips_other_users_tags(self) -> None:
        """POST /api/articles won't apply tags owned by a different user."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            # Tag exists but query filters by user_id so it won't match
            if "FROM tags WHERE id" in sql:
                return []  # No match for this user
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, _session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={
                "url": "https://example.com/wrong-user-tag",
                "tag_ids": ["tag_other_user"],
            },
        )

        assert resp.status_code == 201
        tag_inserts = [c for c in calls if "INSERT" in c["sql"] and "article_tags" in c["sql"]]
        assert len(tag_inserts) == 0

    async def test_tag_ids_empty_list_is_noop(self) -> None:
        """POST /api/articles with empty tag_ids does nothing extra."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, _session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={
                "url": "https://example.com/no-tags",
                "tag_ids": [],
            },
        )

        assert resp.status_code == 201
        tag_inserts = [c for c in calls if "article_tags" in c["sql"]]
        assert len(tag_inserts) == 0

    async def test_tag_ids_capped_at_twenty(self) -> None:
        """POST /api/articles applies at most 20 tags even if more are sent."""
        calls: list[dict[str, Any]] = []
        # Create 25 valid tags
        many_tags = [
            {"id": f"tag_{i:03d}", "user_id": "user_001", "name": f"t{i}"} for i in range(25)
        ]

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            if "FROM tags WHERE id" in sql:
                return [t for t in many_tags if t["id"] == params[0]]
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, _session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={
                "url": "https://example.com/many-tags",
                "tag_ids": [t["id"] for t in many_tags],
            },
        )

        assert resp.status_code == 201
        tag_inserts = [c for c in calls if "INSERT" in c["sql"] and "article_tags" in c["sql"]]
        assert len(tag_inserts) == 20


# ---------------------------------------------------------------------------
# POST /api/articles — listen_later behaviour tests (value-level assertions)
# ---------------------------------------------------------------------------


def _parse_insert_columns_and_params(sql: str, params: list[Any]) -> dict[str, Any]:
    """Parse an INSERT ... VALUES statement into a column→value mapping.

    Handles both parameterised (?) and inline ('pending') values.
    Returns a dict mapping column names to their bound or inline values.
    """
    import re

    # Extract column names from INSERT INTO table (col1, col2, ...)
    col_match = re.search(r"\(([^)]+)\)\s*VALUES", sql, re.IGNORECASE)
    if not col_match:
        return {}

    columns = [c.strip() for c in col_match.group(1).split(",")]

    # Extract values from VALUES (...)
    val_match = re.search(r"VALUES\s*\(([^)]+)\)", sql, re.IGNORECASE)
    if not val_match:
        return {}

    values_raw = [v.strip() for v in val_match.group(1).split(",")]

    result: dict[str, Any] = {}
    param_idx = 0
    for i, col in enumerate(columns):
        if i < len(values_raw):
            val = values_raw[i]
            if val == "?":
                result[col] = params[param_idx] if param_idx < len(params) else None
                param_idx += 1
            else:
                # Inline value like 'pending' or 0
                result[col] = val.strip("'")
    return result


class TestListenLaterBehaviour:
    """Value-level assertions for the listen_later / save-audio flow.

    Unlike the SQL-string-inspection tests above, these verify the actual
    bound parameter values that would reach D1.
    """

    async def test_listen_later_insert_binds_audio_status_pending(self) -> None:
        """POST with listen_later=true binds audio_status='pending' in the INSERT."""
        db = TrackingD1()
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/audio-insert", "listen_later": True},
        )

        assert resp.status_code == 201

        insert_stmts = [
            (sql, params) for sql, params in db.executed if sql.strip().startswith("INSERT")
        ]
        assert len(insert_stmts) == 1

        parsed = _parse_insert_columns_and_params(*insert_stmts[0])
        assert parsed.get("audio_status") == "pending"

    async def test_save_insert_does_not_include_audio_status(self) -> None:
        """POST without listen_later omits audio_status from the INSERT entirely."""
        db = TrackingD1()
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/plain-insert"},
        )

        assert resp.status_code == 201

        insert_stmts = [
            (sql, params) for sql, params in db.executed if sql.strip().startswith("INSERT")
        ]
        assert len(insert_stmts) == 1

        parsed = _parse_insert_columns_and_params(*insert_stmts[0])
        assert "audio_status" not in parsed

    async def test_listen_later_duplicate_update_sets_audio_status_pending(
        self,
    ) -> None:
        """POST with listen_later=true on existing article sets audio_status='pending' in UPDATE."""
        existing = ArticleFactory.create(
            user_id="user_001",
            original_url="https://example.com/dup-audio-update",
        )

        def result_fn(sql, params):
            if "original_url = ?" in sql:
                return [existing]
            return []

        db = TrackingD1(result_fn=result_fn)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/dup-audio-update", "listen_later": True},
        )

        assert resp.status_code == 201

        update_stmts = [
            (sql, params) for sql, params in db.executed if "UPDATE articles SET" in sql
        ]
        assert len(update_stmts) == 1

        # audio_status is set as an inline value in the SQL, not a ? placeholder
        sql = update_stmts[0][0]
        assert "audio_status = 'pending'" in sql

    async def test_save_duplicate_update_resets_audio_status_to_null(self) -> None:
        """POST without listen_later on existing article resets audio_status to NULL."""
        existing = ArticleFactory.create(
            user_id="user_001",
            original_url="https://example.com/dup-plain-update",
        )

        def result_fn(sql, params):
            if "original_url = ?" in sql:
                return [existing]
            return []

        db = TrackingD1(result_fn=result_fn)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/dup-plain-update"},
        )

        assert resp.status_code == 201

        update_stmts = [
            (sql, params) for sql, params in db.executed if "UPDATE articles SET" in sql
        ]
        assert len(update_stmts) == 1

        sql = update_stmts[0][0]
        assert "audio_status = NULL" in sql
        assert "audio_status = 'pending'" not in sql

    async def test_both_paths_enqueue_article_processing(self) -> None:
        """Both Save and Save Audio enqueue an article_processing message."""
        for listen_later in [False, True]:
            db = TrackingD1()
            queue = MockQueue()
            env = MockEnv(db=db, article_queue=queue)

            client, session_id = await _authenticated_client(env)
            resp = client.post(
                "/api/articles",
                json={
                    "url": f"https://example.com/enqueue-{listen_later}",
                    "listen_later": listen_later,
                },
            )

            assert resp.status_code == 201

            processing_msgs = [m for m in queue.messages if m.get("type") == "article_processing"]
            assert len(processing_msgs) == 1, (
                f"listen_later={listen_later} should enqueue exactly one "
                f"article_processing message, got {len(processing_msgs)}"
            )
            assert "article_id" in processing_msgs[0]
            assert processing_msgs[0]["url"] == f"https://example.com/enqueue-{listen_later}"


# ---------------------------------------------------------------------------
# POST /api/articles with content — Bookmarklet content capture
# ---------------------------------------------------------------------------


class TestCreateArticleWithContent:
    async def test_stores_raw_html_in_r2_when_content_provided(self) -> None:
        """POST /api/articles with content stores raw HTML in R2."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, article_queue=queue, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={
                "url": "https://example.com/paywalled",
                "title": "Paywalled Article",
                "content": "<html><body><p>Secret content</p></body></html>",
            },
        )

        assert resp.status_code == 201
        data = resp.json()
        article_id = data["id"]

        # Verify raw HTML was stored in R2
        raw_key = f"articles/{article_id}/raw.html"
        assert raw_key in r2._store
        stored = r2._store[raw_key].decode("utf-8")
        assert "<p>Secret content</p>" in stored

    async def test_creates_article_without_content(self) -> None:
        """POST /api/articles without content does not store raw HTML in R2."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, article_queue=queue, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/no-content"},
        )

        assert resp.status_code == 201
        # No raw.html should be in R2
        raw_keys = [k for k in r2._store if k.endswith("raw.html")]
        assert len(raw_keys) == 0

    async def test_rejects_content_exceeding_5mb(self) -> None:
        """POST /api/articles returns 400 when content exceeds 5 MB."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        huge_content = "x" * (5_242_880 + 1)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/huge", "content": huge_content},
        )

        assert resp.status_code == 400
        assert "5 MB" in resp.json()["detail"]

    async def test_rejects_non_string_content(self) -> None:
        """POST /api/articles returns 400 when content is not a string."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/bad", "content": 12345},
        )

        assert resp.status_code == 400
        assert "string" in resp.json()["detail"].lower()

    async def test_content_with_duplicate_url_stores_raw_html(self) -> None:
        """POST /api/articles with content on duplicate URL still stores raw HTML."""
        existing = ArticleFactory.create(
            user_id="user_001",
            original_url="https://example.com/dup-content",
        )

        def execute(sql: str, params: list) -> list:
            if "original_url = ?" in sql:
                return [existing]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={
                "url": "https://example.com/dup-content",
                "content": "<html><body><p>Updated content</p></body></html>",
            },
        )

        assert resp.status_code == 201
        data = resp.json()
        article_id = data["id"]

        raw_key = f"articles/{article_id}/raw.html"
        assert raw_key in r2._store

    async def test_empty_content_string_does_not_store(self) -> None:
        """POST /api/articles with empty content string skips R2 storage."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, article_queue=queue, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/empty-content", "content": ""},
        )

        assert resp.status_code == 201
        # Empty string is falsy, so no raw.html should be stored
        raw_keys = [k for k in r2._store if k.endswith("raw.html")]
        assert len(raw_keys) == 0

    async def test_enqueues_processing_when_content_provided(self) -> None:
        """POST /api/articles with content still enqueues the processing job."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, article_queue=queue, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={
                "url": "https://example.com/with-content",
                "content": "<html><body><p>Content</p></body></html>",
            },
        )

        assert resp.status_code == 201

        # Queue message should still be sent
        assert len(queue.messages) == 1
        msg = queue.messages[0]
        assert msg["type"] == "article_processing"
        assert msg["url"] == "https://example.com/with-content"


# ---------------------------------------------------------------------------
# GET /api/articles — List articles
# ---------------------------------------------------------------------------


class TestListArticles:
    async def test_returns_pending_articles_with_null_fields(self) -> None:
        """GET /api/articles returns articles with NULL optional fields."""
        # A freshly-created article has many NULL fields before processing completes
        pending_article = {
            "id": "art_pending",
            "user_id": "user_001",
            "original_url": "https://okayfail.com/2025/in-praise-of-dhh.html",
            "final_url": None,
            "canonical_url": None,
            "domain": "okayfail.com",
            "title": None,
            "excerpt": None,
            "author": None,
            "word_count": None,
            "reading_time_minutes": None,
            "image_count": 0,
            "status": "pending",
            "reading_status": "unread",
            "is_favorite": 0,
            "audio_key": None,
            "audio_duration_seconds": None,
            "audio_status": None,
            "html_key": None,
            "thumbnail_key": None,
            "original_key": None,
            "original_status": "unknown",
            "scroll_position": 0.0,
            "reading_progress": 0.0,
            "created_at": "2025-01-15T10:00:00",
            "updated_at": "2025-01-15T10:00:00",
        }

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "FROM articles" in sql and "LIMIT" in sql:
                return [pending_article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "art_pending"
        assert data[0]["status"] == "pending"
        assert data[0]["title"] is None
        assert data[0]["final_url"] is None

    async def test_returns_users_articles(self) -> None:
        """GET /api/articles returns a list of the user's articles."""
        articles = [
            ArticleFactory.create(user_id="user_001", title="First"),
            ArticleFactory.create(user_id="user_001", title="Second"),
        ]

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "FROM articles" in sql and "LIMIT" in sql:
                return articles
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["title"] == "First"
        assert data[1]["title"] == "Second"

    async def test_filters_by_reading_status(self) -> None:
        """GET /api/articles?reading_status=unread filters correctly."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/articles?reading_status=unread",
        )

        # Verify the query includes reading_status filter
        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        assert "reading_status = ?" in select_calls[0]["sql"]

    async def test_default_sort_is_newest(self) -> None:
        """GET /api/articles without sort param orders by created_at DESC."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles",
        )

        assert resp.status_code == 200
        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        assert "ORDER BY created_at DESC" in select_calls[0]["sql"]

    async def test_sort_oldest(self) -> None:
        """GET /api/articles?sort=oldest orders by created_at ASC."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles?sort=oldest",
        )

        assert resp.status_code == 200
        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        assert "ORDER BY created_at ASC" in select_calls[0]["sql"]

    async def test_sort_shortest(self) -> None:
        """GET /api/articles?sort=shortest orders by reading_time_minutes ASC NULLS LAST."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles?sort=shortest",
        )

        assert resp.status_code == 200
        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        assert "ORDER BY reading_time_minutes ASC NULLS LAST" in select_calls[0]["sql"]

    async def test_sort_longest(self) -> None:
        """GET /api/articles?sort=longest orders by reading_time_minutes DESC NULLS LAST."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles?sort=longest",
        )

        assert resp.status_code == 200
        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        assert "ORDER BY reading_time_minutes DESC NULLS LAST" in select_calls[0]["sql"]

    async def test_sort_title_asc(self) -> None:
        """GET /api/articles?sort=title_asc orders by title ASC."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles?sort=title_asc",
        )

        assert resp.status_code == 200
        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        assert "ORDER BY title ASC" in select_calls[0]["sql"]

    async def test_sort_invalid_value_returns_422(self) -> None:
        """GET /api/articles?sort=invalid returns 422."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles?sort=invalid",
        )

        assert resp.status_code == 422
        assert "sort must be one of" in resp.json()["detail"]

    async def test_sort_combined_with_filter(self) -> None:
        """GET /api/articles?reading_status=unread&sort=shortest combines filter and sort."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles?reading_status=unread&sort=shortest",
        )

        assert resp.status_code == 200
        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        sql = select_calls[0]["sql"]
        assert "reading_status = ?" in sql
        assert "ORDER BY reading_time_minutes ASC NULLS LAST" in sql


# ---------------------------------------------------------------------------
# GET /api/articles/{article_id} — Get single article
# ---------------------------------------------------------------------------


class TestGetArticle:
    async def test_returns_single_article(self) -> None:
        """GET /api/articles/{id} returns the article metadata."""
        article = ArticleFactory.create(id="art_123", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_123":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_123",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "art_123"

    async def test_returns_404_for_missing_article(self) -> None:
        """GET /api/articles/{id} returns 404 when article doesn't exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/nonexistent",
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/articles/{article_id} — Update article
# ---------------------------------------------------------------------------


class TestUpdateArticle:
    async def test_updates_reading_status(self) -> None:
        """PATCH /api/articles/{id} updates reading_status and returns updated article."""
        article = ArticleFactory.create(id="art_456", user_id="user_001")
        updated_article = {**article, "reading_status": "archived"}

        call_count = 0

        def execute(sql: str, params: list) -> list:
            nonlocal call_count
            call_count += 1
            if sql.startswith("SELECT") and "id = ?" in sql and params[0] == "art_456":
                # First SELECT returns existing, subsequent returns updated
                if call_count <= 1:
                    return [article]
                return [updated_article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_456",
            json={"reading_status": "archived"},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["reading_status"] == "archived"

    async def test_returns_404_for_missing_article(self) -> None:
        """PATCH /api/articles/{id} returns 404 when article doesn't exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/nonexistent",
            json={"reading_status": "archived"},
        )

        assert resp.status_code == 404

    async def test_rejects_empty_update(self) -> None:
        """PATCH /api/articles/{id} returns 422 when no updatable fields are provided."""
        article = ArticleFactory.create(id="art_789", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_789":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_789",
            json={"unknown_field": "value"},
        )

        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DELETE /api/articles/{article_id} — Delete article
# ---------------------------------------------------------------------------


class TestDeleteArticle:
    async def test_deletes_article(self) -> None:
        """DELETE /api/articles/{id} removes article from D1 and R2."""
        article = ArticleFactory.create(id="art_del", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and params[0] == "art_del":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        # Pre-populate R2
        await r2.put("articles/art_del/content.html", "<p>html</p>")
        await r2.put("articles/art_del/content.md", "# markdown")

        client, session_id = await _authenticated_client(env)
        resp = client.delete(
            "/api/articles/art_del",
        )

        assert resp.status_code == 204

    async def test_returns_404_for_missing_article(self) -> None:
        """DELETE /api/articles/{id} returns 404 when article doesn't exist."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.delete(
            "/api/articles/nonexistent",
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/articles/{article_id}/thumbnail — Serve thumbnail
# ---------------------------------------------------------------------------


class TestGetArticleThumbnail:
    async def test_returns_thumbnail_image(self) -> None:
        """GET /api/articles/{id}/thumbnail returns WebP image from R2."""
        article = ArticleFactory.create(
            id="art_thumb",
            user_id="user_001",
            thumbnail_key="articles/art_thumb/thumbnail.webp",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_thumb":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        # Put a fake WebP image in R2
        await r2.put("articles/art_thumb/thumbnail.webp", b"\x00WEBP_IMAGE_DATA")

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_thumb/thumbnail",
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"
        assert resp.headers["cache-control"] == "public, max-age=86400"
        assert resp.content == b"\x00WEBP_IMAGE_DATA"

    async def test_returns_404_when_no_thumbnail_key(self) -> None:
        """GET /api/articles/{id}/thumbnail returns 404 when thumbnail_key is null."""
        article = ArticleFactory.create(
            id="art_nothumb",
            user_id="user_001",
            thumbnail_key=None,
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_nothumb":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_nothumb/thumbnail",
        )

        assert resp.status_code == 404

    async def test_returns_404_when_r2_object_missing(self) -> None:
        """GET /api/articles/{id}/thumbnail returns 404 when R2 object is gone."""
        article = ArticleFactory.create(
            id="art_gone",
            user_id="user_001",
            thumbnail_key="articles/art_gone/thumbnail.webp",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_gone":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()  # Empty R2 — no object stored
        env = MockEnv(db=db, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_gone/thumbnail",
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/articles/{article_id}/images/{filename} — Serve article images
# ---------------------------------------------------------------------------


class TestGetArticleImage:
    async def test_returns_webp_image(self) -> None:
        """GET /api/articles/{id}/images/{filename} returns image from R2."""
        article = ArticleFactory.create(id="art_img", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_img":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        await r2.put("articles/art_img/images/abc123.webp", b"\x00WEBP_IMAGE_DATA")

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_img/images/abc123.webp",
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/webp"
        assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert resp.content == b"\x00WEBP_IMAGE_DATA"

    async def test_returns_jpeg_image(self) -> None:
        """GET /api/articles/{id}/images/{filename} returns correct type for .jpg."""
        article = ArticleFactory.create(id="art_jpg", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_jpg":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        await r2.put("articles/art_jpg/images/def456.jpg", b"\xff\xd8JPEG_DATA")

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_jpg/images/def456.jpg",
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "image/jpeg"

    async def test_returns_404_when_image_not_in_r2(self) -> None:
        """GET /api/articles/{id}/images/{filename} returns 404 when not in R2."""
        article = ArticleFactory.create(id="art_noimg", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_noimg":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()  # Empty R2
        env = MockEnv(db=db, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_noimg/images/deadbeef.webp",
        )

        assert resp.status_code == 404

    async def test_returns_404_when_article_not_found(self) -> None:
        """GET /api/articles/{id}/images/{filename} returns 404 for wrong article."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/nonexistent/images/abc123.webp",
        )

        assert resp.status_code == 404

    def test_requires_auth(self) -> None:
        """GET /api/articles/{id}/images/{filename} returns 401 without auth."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/articles/some-id/images/abc123.webp")
        assert resp.status_code == 401

    async def test_rejects_non_hex_image_filename(self) -> None:
        """GET /api/articles/{id}/images/{filename} rejects non-hex filenames."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.get("/api/articles/art_id/images/not-a-hash.webp")
        assert resp.status_code == 400

    async def test_rejects_disallowed_extension(self) -> None:
        """GET /api/articles/{id}/images/{filename} rejects disallowed extensions."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.get("/api/articles/art_id/images/abc123.exe")
        assert resp.status_code == 400

    async def test_returns_octet_stream_for_unknown_extension(self) -> None:
        """GET /api/articles/{id}/images/{filename} falls back to octet-stream."""
        article = ArticleFactory.create(id="art_bin", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_bin":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        await r2.put("articles/art_bin/images/deadbeef.bin", b"\x00BINARY_DATA")

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_bin/images/deadbeef.bin",
        )

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/octet-stream"


# ---------------------------------------------------------------------------
# GET /api/articles?audio_status=... — Filter by audio_status
# ---------------------------------------------------------------------------


class TestUpdateArticleMultipleFields:
    async def test_updates_multiple_fields_at_once(self) -> None:
        """PATCH /api/articles/{id} can update multiple fields at once."""
        article = ArticleFactory.create(id="art_multi", user_id="user_001")
        updated_article = {
            **article,
            "reading_status": "archived",
            "is_favorite": 1,
            "reading_progress": 0.5,
        }

        call_count = 0

        def execute(sql: str, params: list) -> list:
            nonlocal call_count
            call_count += 1
            if sql.startswith("SELECT") and "id = ?" in sql:
                if call_count <= 1:
                    return [article]
                return [updated_article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_multi",
            json={
                "reading_status": "archived",
                "is_favorite": True,
                "reading_progress": 0.5,
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["reading_status"] == "archived"
        assert data["is_favorite"] == 1
        assert data["reading_progress"] == 0.5


class TestListenLaterRegeneration:
    """Test TTS listen-later endpoint always allows re-generation."""

    async def test_listen_later_regenerates_when_audio_ready(self) -> None:
        """POST /api/articles/{id}/listen-later re-queues even when audio is ready."""
        from src.tts.routes import router as tts_router

        article = ArticleFactory.create(
            id="art_audio_ready",
            user_id="user_001",
            audio_status="ready",
            audio_key="articles/art_audio_ready/audio.mp3",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_audio_ready":
                return [article]
            return []

        db = MockD1(execute=execute)
        from tests.conftest import _authenticated_client as _auth_client

        env = MockEnv(db=db, content=MockR2())
        client, session_id = await _auth_client(
            env,
            (tts_router, "/api/articles"),
        )

        resp = client.post(
            "/api/articles/art_audio_ready/listen-later",
        )

        assert resp.status_code == 202, (
            f"Expected 202 for audio regeneration, got {resp.status_code}"
        )
        data = resp.json()
        assert data["audio_status"] == "pending"


class TestFilterByTag:
    async def test_filters_by_tag_id(self) -> None:
        """GET /api/articles?tag=tag_001 includes subquery filter in SQL."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/articles?tag=tag_001",
        )

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        sql = select_calls[0]["sql"]
        assert "article_tags" in sql, "Tag filter should use article_tags subquery"
        assert "tag_id = ?" in sql, "Tag filter should use parameterized tag_id"
        assert "tag_001" in select_calls[0]["params"]


class TestFilterByAudioStatus:
    async def test_filters_by_audio_status(self) -> None:
        """GET /api/articles?audio_status=ready includes audio_status filter in query."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/articles?audio_status=ready",
        )

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        assert "audio_status = ?" in select_calls[0]["sql"]
        assert "ready" in select_calls[0]["params"]


# ---------------------------------------------------------------------------
# Authentication enforcement
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Input validation (field length limits)
# ---------------------------------------------------------------------------


class TestInputValidation:
    async def test_rejects_url_too_long(self) -> None:
        """POST /api/articles returns 400 when URL exceeds 2048 chars."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        long_url = "https://example.com/" + "a" * 2100
        resp = client.post(
            "/api/articles",
            json={"url": long_url},
        )

        assert resp.status_code == 400
        assert "2048" in resp.json()["detail"]

    async def test_rejects_title_too_long(self) -> None:
        """POST /api/articles returns 400 when title exceeds 500 chars."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com", "title": "x" * 501},
        )

        assert resp.status_code == 400
        assert "500" in resp.json()["detail"]

    async def test_rejects_title_too_long_on_update(self) -> None:
        """PATCH /api/articles/{id} returns 400 when title exceeds 500 chars."""
        article = ArticleFactory.create(id="art_valid", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_valid":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_valid",
            json={"title": "x" * 501},
        )

        assert resp.status_code == 400
        assert "500" in resp.json()["detail"]


class TestEnqueueFailure:
    async def test_enqueue_failure_marks_article_failed(self) -> None:
        """POST /api/articles marks article as 'failed' when queue.send() raises."""
        calls: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            calls.append({"sql": sql, "params": params})
            return []

        class FailingQueue:
            messages: list = []

            async def send(self, message: Any, **kwargs: Any) -> None:
                raise RuntimeError("Queue unavailable")

        queue = FailingQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles",
            json={"url": "https://example.com/queue-fail"},
        )

        assert resp.status_code == 503
        assert "enqueue" in resp.json()["detail"].lower()

        # Verify D1 UPDATE set status='failed'
        update_calls = [c for c in calls if c["sql"].startswith("UPDATE")]
        assert len(update_calls) >= 1
        assert "failed" in update_calls[0]["params"]


class TestRejectsInvalidReadingStatus:
    async def test_rejects_invalid_reading_status(self) -> None:
        """PATCH /api/articles/{id} returns 422 for invalid reading_status enum."""
        article = ArticleFactory.create(id="art_inv_rs", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_inv_rs":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_inv_rs",
            json={"reading_status": "invalid_status"},
        )

        assert resp.status_code == 422
        assert "reading_status" in resp.json()["detail"]


class TestRejectsInvalidReadingProgressBounds:
    async def test_rejects_reading_progress_above_one(self) -> None:
        """PATCH /api/articles/{id} returns 422 when reading_progress > 1.0."""
        article = ArticleFactory.create(id="art_rp_hi", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_rp_hi":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_rp_hi",
            json={"reading_progress": 1.5},
        )

        assert resp.status_code == 422
        assert "reading_progress" in resp.json()["detail"]

    async def test_rejects_reading_progress_below_zero(self) -> None:
        """PATCH /api/articles/{id} returns 422 when reading_progress < 0."""
        article = ArticleFactory.create(id="art_rp_lo", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_rp_lo":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_rp_lo",
            json={"reading_progress": -0.1},
        )

        assert resp.status_code == 422
        assert "reading_progress" in resp.json()["detail"]


class TestRejectsInvalidScrollPosition:
    async def test_rejects_negative_scroll_position(self) -> None:
        """PATCH /api/articles/{id} returns 422 for negative scroll_position."""
        article = ArticleFactory.create(id="art_sp_neg", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_sp_neg":
                return [article]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.patch(
            "/api/articles/art_sp_neg",
            json={"scroll_position": -1},
        )

        assert resp.status_code == 422
        assert "scroll_position" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# GET /api/articles/{article_id}/content — Serve article HTML
# ---------------------------------------------------------------------------


class TestGetArticleContent:
    async def test_content_endpoint_returns_html(self) -> None:
        """GET /api/articles/{id}/content returns HTML from R2."""
        article = ArticleFactory.create(
            id="art_html",
            user_id="user_001",
            html_key="articles/art_html/content.html",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_html":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        await r2.put("articles/art_html/content.html", "<p>Article content</p>")

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_html/content",
        )

        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "<p>Article content</p>" in resp.text

    async def test_content_endpoint_includes_csp_header(self) -> None:
        """GET /api/articles/{id}/content includes a restrictive CSP header."""
        article = ArticleFactory.create(
            id="art_csp",
            user_id="user_001",
            html_key="articles/art_csp/content.html",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_csp":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        await r2.put("articles/art_csp/content.html", "<p>Content with CSP</p>")

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_csp/content",
        )

        assert resp.status_code == 200
        assert "content-security-policy" in resp.headers
        assert "default-src 'none'" in resp.headers["content-security-policy"]

    async def test_content_endpoint_not_found(self) -> None:
        """GET /api/articles/{id}/content returns 404 when no HTML in R2."""
        article = ArticleFactory.create(
            id="art_nohtml",
            user_id="user_001",
            html_key="articles/art_nohtml/content.html",
        )

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_nohtml":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()  # Empty R2
        env = MockEnv(db=db, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_nohtml/content",
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/articles/{article_id}/metadata — Serve article metadata
# ---------------------------------------------------------------------------


class TestGetArticleMetadata:
    async def test_metadata_endpoint(self) -> None:
        """GET /api/articles/{id}/metadata returns metadata JSON from R2."""
        article = ArticleFactory.create(id="art_meta", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_meta":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        import json as _json

        metadata = {"article_id": "art_meta", "word_count": 500}
        await r2.put("articles/art_meta/metadata.json", _json.dumps(metadata))

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_meta/metadata",
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["article_id"] == "art_meta"
        assert data["word_count"] == 500

    async def test_metadata_not_found(self) -> None:
        """GET /api/articles/{id}/metadata returns 404 when no metadata in R2."""
        article = ArticleFactory.create(id="art_nometa", user_id="user_001")

        def execute(sql: str, params: list) -> list:
            if "id = ?" in sql and params[0] == "art_nometa":
                return [article]
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()  # Empty R2
        env = MockEnv(db=db, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_nometa/metadata",
        )

        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/articles?status=... — Filter by status
# ---------------------------------------------------------------------------


class TestFilterByStatus:
    async def test_filters_by_status(self) -> None:
        """GET /api/articles?status=ready includes status filter in query."""
        captured: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            captured.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        client.get(
            "/api/articles?status=ready",
        )

        select_calls = [c for c in captured if "SELECT" in c["sql"]]
        assert len(select_calls) >= 1
        assert "status = ?" in select_calls[0]["sql"]
        assert "ready" in select_calls[0]["params"]

    async def test_rejects_invalid_status(self) -> None:
        """GET /api/articles?status=bogus returns 422."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.get(
            "/api/articles?status=bogus",
        )

        assert resp.status_code == 422
        assert "status" in resp.json()["detail"]

    async def test_rejects_invalid_audio_status_filter(self) -> None:
        """GET /api/articles?audio_status=bogus returns 422."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)

        resp = client.get(
            "/api/articles?audio_status=bogus",
        )

        assert resp.status_code == 422
        assert "audio_status" in resp.json()["detail"]


class TestAuthRequired:
    def test_post_returns_401_without_auth(self) -> None:
        """POST /api/articles returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/articles", json={"url": "https://example.com"})
        assert resp.status_code == 401

    def test_get_list_returns_401_without_auth(self) -> None:
        """GET /api/articles returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/articles")
        assert resp.status_code == 401

    def test_get_single_returns_401_without_auth(self) -> None:
        """GET /api/articles/{id} returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/articles/some_id")
        assert resp.status_code == 401

    def test_patch_returns_401_without_auth(self) -> None:
        """PATCH /api/articles/{id} returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.patch("/api/articles/some_id", json={"title": "new"})
        assert resp.status_code == 401

    def test_delete_returns_401_without_auth(self) -> None:
        """DELETE /api/articles/{id} returns 401 without a session cookie."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.delete("/api/articles/some_id")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/articles/{article_id}/retry — Retry failed/pending article
# ---------------------------------------------------------------------------


class TestRetryArticle:
    async def test_retries_failed_article(self) -> None:
        """POST /api/articles/{id}/retry re-queues a failed article."""
        article = ArticleFactory.create(id="art_fail", user_id="user_001", status="failed")
        updates: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql and params[0] == "art_fail":
                return [article]
            if sql.startswith("UPDATE"):
                updates.append({"sql": sql, "params": params})
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_fail/retry",
        )

        assert resp.status_code == 202
        data = resp.json()
        assert data["id"] == "art_fail"
        assert data["status"] == "pending"

        # Verify D1 UPDATE set status='pending'
        assert len(updates) >= 1
        assert "pending" in updates[0]["sql"]

        # Verify queue message
        assert len(queue.messages) == 1
        msg = queue.messages[0]
        assert msg["type"] == "article_processing"
        assert msg["article_id"] == "art_fail"

    async def test_retries_pending_article(self) -> None:
        """POST /api/articles/{id}/retry re-queues a pending (stuck) article."""
        article = ArticleFactory.create(id="art_stuck", user_id="user_001", status="pending")

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql and params[0] == "art_stuck":
                return [article]
            return []

        queue = MockQueue()
        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_stuck/retry",
        )

        assert resp.status_code == 202
        assert resp.json()["status"] == "pending"
        assert len(queue.messages) == 1

    async def test_retries_ready_article(self) -> None:
        """POST /api/articles/{id}/retry re-queues a ready article."""
        article = ArticleFactory.create(id="art_ready", user_id="user_001", status="ready")

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql and params[0] == "art_ready":
                return [article]
            return []

        db = MockD1(execute=execute)
        queue = MockQueue()
        env = MockEnv(db=db, article_queue=queue)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_ready/retry",
        )

        assert resp.status_code == 202
        assert resp.json()["status"] == "pending"
        assert len(queue.messages) == 1

    async def test_returns_404_for_unknown_article(self) -> None:
        """POST /api/articles/{id}/retry returns 404 for nonexistent article."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/nonexistent/retry",
        )

        assert resp.status_code == 404

    def test_returns_401_without_auth(self) -> None:
        """POST /api/articles/{id}/retry returns 401 without auth."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/articles/some-id/retry")
        assert resp.status_code == 401

    async def test_returns_503_on_queue_failure(self) -> None:
        """POST /api/articles/{id}/retry returns 503 and marks failed on queue error."""
        article = ArticleFactory.create(id="art_qfail", user_id="user_001", status="failed")
        updates: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            if sql.startswith("SELECT") and "id = ?" in sql and params[0] == "art_qfail":
                return [article]
            if sql.startswith("UPDATE"):
                updates.append({"sql": sql, "params": params})
            return []

        class FailingQueue:
            messages: list = []

            async def send(self, message: Any, **kwargs: Any) -> None:
                raise RuntimeError("Queue unavailable")

        db = MockD1(execute=execute)
        env = MockEnv(db=db, article_queue=FailingQueue())

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/art_qfail/retry",
        )

        assert resp.status_code == 503
        assert "enqueue" in resp.json()["detail"].lower()

        # Should have 2 updates: first to 'pending', then to 'failed'
        assert len(updates) >= 2
        assert "failed" in updates[-1]["params"]


# ---------------------------------------------------------------------------
# POST /api/articles/batch-update — Batch update articles
# ---------------------------------------------------------------------------


class TestBatchUpdateArticles:
    async def test_batch_update_reading_status(self) -> None:
        """POST /api/articles/batch-update updates reading_status for all given articles."""
        updates: list[dict[str, Any]] = []

        def execute(sql: str, params: list) -> list:
            if "UPDATE articles SET" in sql:
                updates.append({"sql": sql, "params": params})
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/batch-update",
            json={
                "article_ids": ["art_bu1", "art_bu2"],
                "updates": {"reading_status": "archived"},
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 2
        assert len(updates) == 2

    async def test_batch_update_empty_ids_returns_422(self) -> None:
        """POST /api/articles/batch-update rejects empty article_ids."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/batch-update",
            json={"article_ids": [], "updates": {"reading_status": "archived"}},
        )
        assert resp.status_code == 422

    async def test_batch_update_empty_updates_returns_422(self) -> None:
        """POST /api/articles/batch-update rejects empty updates."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/batch-update",
            json={"article_ids": ["art_1"], "updates": {}},
        )
        assert resp.status_code == 422

    async def test_batch_update_invalid_field_returns_422(self) -> None:
        """POST /api/articles/batch-update rejects invalid update fields."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/batch-update",
            json={"article_ids": ["art_1"], "updates": {"title": "Hacked"}},
        )
        assert resp.status_code == 422
        assert "Invalid update fields" in resp.json()["detail"]

    async def test_batch_update_invalid_reading_status_returns_422(self) -> None:
        """POST /api/articles/batch-update rejects invalid reading_status value."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/batch-update",
            json={"article_ids": ["art_1"], "updates": {"reading_status": "bad"}},
        )
        assert resp.status_code == 422

    async def test_batch_update_too_many_ids_returns_422(self) -> None:
        """POST /api/articles/batch-update rejects more than 100 IDs."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)
        ids = ["art_" + str(i) for i in range(101)]
        resp = client.post(
            "/api/articles/batch-update",
            json={"article_ids": ids, "updates": {"reading_status": "archived"}},
        )
        assert resp.status_code == 422
        assert "100" in resp.json()["detail"]

    async def test_batch_update_requires_auth(self) -> None:
        """POST /api/articles/batch-update returns 401 without auth."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/articles/batch-update",
            json={"article_ids": ["art_1"], "updates": {"reading_status": "archived"}},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/articles/batch-delete — Batch delete articles
# ---------------------------------------------------------------------------


class TestBatchDeleteArticles:
    async def test_batch_delete_articles(self) -> None:
        """POST /api/articles/batch-delete deletes all specified articles."""
        deletes: list[str] = []

        def execute(sql: str, params: list) -> list:
            if "SELECT id FROM articles" in sql:
                # Simulate found articles
                article_id = params[0]
                if article_id in ("art_bd1", "art_bd2"):
                    return [{"id": article_id}]
                return []
            if "DELETE FROM articles" in sql:
                deletes.append(params[0])
            return []

        db = MockD1(execute=execute)
        r2 = MockR2()
        env = MockEnv(db=db, content=r2)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/batch-delete",
            json={"article_ids": ["art_bd1", "art_bd2"]},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["deleted"] == 2
        assert "art_bd1" in deletes
        assert "art_bd2" in deletes

    async def test_batch_delete_skips_nonexistent(self) -> None:
        """POST /api/articles/batch-delete skips articles that don't exist for user."""

        def execute(sql: str, params: list) -> list:
            if "SELECT id FROM articles" in sql:
                return []  # No articles found
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)

        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/batch-delete",
            json={"article_ids": ["nonexistent_1", "nonexistent_2"]},
        )

        assert resp.status_code == 200
        assert resp.json()["deleted"] == 0

    async def test_batch_delete_empty_ids_returns_422(self) -> None:
        """POST /api/articles/batch-delete rejects empty article_ids."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)
        resp = client.post(
            "/api/articles/batch-delete",
            json={"article_ids": []},
        )
        assert resp.status_code == 422

    async def test_batch_delete_too_many_ids_returns_422(self) -> None:
        """POST /api/articles/batch-delete rejects more than 100 IDs."""
        env = MockEnv()
        client, session_id = await _authenticated_client(env)
        ids = ["art_" + str(i) for i in range(101)]
        resp = client.post(
            "/api/articles/batch-delete",
            json={"article_ids": ids},
        )
        assert resp.status_code == 422
        assert "100" in resp.json()["detail"]

    async def test_batch_delete_requires_auth(self) -> None:
        """POST /api/articles/batch-delete returns 401 without auth."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/articles/batch-delete",
            json={"article_ids": ["art_1"]},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/articles/{article_id}/markdown — Serve article markdown
# ---------------------------------------------------------------------------


class TestGetArticleMarkdown:
    async def test_returns_markdown_content(self) -> None:
        """GET /api/articles/{id}/markdown returns stored markdown as text."""
        article = ArticleFactory.create(
            id="art_md",
            markdown_content="# Hello\n\nWorld",
        )

        def execute(sql, params):
            if "SELECT" in sql:
                return [article]
            return []

        env = MockEnv(db=MockD1(execute=execute))
        client, sid = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_md/markdown",
        )
        assert resp.status_code == 200
        assert resp.text == "# Hello\n\nWorld"
        assert "text/markdown" in resp.headers["content-type"]

    async def test_returns_cache_header(self) -> None:
        """GET /api/articles/{id}/markdown includes Cache-Control header."""
        article = ArticleFactory.create(
            id="art_md_cache",
            markdown_content="# Cached",
        )

        def execute(sql, params):
            if "SELECT" in sql:
                return [article]
            return []

        env = MockEnv(db=MockD1(execute=execute))
        client, sid = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_md_cache/markdown",
        )
        assert resp.status_code == 200
        assert "private" in resp.headers.get("cache-control", "")

    async def test_returns_404_when_no_markdown(self) -> None:
        """GET /api/articles/{id}/markdown returns 404 when markdown is empty."""
        article = ArticleFactory.create(
            id="art_nomd",
            markdown_content=None,
        )

        def execute(sql, params):
            if "SELECT" in sql:
                return [article]
            return []

        env = MockEnv(db=MockD1(execute=execute))
        client, sid = await _authenticated_client(env)
        resp = client.get(
            "/api/articles/art_nomd/markdown",
        )
        assert resp.status_code == 404

    async def test_requires_auth(self) -> None:
        """GET /api/articles/{id}/markdown returns 401 without auth."""
        env = MockEnv()
        app = _make_app(env)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/articles/some-id/markdown")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Cross-user article isolation
# ---------------------------------------------------------------------------


class TestArticleOwnershipIsolation:
    """Verify that a user cannot access, modify, or delete another user's articles.

    The _get_user_article helper uses WHERE id = ? AND user_id = ?, so
    cross-user access returns 404 (not 403) to avoid revealing article existence.
    """

    async def test_get_returns_404_for_other_users_article(self) -> None:
        """GET /api/articles/{id} returns 404 when article belongs to another user."""
        other_users_article = ArticleFactory.create(
            id="art_other",
            user_id="user_002",
        )

        def execute(sql: str, params: list) -> list:
            # The query is: WHERE id = ? AND user_id = ?
            # Since user_id won't match user_001 (the authenticated user),
            # return empty to simulate no match.
            if "id = ?" in sql and "user_id = ?" in sql:
                art_id = params[0]
                uid = params[1]
                if art_id == "art_other" and uid == other_users_article["user_id"]:
                    return [other_users_article]
                return []  # user_001 asking for user_002's article → no match
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        resp = client.get(
            "/api/articles/art_other",
        )
        assert resp.status_code == 404

    async def test_patch_returns_404_for_other_users_article(self) -> None:
        """PATCH /api/articles/{id} returns 404 for another user's article."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        resp = client.patch(
            "/api/articles/art_other",
            json={"reading_status": "archived"},
        )
        assert resp.status_code == 404

    async def test_delete_returns_404_for_other_users_article(self) -> None:
        """DELETE /api/articles/{id} returns 404 for another user's article."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        resp = client.delete(
            "/api/articles/art_other",
        )
        assert resp.status_code == 404

    async def test_content_returns_404_for_other_users_article(self) -> None:
        """GET /api/articles/{id}/content returns 404 for another user's article."""
        db = MockD1(execute=lambda sql, params: [])
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        resp = client.get(
            "/api/articles/art_other/content",
        )
        assert resp.status_code == 404

    async def test_list_only_returns_own_articles(self) -> None:
        """GET /api/articles only returns articles belonging to the authenticated user."""
        own_article = ArticleFactory.create(id="art_mine", user_id="user_001")
        other_article = ArticleFactory.create(id="art_theirs", user_id="user_002")

        def execute(sql: str, params: list) -> list:
            if "FROM articles" in sql and "user_id = ?" in sql:
                uid = params[0]
                # Only return articles matching the requested user_id
                return [a for a in [own_article, other_article] if a["user_id"] == uid]
            return []

        db = MockD1(execute=execute)
        env = MockEnv(db=db)
        client, session_id = await _authenticated_client(env)

        resp = client.get(
            "/api/articles",
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "art_mine"
