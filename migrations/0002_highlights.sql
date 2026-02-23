-- Tasche: Highlights table for annotations and spaced repetition
-- Applies to: D1 binding "DB"

-- =========================================================================
-- Highlights
-- =========================================================================

CREATE TABLE IF NOT EXISTS highlights (
    id          TEXT PRIMARY KEY,
    article_id  TEXT NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    note        TEXT DEFAULT '',
    prefix      TEXT DEFAULT '',
    suffix      TEXT DEFAULT '',
    color       TEXT DEFAULT 'yellow',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_highlights_article
    ON highlights(article_id);
