-- Convert any articles with reading_status='reading' to 'unread'.
-- The CHECK constraint still allows 'reading' (SQLite cannot alter CHECK
-- constraints without recreating the table), but the application no longer
-- sets or filters by this value.
UPDATE articles SET reading_status = 'unread' WHERE reading_status = 'reading';
