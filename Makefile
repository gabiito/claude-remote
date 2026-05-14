.PHONY: setup test test-unit test-integration lint format typecheck run check

PYTHON := .venv/bin/python
UV := uv

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

# Start dev server (blocks)
run:
	$(PYTHON) -m uvicorn claude_remote.app:app --reload --host 0.0.0.0 --port 8000

# Composite gate: lint + typecheck + test (local CI equivalent)
check: lint typecheck test
