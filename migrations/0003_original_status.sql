-- Add last_checked_at column to track when the original URL was last health-checked.
-- The original_status column already exists from 0001_initial.sql; this migration
-- adds the timestamp needed for periodic re-checking of original URLs.

ALTER TABLE articles ADD COLUMN last_checked_at TEXT DEFAULT NULL;
