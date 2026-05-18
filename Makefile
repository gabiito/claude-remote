.PHONY: setup test test-unit test-integration lint format typecheck run check \
        set-password service-install service-uninstall service-status service-logs

PYTHON := .venv/bin/python
UV := uv
CLAUDIO := .venv/bin/claudio

SERVICE_NAME := claude-remote.service

# Dev server isolation: own port + own DB so `make run` coexists with the
# installed systemd service (which is hardcoded to port 8000 + ./claude-remote.db).
# Both are overridable: `make run PORT=9000 DEV_DB=./scratch.db`.
PORT ?= 8001
DEV_DB ?= ./claude-remote.dev.db

# Install all deps (runtime + dev) into .venv
setup:
	$(UV) sync --extra dev

# Run all tests with coverage (integration tests auto-skip if tmux not on PATH)
test:
	$(PYTHON) -m pytest

# Run only unit/endpoint tests (no tmux binary required)
test-unit:
	$(PYTHON) -m pytest -m "not requires_tmux"

# Run only real-tmux integration tests (requires tmux on PATH)
test-integration:
	$(PYTHON) -m pytest -m requires_tmux tests/integration/ -v

# Lint source tree
lint:
	$(PYTHON) -m ruff check src/ tests/

# Format source tree in place
format:
	$(PYTHON) -m ruff format src/ tests/

# Type-check source tree
typecheck:
	$(PYTHON) -m pyright src/

# Start dev server (blocks) — isolated port + DB, coexists with installed service
run:
	CLAUDE_REMOTE_DB_PATH=$(DEV_DB) $(PYTHON) -m uvicorn claude_remote.app:app --reload --host 0.0.0.0 --port $(PORT)

# Set the login password on the DEV DB (same DEV_DB as `make run`, never the installed one)
set-password:
	CLAUDE_REMOTE_DB_PATH=$(DEV_DB) $(CLAUDIO) set-password

# Composite gate: lint + typecheck + test (local CI equivalent)
check: lint typecheck test

# Install/uninstall delegate to `claudio` — single source of truth for the
# render + enable + linger + symlink logic (see src/claude_remote/cli.py).
service-install:
	$(CLAUDIO) install

service-uninstall:
	$(CLAUDIO) uninstall

service-status:
	systemctl --user status $(SERVICE_NAME) --no-pager || true

service-logs:
	journalctl --user -u $(SERVICE_NAME) -f
