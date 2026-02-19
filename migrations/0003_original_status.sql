-- Migration 0003: original_status additions
--
-- This migration originally added last_checked_at, but that column is
-- already defined in 0001_initial.sql (line 51). The ALTER TABLE has been
-- removed to avoid a "duplicate column name" error on fresh deploys.
--
-- last_checked_at already defined in 0001_initial.sql

-- No-op: all columns from this migration are present in the initial schema.
