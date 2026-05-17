"""Shared Jinja2Templates singleton for all route modules.

Importing from app.py would create circular imports (app imports routers;
routers need TEMPLATES).  This thin module breaks the cycle.

Custom Jinja2 filters registered here (ADR-3):
  - ``format_relative``  → services/timefmt.py
  - ``extract_snippet``  → services/event_snippet.py
  - ``status_token``     → maps derive_live_status() output to CSS [data-status] token
"""

import subprocess
from functools import lru_cache
from pathlib import Path

from fastapi.templating import Jinja2Templates

from claude_remote.services.event_snippet import extract_snippet
from claude_remote.services.timefmt import format_relative


def status_token(live_status: str) -> str:
    """Map ``derive_live_status`` output → the CSS ``[data-status]`` token.

    The service layer uses ``needs_input`` (spec REQ-1) while the Catppuccin
    Mocha design system uses ``needs`` (shorter, set during mvp-project-view).
    Keep both readable in their own domain; bridge here.
    """
    if live_status == "needs_input":
        return "needs"
    return live_status


def _fmt_uptime(seconds: object) -> str:
    """Compact uptime: 3d 4h / 1h 12m / 34m / 38s. 0/invalid → "0s"."""
    try:
        s = int(seconds)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "0s"
    if s < 0:
        s = 0
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return f"{sec}s"


_PACKAGE_ROOT = Path(__file__).parent.parent
_STATIC_ROOT = _PACKAGE_ROOT / "static"
templates = Jinja2Templates(directory=_PACKAGE_ROOT / "templates")


def asset_url(rel_path: str) -> str:
    """Return ``/static/<rel_path>?v=<mtime>`` for cache-busting.

    The token is the asset's integer mtime, so any edit forces browsers and
    installed PWAs to refetch (Android PWAs cache static files aggressively and
    offer no hard-refresh). Re-stats every render — cheap, and picks up dev
    edits immediately. A missing file falls back to ``?v=0`` (never raises).
    """
    try:
        version = int((_STATIC_ROOT / rel_path).stat().st_mtime)
    except OSError:
        version = 0
    return f"/static/{rel_path}?v={version}"


@lru_cache(maxsize=1)
def app_version() -> str:
    """Return the running version, derived from git so a tag is the source of truth.

    `git describe --tags --always --dirty`:
      - tagged commit            → the tag (e.g. v0.1.0)
      - commits after a tag      → v0.1.0-3-gabc1234
      - uncommitted changes      → ...-dirty
      - no tags yet              → short SHA (honest: untagged commit)

    Falls back to the packaged metadata version, then "dev". Cached for the
    process (restart to pick up a new tag) and never raises.
    """
    try:
        out = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            capture_output=True,
            text=True,
            check=True,
            timeout=2,
            cwd=_PACKAGE_ROOT,
        ).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        from importlib.metadata import version

        return version("claude-remote")
    except Exception:  # noqa: BLE001
        return "dev"


# Register display helpers as Jinja2 filters/globals so templates call them
# inline. Jinja2Templates.env is untyped in the FastAPI stubs; the localized
# pyright suppressions keep that third-party noise out of the error baseline.
templates.env.filters["format_relative"] = format_relative  # pyright: ignore[reportUnknownMemberType]
templates.env.filters["extract_snippet"] = extract_snippet  # pyright: ignore[reportUnknownMemberType]
templates.env.filters["status_token"] = status_token  # pyright: ignore[reportUnknownMemberType]
templates.env.filters["fmt_uptime"] = _fmt_uptime  # pyright: ignore[reportUnknownMemberType]
templates.env.globals["asset_url"] = asset_url  # pyright: ignore[reportUnknownMemberType]
templates.env.globals["app_version"] = app_version  # pyright: ignore[reportUnknownMemberType]
