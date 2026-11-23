-- Historical rows retain NULL for analytics that were not recorded at the time.
ALTER TABLE question_logs ADD COLUMN user_key TEXT;
ALTER TABLE question_logs ADD COLUMN endpoint TEXT;
ALTER TABLE question_logs ADD COLUMN answer TEXT;
ALTER TABLE question_logs ADD COLUMN outcome TEXT;
ALTER TABLE question_logs ADD COLUMN http_status INTEGER;
ALTER TABLE question_logs ADD COLUMN failure_stage TEXT;
ALTER TABLE question_logs ADD COLUMN error_type TEXT;
ALTER TABLE question_logs ADD COLUMN duration_ms INTEGER;
ALTER TABLE question_logs ADD COLUMN retrieval_ms INTEGER;
ALTER TABLE question_logs ADD COLUMN generation_ms INTEGER;
ALTER TABLE question_logs ADD COLUMN model TEXT;
ALTER TABLE question_logs ADD COLUMN prompt_sha TEXT;
ALTER TABLE question_logs ADD COLUMN corpus_version TEXT;
ALTER TABLE question_logs ADD COLUMN sources_json TEXT;
ALTER TABLE question_logs ADD COLUMN input_tokens INTEGER;
ALTER TABLE question_logs ADD COLUMN output_tokens INTEGER;
ALTER TABLE question_logs ADD COLUMN total_tokens INTEGER;
-- Reserved for cost estimates; pricing is not currently configured.
ALTER TABLE question_logs ADD COLUMN estimated_cost REAL;
ALTER TABLE question_logs ADD COLUMN finish_reason TEXT;
CREATE INDEX IF NOT EXISTS question_logs_user_key_idx ON question_logs (user_key);
