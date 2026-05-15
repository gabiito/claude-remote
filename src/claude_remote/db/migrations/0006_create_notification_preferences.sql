CREATE TABLE notification_preferences (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    notify_on_notification INTEGER NOT NULL DEFAULT 1,
    notify_on_stop INTEGER NOT NULL DEFAULT 0,
    notify_on_session_end INTEGER NOT NULL DEFAULT 0,
    notify_on_session_start INTEGER NOT NULL DEFAULT 0,
    notify_on_pre_tool_use INTEGER NOT NULL DEFAULT 0,
    notify_on_post_tool_use INTEGER NOT NULL DEFAULT 0,
    quiet_hours_start TEXT,
    quiet_hours_end TEXT,
    ntfy_topic TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO notification_preferences (id, ntfy_topic, updated_at)
  VALUES (1, lower(hex(randomblob(16))), datetime('now'))
