ALTER TABLE question_logs ADD COLUMN cache_key TEXT;
ALTER TABLE question_logs ADD COLUMN cache_value TEXT;
ALTER TABLE question_logs ADD COLUMN cache_expires_at INTEGER;
ALTER TABLE question_logs ADD COLUMN answered_from_cache INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS question_logs_cache_key_idx
    ON question_logs (cache_key, cache_expires_at);