-- The backend fetches a stored brief by brief_id, asks for briefs due in a send-time window,
-- and marks each one sent.
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS brief_id text;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS sent_at timestamptz;
UPDATE outbox SET brief_id = brief->>'brief_id' WHERE brief_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS outbox_brief_id ON outbox (brief_id);
CREATE INDEX IF NOT EXISTS outbox_send_at ON outbox (send_at_utc) WHERE sent_at IS NULL;
