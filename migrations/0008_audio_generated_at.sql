-- Add audio_generated_at column to track when TTS audio was last generated.
-- Used for idempotency: skip regeneration when article content hasn't changed
-- since the audio was generated (updated_at <= audio_generated_at).

ALTER TABLE articles ADD COLUMN audio_generated_at TEXT DEFAULT NULL;
