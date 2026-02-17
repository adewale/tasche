-- Add notes column for user annotations on articles.
-- Spec section 5.1: notes TEXT, max 10000 characters.

ALTER TABLE articles ADD COLUMN notes TEXT DEFAULT NULL;
