-- Tag rules for auto-tagging articles based on domain, title, or URL patterns.

CREATE TABLE IF NOT EXISTS tag_rules (
    id          TEXT PRIMARY KEY,
    tag_id      TEXT NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    match_type  TEXT NOT NULL CHECK(match_type IN ('domain', 'title_contains', 'url_contains')),
    pattern     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tag_rules_tag ON tag_rules(tag_id);
