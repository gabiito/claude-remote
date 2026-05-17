#!/usr/bin/env bash
# Bootstrap installer for claude-remote.
#
# Run once after getting the repo:  ./install.sh
# Idempotent — safe to re-run (it re-syncs deps and re-applies the service).
#
# What it does, in order:
#   1. verifies `uv` is available (the Python toolchain this project uses)
#   2. `uv sync`           → creates .venv, installs runtime deps,
#                            registers the `claudio` console script
#   3. `uv run claudio install` → renders the systemd --user unit pointing
#                            at THIS checkout's .venv, enables + starts it,
#                            enables linger, symlinks ~/.local/bin/claudio
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

if ! command -v uv >/dev/null 2>&1; then
  cat >&2 <<'EOF'
error: `uv` is not installed (the Python toolchain this project uses).

Install it, then re-run ./install.sh :
  curl -LsSf https://astral.sh/uv/install.sh | sh
  # or see https://docs.astral.sh/uv/getting-started/installation/
EOF
  exit 1
fi

echo ">> Creating the virtualenv and installing dependencies (uv sync)…"
uv sync

echo ">> Installing + starting the systemd --user service (claudio install)…"
uv run claudio install

cat <<'EOF'

Done. The service is running and will auto-start on login.

  Lifecycle:  claudio status | restart | stop | logs
              (needs ~/.local/bin on PATH; otherwise: uv run claudio status)

  First use:  open  http://<this-host>:8000  from your phone (over
              Tailscale) — it walks you through choosing the projects
              folder.
EOF
