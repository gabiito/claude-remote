-- Auth (#7): mandatory shared password + session signing secret, on the
-- existing app_settings singleton. NULL password_hash = not configured →
-- the auth gate serves a "run claudio set-password" notice until set.
ALTER TABLE app_settings ADD COLUMN password_hash TEXT;
ALTER TABLE app_settings ADD COLUMN session_secret TEXT;
