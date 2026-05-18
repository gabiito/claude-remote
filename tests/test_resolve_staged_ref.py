"""Unit tests for resolve_staged_ref — containment security (B-1 RED).

All tests must FAIL until resolve_staged_ref is implemented (B-2 GREEN).
"""

from __future__ import annotations

from pathlib import Path

import pytest

# resolve_staged_ref does not exist yet — import will fail until B-2 GREEN.
# We import lazily in each test via importlib to get a clear FAIL vs. ImportError.

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _get_resolve() -> object:
    """Import resolve_staged_ref — fails until B-2 GREEN."""
    from claude_remote.services.image_upload import resolve_staged_ref  # noqa: PLC0415
    return resolve_staged_ref


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    """A temporary project root with the uploads subdir created."""
    uploads = tmp_path / ".claude" / "uploads"
    uploads.mkdir(parents=True)
    return tmp_path


@pytest.fixture()
def staged_file(project_dir: Path) -> Path:
    """A real UUID-named PNG file staged in the uploads dir."""
    import uuid
    name = f"{uuid.uuid4().hex}.png"
    p = project_dir / ".claude" / "uploads" / name
    p.write_bytes(PNG_MAGIC)
    return p


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_resolve_valid_uuid_returns_path(project_dir: Path, staged_file: Path) -> None:
    """Valid UUID file in uploads dir → returns Path pointing to the file."""
    resolve = _get_resolve()
    result = resolve(str(project_dir), staged_file.name)  # type: ignore[operator]
    assert result is not None, "Expected a Path, got None"
    assert isinstance(result, Path)
    assert result == staged_file.resolve()


# ---------------------------------------------------------------------------
# Traversal rejections
# ---------------------------------------------------------------------------


def test_resolve_traversal_dotdot_rejected(project_dir: Path, staged_file: Path) -> None:
    """ref '../../../../etc/passwd' → None (path traversal rejected)."""
    resolve = _get_resolve()
    result = resolve(str(project_dir), "../../../../etc/passwd")  # type: ignore[operator]
    assert result is None


def test_resolve_traversal_dotdot_simple_rejected(project_dir: Path, staged_file: Path) -> None:
    """ref '../sibling.png' → None."""
    resolve = _get_resolve()
    result = resolve(str(project_dir), "../sibling.png")  # type: ignore[operator]
    assert result is None


def test_resolve_absolute_path_rejected(project_dir: Path) -> None:
    """ref '/etc/shadow' (absolute path) → None."""
    resolve = _get_resolve()
    result = resolve(str(project_dir), "/etc/shadow")  # type: ignore[operator]
    assert result is None


def test_resolve_slash_in_ref_rejected(project_dir: Path, staged_file: Path) -> None:
    """ref 'foo/bar.png' (contains slash) → None."""
    resolve = _get_resolve()
    result = resolve(str(project_dir), "foo/bar.png")  # type: ignore[operator]
    assert result is None


def test_resolve_backslash_in_ref_rejected(project_dir: Path) -> None:
    r"""ref 'foo\\bar.png' (contains backslash) → None."""
    resolve = _get_resolve()
    result = resolve(str(project_dir), "foo\\bar.png")  # type: ignore[operator]
    assert result is None


def test_resolve_dot_rejected(project_dir: Path) -> None:
    """ref '.' → None."""
    resolve = _get_resolve()
    result = resolve(str(project_dir), ".")  # type: ignore[operator]
    assert result is None


def test_resolve_dotdot_alone_rejected(project_dir: Path) -> None:
    """ref '..' → None."""
    resolve = _get_resolve()
    result = resolve(str(project_dir), "..")  # type: ignore[operator]
    assert result is None


# ---------------------------------------------------------------------------
# Symlink escape
# ---------------------------------------------------------------------------


def test_resolve_symlink_escape_rejected(project_dir: Path, tmp_path: Path) -> None:
    """Symlink inside uploads dir pointing outside → None (realpath escapes containment)."""
    # Create a file outside the project dir
    outside = tmp_path / "outside_secret.txt"
    outside.write_text("secret")

    # Create a symlink inside uploads pointing to the outside file
    link = project_dir / ".claude" / "uploads" / "escape_link.png"
    link.symlink_to(outside)

    resolve = _get_resolve()
    result = resolve(str(project_dir), "escape_link.png")  # type: ignore[operator]
    assert result is None, (
        "Symlink escaping uploads dir must be rejected by realpath containment check"
    )


# ---------------------------------------------------------------------------
# Foreign instance
# ---------------------------------------------------------------------------


def test_resolve_foreign_instance_rejected(tmp_path: Path) -> None:
    """UUID file exists under instance B's uploads; ref sent to instance A → None."""
    import uuid

    # Instance A's project
    proj_a = tmp_path / "inst_a"
    (proj_a / ".claude" / "uploads").mkdir(parents=True)

    # Instance B's project — file staged here
    proj_b = tmp_path / "inst_b"
    uploads_b = proj_b / ".claude" / "uploads"
    uploads_b.mkdir(parents=True)
    ref_name = f"{uuid.uuid4().hex}.png"
    (uploads_b / ref_name).write_bytes(PNG_MAGIC)

    resolve = _get_resolve()
    # Same ref sent to instance A — file is NOT inside A's uploads
    result = resolve(str(proj_a), ref_name)  # type: ignore[operator]
    assert result is None, (
        "ref valid under instance B must resolve to None when checked against instance A"
    )


# ---------------------------------------------------------------------------
# Nonexistent file
# ---------------------------------------------------------------------------


def test_resolve_nonexistent_ref_returns_none(project_dir: Path) -> None:
    """Valid UUID format but file doesn't exist → None."""
    import uuid

    ref = f"{uuid.uuid4().hex}.png"
    resolve = _get_resolve()
    result = resolve(str(project_dir), ref)  # type: ignore[operator]
    assert result is None


# ---------------------------------------------------------------------------
# Empty ref
# ---------------------------------------------------------------------------


def test_resolve_empty_ref_rejected(project_dir: Path) -> None:
    """Empty string ref → None."""
    resolve = _get_resolve()
    result = resolve(str(project_dir), "")  # type: ignore[operator]
    assert result is None


# ---------------------------------------------------------------------------
# Python >= 3.9 is_relative_to check
# ---------------------------------------------------------------------------


def test_path_is_relative_to_available() -> None:
    """Verify Path.is_relative_to is available (Python >= 3.9)."""
    p = Path("/a/b/c")
    # is_relative_to must exist (pyproject says >= 3.12 — safe)
    assert hasattr(p, "is_relative_to"), "Path.is_relative_to not available (needs Python >= 3.9)"
    assert p.is_relative_to("/a/b")
    assert not p.is_relative_to("/a/x")
