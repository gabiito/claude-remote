CREATE TABLE instances (
    id                 TEXT PRIMARY KEY,
    project_id         TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    tmux_session_name  TEXT NOT NULL UNIQUE,
    pane_pid           INTEGER,
    status             TEXT NOT NULL CHECK(status IN ('starting','running','stopped','crashed')),
    created_at         TEXT NOT NULL,
    stopped_at         TEXT
);

CREATE INDEX idx_instances_project_id ON instances(project_id)
