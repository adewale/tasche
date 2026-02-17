-- Tasche: Initial D1 schema
-- Applies to: D1 binding "DB"
-- Compatibility: SQLite (D1 is serverless SQLite)

-- =========================================================================
-- Users
-- =========================================================================

CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    github_id       INTEGER UNIQUE,
    email           TEXT,
    username        TEXT,
    avatar_url      TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

-- =========================================================================
-- Articles
-- =========================================================================

CREATE TABLE IF NOT EXISTS articles (
    id                      TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    original_url            TEXT NOT NULL,
    final_url               TEXT,
    canonical_url           TEXT,
    domain                  TEXT,
    title                   TEXT,
    excerpt                 TEXT,
    author                  TEXT,
    word_count              INTEGER,
    reading_time_minutes    INTEGER,
    image_count             INTEGER DEFAULT 0,
    status                  TEXT DEFAULT 'pending'
                            CHECK(status IN ('pending', 'processing', 'ready', 'failed')),
    reading_status          TEXT DEFAULT 'unread'
                            CHECK(reading_status IN ('unread', 'reading', 'archived')),
    is_favorite             INTEGER DEFAULT 0,
    listen_later            INTEGER DEFAULT 0,
    audio_key               TEXT,
    audio_duration_seconds  INTEGER,
    audio_status            TEXT DEFAULT NULL
                            CHECK(audio_status IS NULL OR audio_status IN ('pending', 'generating', 'ready', 'failed')),
    html_key                TEXT,
    markdown_key            TEXT,
    thumbnail_key           TEXT,
    markdown_content        TEXT,
    original_status         TEXT DEFAULT 'unknown'
                            CHECK(original_status IN ('available', 'paywalled', 'gone', 'domain_dead', 'unknown')),
    last_checked_at         TEXT DEFAULT NULL,
    scroll_position         REAL DEFAULT 0,
    reading_progress        REAL DEFAULT 0,
    created_at              TEXT DEFAULT (datetime('now')),
    updated_at              TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- =========================================================================
-- Tags
-- =========================================================================

CREATE TABLE IF NOT EXISTS tags (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    name            TEXT NOT NULL,
    created_at      TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- =========================================================================
-- Article-Tags junction table
-- =========================================================================

CREATE TABLE IF NOT EXISTS article_tags (
    article_id      TEXT NOT NULL,
    tag_id          TEXT NOT NULL,
    PRIMARY KEY (article_id, tag_id),
    FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id)     REFERENCES tags(id)     ON DELETE CASCADE
);

-- =========================================================================
-- Full-text search (FTS5)
-- =========================================================================

CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
    title,
    excerpt,
    markdown_content,
    content=articles,
    content_rowid=rowid
);

-- =========================================================================
-- FTS5 content-sync triggers
-- =========================================================================

CREATE TRIGGER IF NOT EXISTS articles_fts_ai AFTER INSERT ON articles BEGIN
    INSERT INTO articles_fts(rowid, title, excerpt, markdown_content)
    VALUES (new.rowid, new.title, new.excerpt, new.markdown_content);
END;

CREATE TRIGGER IF NOT EXISTS articles_fts_ad AFTER DELETE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, excerpt, markdown_content)
    VALUES ('delete', old.rowid, old.title, old.excerpt, old.markdown_content);
END;

CREATE TRIGGER IF NOT EXISTS articles_fts_au AFTER UPDATE ON articles BEGIN
    INSERT INTO articles_fts(articles_fts, rowid, title, excerpt, markdown_content)
    VALUES ('delete', old.rowid, old.title, old.excerpt, old.markdown_content);
    INSERT INTO articles_fts(rowid, title, excerpt, markdown_content)
    VALUES (new.rowid, new.title, new.excerpt, new.markdown_content);
END;

-- =========================================================================
-- Indexes
-- =========================================================================

CREATE INDEX IF NOT EXISTS idx_articles_user_created
    ON articles(user_id, created_at);

CREATE INDEX IF NOT EXISTS idx_articles_user_reading_status
    ON articles(user_id, reading_status);

CREATE INDEX IF NOT EXISTS idx_articles_original_url
    ON articles(original_url);

CREATE INDEX IF NOT EXISTS idx_articles_final_url
    ON articles(final_url);

CREATE INDEX IF NOT EXISTS idx_articles_canonical_url
    ON articles(canonical_url);

CREATE UNIQUE INDEX IF NOT EXISTS idx_tags_user_name
    ON tags(user_id, name);

CREATE INDEX IF NOT EXISTS idx_users_email
    ON users(email);
