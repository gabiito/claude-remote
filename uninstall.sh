#!/usr/bin/env bash
# Uninstaller for claude-remote — symmetric to install.sh.
#
# Run:  ./uninstall.sh
#
# Always:   stops + disables the systemd --user service, removes the unit,
#           daemon-reload, removes the ~/.local/bin/claudio symlink
#           (this is `claudio uninstall`).
# Optional: asks (default No) whether to also delete the .venv and the
#           local database. The repo folder itself and `loginctl
#           enable-linger` are left for you to handle manually.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

confirm() { # confirm "question" -> 0 if user typed y/Y, default No
  local reply
  read -r -p "$1 [y/N] " reply
  [[ "$reply" == [yY] ]]
}

echo ">> Stopping + removing the systemd --user service (claudio uninstall)…"
if command -v uv >/dev/null 2>&1 && [[ -x .venv/bin/claudio ]]; then
  uv run claudio uninstall
elif command -v claudio >/dev/null 2>&1; then
  claudio uninstall
else
  echo "   (could not find claudio — .venv already gone? skipping service step)" >&2
fi

if [[ -d .venv ]] && confirm ">> Delete the virtualenv (.venv)?"; then
  rm -rf .venv
  echo "   .venv removed."
fi

DB="${CLAUDE_REMOTE_DB_PATH:-$REPO_ROOT/claude-remote.db}"
if [[ -f "$DB" ]] && confirm ">> Delete the database ($DB)? This erases your projects-root config, push subscriptions and event history."; then
  rm -f "$DB" "$DB-wal" "$DB-shm"
  echo "   database removed."
fi

cat <<'EOF'

Done.

Not touched (remove manually if you want a full wipe):
  - the repo folder itself          rm -rf <this folder>
  - login linger (auto-start)       loginctl disable-linger "$USER"
EOF
