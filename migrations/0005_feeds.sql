-- Tasche: RSS/Atom feed subscriptions
-- Applies to: D1 binding "DB"

CREATE TABLE IF NOT EXISTS feeds (
    id                      TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    url                     TEXT NOT NULL,
    title                   TEXT DEFAULT '',
    site_url                TEXT DEFAULT '',
    last_fetched_at         TEXT,
    last_entry_published    TEXT,
    fetch_interval_minutes  INTEGER DEFAULT 60,
    is_active               INTEGER DEFAULT 1,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_feeds_user_url
    ON feeds(user_id, url);

CREATE INDEX IF NOT EXISTS idx_feeds_user_active
    ON feeds(user_id, is_active);
