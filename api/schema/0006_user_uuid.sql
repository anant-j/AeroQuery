DROP INDEX IF EXISTS question_logs_user_key_idx;
ALTER TABLE question_logs RENAME COLUMN user_key TO user_uuid;
CREATE INDEX IF NOT EXISTS question_logs_user_uuid_idx ON question_logs (user_uuid);
