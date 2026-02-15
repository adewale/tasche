-- Add unique constraint on (user_id, original_url) to prevent duplicate articles.
-- This prevents race conditions where two near-simultaneous requests could create
-- duplicate articles for the same URL.

CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_user_url
    ON articles(user_id, original_url);
