"""Tests for validate_project_path.

WU-3 — RED tests (must fail until services/path_validation.py is implemented).
One test per error code + one happy path. Uses tmp_path to create real dir structures.
"""

from pathlib import Path

import pytest

from claude_remote.services.path_validation import (
    PathValidationError,
    ValidatedProject,
    validate_project_path,
)


@pytest.fixture()
def projects_root(tmp_path: Path) -> Path:
    root = tmp_path / "projects-root"
    root.mkdir()
    return root


class TestHappyPath:
    def test_valid_two_level_path_returns_validated_project(
        self, projects_root: Path
    ) -> None:
        """A valid 2-level path returns ValidatedProject with absolute_path and domain."""
        project_dir = projects_root / "sandbox" / "my-project"
        project_dir.mkdir(parents=True)
        result = validate_project_path(str(project_dir), projects_root)
        assert isinstance(result, ValidatedProject)
        assert result.absolute_path == project_dir.resolve()
        assert result.domain == "sandbox"

    def test_domain_is_parent_directory_name(self, projects_root: Path) -> None:
        """domain is always the immediate parent directory under projects_root."""
        project_dir = projects_root / "work" / "acme-api"
        project_dir.mkdir(parents=True)
        result = validate_project_path(str(project_dir), projects_root)
        assert result.domain == "work"


class TestPathDoesNotExist:
    def test_nonexistent_path_raises_path_does_not_exist(
        self, projects_root: Path
    ) -> None:
        """A path that does not exist raises PathValidationError with code path_does_not_exist."""
        nonexistent = str(projects_root / "sandbox" / "ghost-project")
        with pytest.raises(PathValidationError) as exc_info:
            validate_project_path(nonexistent, projects_root)
        assert exc_info.value.code == "path_does_not_exist"


class TestPathNotADirectory:
    def test_file_path_raises_path_not_a_directory(
        self, projects_root: Path
    ) -> None:
        """A path that is a file (not a dir) raises PathValidationError with path_not_a_directory."""
        domain_dir = projects_root / "sandbox"
        domain_dir.mkdir()
        file_path = domain_dir / "a-file.txt"
        file_path.write_text("I am a file, not a directory.")
        with pytest.raises(PathValidationError) as exc_info:
            validate_project_path(str(file_path), projects_root)
        assert exc_info.value.code == "path_not_a_directory"


class TestPathOutsideProjectsRoot:
    def test_path_outside_root_raises_path_outside_projects_root(
        self, tmp_path: Path, projects_root: Path
    ) -> None:
        """A valid dir that is outside projects_root raises path_outside_projects_root."""
        outside_dir = tmp_path / "outside" / "some-project"
        outside_dir.mkdir(parents=True)
        with pytest.raises(PathValidationError) as exc_info:
            validate_project_path(str(outside_dir), projects_root)
        assert exc_info.value.code == "path_outside_projects_root"


class TestPathWrongDepth:
    def test_one_level_deep_raises_path_wrong_depth(
        self, projects_root: Path
    ) -> None:
        """A directory directly under root (1 level) raises path_wrong_depth."""
        one_level = projects_root / "sandbox"
        one_level.mkdir()
        with pytest.raises(PathValidationError) as exc_info:
            validate_project_path(str(one_level), projects_root)
        assert exc_info.value.code == "path_wrong_depth"

    def test_three_levels_deep_raises_path_wrong_depth(
        self, projects_root: Path
    ) -> None:
        """A directory 3 levels deep raises path_wrong_depth."""
        three_levels = projects_root / "sandbox" / "my-project" / "subdir"
        three_levels.mkdir(parents=True)
        with pytest.raises(PathValidationError) as exc_info:
            validate_project_path(str(three_levels), projects_root)
        assert exc_info.value.code == "path_wrong_depth"

    def test_projects_root_itself_raises_path_wrong_depth(
        self, projects_root: Path
    ) -> None:
        """The root itself (0 levels) raises path_wrong_depth."""
        with pytest.raises(PathValidationError) as exc_info:
            validate_project_path(str(projects_root), projects_root)
        assert exc_info.value.code == "path_wrong_depth"


class TestErrorAttributes:
    def test_error_has_code_attribute(self, projects_root: Path) -> None:
        """PathValidationError exposes .code and .message."""
        try:
            validate_project_path("/nonexistent/path/here", projects_root)
        except PathValidationError as e:
            assert hasattr(e, "code")
            assert hasattr(e, "message")
            assert e.code == "path_does_not_exist"
