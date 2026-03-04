CREATE TABLE IF NOT EXISTS user_preferences (
    user_id     TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    tts_voice   TEXT DEFAULT 'athena' CHECK(tts_voice IN ('athena', 'orion')),
    created_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')),
    updated_at  TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))
);
