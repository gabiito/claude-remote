CREATE TABLE projects (
    id          TEXT PRIMARY KEY,
    slug        TEXT NOT NULL,
    name        TEXT NOT NULL,
    path        TEXT NOT NULL,
    domain      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    UNIQUE(domain, slug)
);

CREATE INDEX idx_projects_created_at ON projects(created_at DESC);
