# Claudio-RC (claude-remote)

Monitor and control your Claude Code CLI sessions from your phone — a
mobile-first PWA you reach over Tailscale. Launch / stop sessions, watch
their terminals live, and get web-push notifications when a session needs
you.

## Install

On the machine that runs your Claude Code sessions (Linux, systemd
`--user`):

```bash
git clone <repo> claude-remote
cd claude-remote
./install.sh                # uv sync + installs & starts the systemd --user service
claudio set-password        # REQUIRED — the app is locked until this is set
```

Then open `http://<this-host>:8000` from your phone over Tailscale, sign
in, and (first run) point it at your projects folder.

> The whole app is behind a single shared password. Until you run
> `claudio set-password` every page redirects to a login screen that tells
> you exactly that.

`install.sh` needs [`uv`](https://docs.astral.sh/uv/) and the git
checkout layout. Uninstall with `./uninstall.sh` (optionally wipes the
venv + database) or `claudio uninstall`.

## The `claudio` command

```
claudio install        set up + start the systemd --user service (auto-start on login)
claudio uninstall      stop + remove the service and the claudio symlink
claudio start|stop|restart|status
claudio logs           follow the service logs
claudio set-password   set the shared login password (rotates sessions: logs out all devices)
claudio --version      git-derived version (same source as the in-app header)
claudio --help
```

`set-password` writes to the same database the server uses
(`CLAUDE_REMOTE_DB_PATH`, default `./claude-remote.db`), so run it from the
repo root / with the same env as the server. No restart needed — the next
request picks it up.

## Notifications

Web push (PWA). Notifications are **presence-aware**: if you're actively
using the app on a device, that device won't buzz for something you're
already watching — it only notifies when you've been away.

## Development

```bash
make setup        # uv sync --extra dev
make run          # dev server (uvicorn --reload) at http://0.0.0.0:8000
make test         # pytest
make lint         # ruff
make typecheck    # pyright
make check        # lint + typecheck + test
```

For `make run` the app is still password-gated: from the repo root run
`uv run claudio set-password` (writes to `./claude-remote.db`) after the
server has started once (so migrations apply).

## System requirements

- Python 3.12+
- `tmux` — required at runtime to launch Claude Code instances
  (integration tests auto-skip without it)
- `uv` for dependency management
- Linux with systemd `--user` (for `claudio install`)
- Tailscale (to reach the app from your phone)

### Tests

```bash
uv run pytest -m requires_tmux tests/integration/ -v   # integration (needs tmux)
uv run pytest -m "not requires_tmux"                    # unit only
```
