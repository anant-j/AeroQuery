CREATE TABLE IF NOT EXISTS question_logs (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    query_length INTEGER NOT NULL,
    mode TEXT NOT NULL,
    query_text TEXT
);

CREATE INDEX IF NOT EXISTS question_logs_created_at_idx
    ON question_logs (created_at);