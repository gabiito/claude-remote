# Migrations

Raw SQL migration files, applied in lexicographic order.

## Naming convention

```
NNNN_description.sql
```

Examples:
- `0001_create_sessions.sql`
- `0002_add_events_table.sql`

## Runner

The migration runner ships in the `mvp-db-schema` slice. For now this directory
holds the convention and the `.gitkeep` so the path exists in the repo.
