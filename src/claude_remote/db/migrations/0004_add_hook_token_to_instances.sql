ALTER TABLE instances ADD COLUMN hook_token TEXT;
UPDATE instances SET hook_token = lower(hex(randomblob(24))) WHERE hook_token IS NULL;
CREATE UNIQUE INDEX idx_instances_hook_token ON instances(hook_token)
