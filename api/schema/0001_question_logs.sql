CREATE TABLE IF NOT EXISTS question_logs (
    created_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    question_asked TEXT,
    cache_key TEXT,
    answered_from_cache INTEGER NOT NULL DEFAULT 0,
    user_uuid TEXT,
    answer TEXT,
    outcome TEXT,
    http_status INTEGER,
    failure_stage TEXT,
    error_type TEXT,
    duration_ms INTEGER,
    retrieval_ms INTEGER,
    generation_ms INTEGER,
    model TEXT,
    corpus_version TEXT,
    sources_json TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    finish_reason TEXT
);

CREATE INDEX IF NOT EXISTS question_logs_created_at_idx
    ON question_logs (created_at);
CREATE INDEX IF NOT EXISTS question_logs_user_uuid_idx
    ON question_logs (user_uuid);