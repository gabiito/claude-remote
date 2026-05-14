"""Path validation for project registration.

Enforces strict 2-level hierarchy:
    <CLAUDE_REMOTE_PROJECTS_ROOT>/<domain>/<project>/

Rules (in order — checked before subsequent ones):
  1. Path exists on disk          → path_does_not_exist
  2. Path is a directory          → path_not_a_directory
  3. Path is inside projects_root → path_outside_projects_root
  4. Depth under root == 2        → path_wrong_depth

Domain is derived from resolved_path.parent.name — NOT from the request body.

Symlink note: Path.resolve() follows symlinks. If projects_root itself is a
symlink target, relative_to() may fail because the resolved abs path lives
outside the symlinked root. Mitigation: always pass an already-resolved
projects_root (Settings.projects_root is resolved on construction).
"""

from dataclasses import dataclass
from pathlib import Path

# Error code constants
PATH_DOES_NOT_EXIST = "path_does_not_exist"
PATH_NOT_A_DIRECTORY = "path_not_a_directory"
PATH_OUTSIDE_PROJECTS_ROOT = "path_outside_projects_root"
PATH_WRONG_DEPTH = "path_wrong_depth"


class PathValidationError(Exception):
    """Raised when a project path fails validation.

    Attributes:
        code: machine-readable error code (one of the PATH_* constants above)
        message: human-readable description
        details: optional dict with additional context (e.g. {"depth": 3})
    """

    def __init__(self, code: str, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class ValidatedProject:
    """Result of a successful path validation."""

    absolute_path: Path
    domain: str


def validate_project_path(raw_path: str, projects_root: Path) -> ValidatedProject:
    """Validate that raw_path is a 2-level directory under projects_root.

    Args:
        raw_path: the path string from the client request
        projects_root: already-resolved absolute root (from Settings.projects_root)

    Returns:
        ValidatedProject with absolute_path and derived domain

    Raises:
        PathValidationError with .code describing the failure
    """
    abs_path = Path(raw_path).expanduser().resolve()

    if not abs_path.exists():
        raise PathValidationError(
            PATH_DOES_NOT_EXIST,
            f"Path does not exist: {abs_path}",
        )

    if not abs_path.is_dir():
        raise PathValidationError(
            PATH_NOT_A_DIRECTORY,
            f"Path is not a directory: {abs_path}",
        )

    try:
        rel = abs_path.relative_to(projects_root)
    except ValueError:
        raise PathValidationError(
            PATH_OUTSIDE_PROJECTS_ROOT,
            f"Path is outside projects root ({projects_root}): {abs_path}",
        ) from None

    parts = rel.parts  # e.g. ("sandbox", "claude-remote") for a valid 2-level path
    if len(parts) != 2:  # noqa: PLR2004
        raise PathValidationError(
            PATH_WRONG_DEPTH,
            f"Path must be exactly 2 levels under projects root, got {len(parts)}",
            details={"depth": len(parts)},
        )

    domain = parts[0]
    return ValidatedProject(absolute_path=abs_path, domain=domain)
