"""Project discovery service — filesystem scanner.

Scans a 2-level directory tree:
  <projects_root>/<domain>/<project>/

Returns a list of ProjectCandidate instances sorted by (domain, name).
Does NOT access the database.

Filtering rules:
  - Files at any level are skipped.
  - Symlinks at level 1 (domain) or level 2 (project) are skipped.
  - Directories whose name starts with '.' are skipped (hidden dirs).
  - Non-existent or non-directory root returns [].
"""

from pathlib import Path

from pydantic import BaseModel

from claude_remote.services.slug import slugify


class ProjectCandidate(BaseModel):
    """Candidate project discovered on disk."""

    domain: str
    name: str
    absolute_path: Path
    suggested_slug: str


def scan_projects_root(projects_root: Path) -> list[ProjectCandidate]:
    """Scan <projects_root>/<domain>/<project>/ and return a list of candidates.

    Args:
        projects_root: Root directory to scan. Must exist and be a real directory
            (not a symlink target check is done via is_dir + not is_symlink).

    Returns:
        Sorted list of ProjectCandidate by (domain, name). Empty list when root
        does not exist, is not a directory, or contains no valid 2-level entries.
    """
    if not projects_root.exists() or not projects_root.is_dir():
        return []

    candidates: list[ProjectCandidate] = []

    for domain_entry in sorted(projects_root.iterdir()):
        # Skip files, symlinks, and hidden directories at level 1
        if domain_entry.is_symlink():
            continue
        if not domain_entry.is_dir():
            continue
        if domain_entry.name.startswith("."):
            continue

        for proj_entry in sorted(domain_entry.iterdir()):
            # Skip files, symlinks, and hidden directories at level 2
            if proj_entry.is_symlink():
                continue
            if not proj_entry.is_dir():
                continue
            if proj_entry.name.startswith("."):
                continue

            candidates.append(
                ProjectCandidate(
                    domain=domain_entry.name,
                    name=proj_entry.name,
                    absolute_path=proj_entry.resolve(),
                    suggested_slug=slugify(proj_entry.name),
                )
            )

    return candidates
