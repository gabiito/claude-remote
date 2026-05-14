# claude-remote

Monitor and control Claude Code sessions from your phone.

## Quickstart

```bash
# Install deps
make setup

# Start dev server at http://localhost:8000
make run
```

## Development

```bash
# Run tests
make test

# Lint
make lint

# Format in place
make format

# Type-check
make typecheck

# Full check gate (lint + typecheck + test)
make check
```

## System Requirements

- Python 3.12+
- `tmux` (system binary) — required at runtime to launch Claude Code instances. Integration tests are skipped if `tmux` is not on PATH.
- `uv` for dependency management — `pip install uv` or see [uv docs](https://docs.astral.sh/uv/).

### Integration tests

```bash
# Run only integration tests (requires tmux on PATH)
uv run pytest -m requires_tmux tests/integration/ -v

# Run only unit tests (no tmux binary needed)
uv run pytest -m "not requires_tmux"
```
