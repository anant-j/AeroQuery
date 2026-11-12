DROP INDEX IF EXISTS question_logs_cache_key_idx;

ALTER TABLE question_logs RENAME COLUMN id TO correlation_id;
ALTER TABLE question_logs RENAME COLUMN query_text TO question_asked;
ALTER TABLE question_logs ADD COLUMN question_sha TEXT;
ALTER TABLE question_logs DROP COLUMN query_length;
ALTER TABLE question_logs DROP COLUMN cache_key;
ALTER TABLE question_logs DROP COLUMN cache_value;
ALTER TABLE question_logs DROP COLUMN cache_expires_at;