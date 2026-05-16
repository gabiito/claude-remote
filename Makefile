.PHONY: setup test test-unit test-integration lint format typecheck run check \
        service-install service-uninstall service-status service-logs

PYTHON := .venv/bin/python
UV := uv

SYSTEMD_USER_DIR := $(HOME)/.config/systemd/user
SERVICE_NAME := claude-remote.service

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

# Install + start the keep-alive systemd --user service (auto-starts on login).
# enable-linger lets it keep running after logout / start at boot.
service-install:
	mkdir -p $(SYSTEMD_USER_DIR)
	$(PYTHON) deploy/render_service.py > $(SYSTEMD_USER_DIR)/$(SERVICE_NAME)
	systemctl --user daemon-reload
	systemctl --user enable --now $(SERVICE_NAME)
	loginctl enable-linger $(USER) || echo "NOTE: run 'sudo loginctl enable-linger $(USER)' so it survives logout/boot"
	@echo "Installed + started. Status: make service-status | Logs: make service-logs"

# Stop, disable and remove the service.
service-uninstall:
	-systemctl --user disable --now $(SERVICE_NAME)
	-rm -f $(SYSTEMD_USER_DIR)/$(SERVICE_NAME)
	systemctl --user daemon-reload
	@echo "Removed."

service-status:
	systemctl --user status $(SERVICE_NAME) --no-pager || true

service-logs:
	journalctl --user -u $(SERVICE_NAME) -f
