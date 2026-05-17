"""Single source of truth for the running version.

A git tag is authoritative, so both the web header and the ``claudio`` CLI
resolve through this one function — they can never drift apart.

`git describe --tags --always --dirty`:
  - tagged commit       → the tag (e.g. v0.1.3)
  - commits after a tag → v0.1.3-3-gabc1234
  - uncommitted changes → ...-dirty
  - no tags yet         → short SHA (honest: untagged commit)

Falls back to packaged metadata, then "dev". Cached for the process
(restart to pick up a new tag) and never raises.
"""

from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def resolve_version() -> str:
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
