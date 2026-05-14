CREATE TABLE events (
    id TEXT PRIMARY KEY,
    instance_id TEXT REFERENCES instances(id) ON DELETE CASCADE,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL CHECK(event_type IN ('SessionStart','Notification','Stop','PreToolUse','PostToolUse','SessionEnd')),
    payload TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE INDEX idx_events_project_received ON events(project_id, received_at DESC);
CREATE INDEX idx_events_instance_received ON events(instance_id, received_at DESC)
