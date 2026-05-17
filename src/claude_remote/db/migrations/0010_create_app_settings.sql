CREATE TABLE IF NOT EXISTS app_settings (
    id INTEGER PRIMARY KEY CHECK(id = 1),
    projects_root TEXT,
    updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO app_settings (id, projects_root, updated_at)
  VALUES (1, NULL, datetime('now'))
