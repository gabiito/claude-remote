"""Filesystem service for project directory creation.

Provides `create_project_directory` — identifier-validated mkdir with optional
non-fatal `git init`.

Safety guarantees:
  - Identifiers validated against IDENTIFIER_RE before any filesystem write.
  - Composed path verified to be relative to projects_root after resolve().
  - git init failure is logged and never propagated.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Strict identifier regex: lowercase alphanumeric + hyphens/underscores.
# Must START with alphanumeric character (prevents '-foo', '_bar', '..').
IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Generous timeout for git init — first-time run completes in <100ms on healthy boxes.
GIT_INIT_TIMEOUT_S = 10


class InvalidIdentifierError(Exception):
    """Raised when domain or name fails IDENTIFIER_RE validation or safety check."""

    def __init__(self, field: str, value: str) -> None:
        super().__init__(f"Invalid {field}: {value!r}")
        self.field = field
        self.value = value


class DirectoryAlreadyExistsError(Exception):
    """Raised when the target directory already exists on disk."""

    def __init__(self, path: Path) -> None:
        super().__init__(f"Directory already exists: {path}")
        self.path = path


def create_project_directory(
    projects_root: Path,
    domain: str,
    name: str,
    *,
    git_init: bool = False,
) -> Path:
    """Create <projects_root>/<domain>/<name>/ with optional git init.

    Validates both `domain` and `name` against IDENTIFIER_RE before any
    filesystem write. Raises InvalidIdentifierError immediately on validation
    failure (no side effects).

    Args:
        projects_root: Root directory under which domain/name will be created.
        domain: Level-1 directory name (e.g. "gabiito"). Must match IDENTIFIER_RE.
        name: Level-2 directory name (e.g. "new-project"). Must match IDENTIFIER_RE.
        git_init: If True, run `git init .` inside the created directory.
            Failure (non-zero exit, missing binary, timeout) is logged and NOT raised.

    Returns:
        Resolved absolute Path of the created directory.

    Raises:
        InvalidIdentifierError: domain or name fails validation, or resolved path
            lies outside projects_root (belt-and-braces safety check).
        DirectoryAlreadyExistsError: target directory already exists.
    """
    # Validate identifiers before any filesystem operation
    if not IDENTIFIER_RE.match(domain):
        raise InvalidIdentifierError("domain", domain)
    if not IDENTIFIER_RE.match(name):
        raise InvalidIdentifierError("name", name)

    # Compose + resolve target path
    target = (projects_root / domain / name).resolve()

    # Safety net: verify target is under projects_root after resolution
    root_resolved = projects_root.resolve()
    if not target.is_relative_to(root_resolved):
        raise InvalidIdentifierError("path", "outside projects_root")

    # Create directory — parents=True for domain dir, exist_ok=False to detect conflicts
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise DirectoryAlreadyExistsError(target) from exc

    # Optional git init — always non-fatal
    if git_init:
        try:
            result = subprocess.run(
                ["git", "init", "."],
                cwd=target,
                check=False,
                capture_output=True,
                timeout=GIT_INIT_TIMEOUT_S,
            )
            if result.returncode != 0:
                logger.warning(
                    "git init failed at %s (rc=%d): %s",
                    target,
                    result.returncode,
                    result.stderr.decode(errors="replace"),
                )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            # git binary missing or subprocess hung — log and continue
            logger.warning("git init unavailable for %s: %s", target, exc)

    return target
