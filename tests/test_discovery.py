"""Red tests for scan_projects_root + ProjectCandidate — WU-1.

All tests use tmp_path fixtures; no DB, no HTTP, no env vars.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Import guard — these must fail until services/discovery.py is created
# ---------------------------------------------------------------------------


def _import_scanner():
    from claude_remote.services.discovery import (  # noqa: PLC0415
        ProjectCandidate,
        scan_projects_root,
    )
    return ProjectCandidate, scan_projects_root


# ---------------------------------------------------------------------------
# ProjectCandidate model
# ---------------------------------------------------------------------------


class TestProjectCandidate:
    def test_candidate_constructed_with_all_fields(self, tmp_path: Path) -> None:
        """ProjectCandidate is constructable with domain, name, absolute_path, suggested_slug."""
        ProjectCandidate, _ = _import_scanner()
        path = tmp_path / "gabiito" / "claude-remote"
        path.mkdir(parents=True)
        cand = ProjectCandidate(
            domain="gabiito",
            name="claude-remote",
            absolute_path=path,
            suggested_slug="claude-remote",
        )
        assert cand.domain == "gabiito"
        assert cand.name == "claude-remote"
        assert cand.absolute_path == path
        assert cand.suggested_slug == "claude-remote"

    def test_suggested_slug_is_derived_from_name(self, tmp_path: Path) -> None:
        """suggested_slug must be the slugified version of name."""
        ProjectCandidate, scan_projects_root = _import_scanner()
        # Create a domain + project dir
        proj = tmp_path / "domain" / "My Cool Project"
        proj.mkdir(parents=True)
        candidates = scan_projects_root(tmp_path)
        assert len(candidates) == 1
        assert candidates[0].suggested_slug == "my-cool-project"


# ---------------------------------------------------------------------------
# scan_projects_root — happy paths
# ---------------------------------------------------------------------------


class TestScanProjectsRoot:
    def test_empty_root_returns_empty_list(self, tmp_path: Path) -> None:
        """Empty projects_root returns []."""
        _, scan_projects_root = _import_scanner()
        result = scan_projects_root(tmp_path)
        assert result == []

    def test_two_domains_three_projects_returns_six_candidates(self, tmp_path: Path) -> None:
        """2 domains × 3 projects = 6 candidates."""
        _, scan_projects_root = _import_scanner()
        for domain in ("alpha", "beta"):
            for proj in ("proj1", "proj2", "proj3"):
                (tmp_path / domain / proj).mkdir(parents=True)
        result = scan_projects_root(tmp_path)
        assert len(result) == 6

    def test_result_sorted_by_domain_then_name(self, tmp_path: Path) -> None:
        """Results are sorted by (domain, name) lexicographically."""
        _, scan_projects_root = _import_scanner()
        # Create out of alphabetical order to ensure sorting works
        (tmp_path / "zzz" / "bbb").mkdir(parents=True)
        (tmp_path / "aaa" / "ccc").mkdir(parents=True)
        (tmp_path / "aaa" / "aaa").mkdir(parents=True)
        result = scan_projects_root(tmp_path)
        domains = [c.domain for c in result]
        names = [c.name for c in result]
        assert domains == ["aaa", "aaa", "zzz"]
        assert names == ["aaa", "ccc", "bbb"]

    def test_candidate_has_correct_domain_and_name(self, tmp_path: Path) -> None:
        """Candidate domain = level-1 dir name, name = level-2 dir name."""
        _, scan_projects_root = _import_scanner()
        (tmp_path / "gabiito" / "my-project").mkdir(parents=True)
        result = scan_projects_root(tmp_path)
        assert len(result) == 1
        assert result[0].domain == "gabiito"
        assert result[0].name == "my-project"

    def test_absolute_path_is_resolved(self, tmp_path: Path) -> None:
        """absolute_path uses resolved absolute path."""
        _, scan_projects_root = _import_scanner()
        proj = tmp_path / "d" / "p"
        proj.mkdir(parents=True)
        result = scan_projects_root(tmp_path)
        assert len(result) == 1
        assert result[0].absolute_path.is_absolute()
        assert result[0].absolute_path == proj.resolve()


# ---------------------------------------------------------------------------
# scan_projects_root — filtering
# ---------------------------------------------------------------------------


class TestScanFiltering:
    def test_nonexistent_root_returns_empty_list(self, tmp_path: Path) -> None:
        """Non-existent root returns [] without raising."""
        _, scan_projects_root = _import_scanner()
        result = scan_projects_root(tmp_path / "does-not-exist")
        assert result == []

    def test_root_that_is_a_file_returns_empty_list(self, tmp_path: Path) -> None:
        """Root that is a file (not a dir) returns [] without raising."""
        _, scan_projects_root = _import_scanner()
        file_root = tmp_path / "a-file.txt"
        file_root.touch()
        result = scan_projects_root(file_root)
        assert result == []

    def test_symlink_at_level1_skipped(self, tmp_path: Path) -> None:
        """Symlinked directories at level 1 (domain level) are skipped."""
        _, scan_projects_root = _import_scanner()
        real_dir = tmp_path / "real_domain"
        real_dir.mkdir()
        (real_dir / "project").mkdir()
        link = tmp_path / "linked_domain"
        link.symlink_to(real_dir)
        result = scan_projects_root(tmp_path)
        domains = [c.domain for c in result]
        # real_domain/project is found; linked_domain must NOT be
        assert "real_domain" in domains
        assert "linked_domain" not in domains

    def test_symlink_at_level2_skipped(self, tmp_path: Path) -> None:
        """Symlinked directories at level 2 (project level) are skipped."""
        _, scan_projects_root = _import_scanner()
        real_proj = tmp_path / "other_root" / "project"
        real_proj.mkdir(parents=True)
        domain = tmp_path / "gabiito"
        domain.mkdir()
        link = domain / "linked-proj"
        link.symlink_to(real_proj)
        result = scan_projects_root(tmp_path)
        names = [c.name for c in result]
        assert "linked-proj" not in names

    def test_hidden_dir_at_level1_skipped(self, tmp_path: Path) -> None:
        """Directories starting with '.' at level 1 are skipped."""
        _, scan_projects_root = _import_scanner()
        (tmp_path / ".git" / "refs").mkdir(parents=True)
        (tmp_path / ".cache" / "proj").mkdir(parents=True)
        (tmp_path / "real" / "proj").mkdir(parents=True)
        result = scan_projects_root(tmp_path)
        domains = [c.domain for c in result]
        assert ".git" not in domains
        assert ".cache" not in domains
        assert "real" in domains

    def test_hidden_dir_at_level2_skipped(self, tmp_path: Path) -> None:
        """Directories starting with '.' at level 2 are skipped."""
        _, scan_projects_root = _import_scanner()
        (tmp_path / "gabiito" / ".hidden-proj").mkdir(parents=True)
        (tmp_path / "gabiito" / "visible-proj").mkdir(parents=True)
        result = scan_projects_root(tmp_path)
        names = [c.name for c in result]
        assert ".hidden-proj" not in names
        assert "visible-proj" in names

    def test_file_at_level1_skipped(self, tmp_path: Path) -> None:
        """Files at level 1 (not directories) are skipped without error."""
        _, scan_projects_root = _import_scanner()
        (tmp_path / "README.md").touch()
        (tmp_path / "real-domain" / "project").mkdir(parents=True)
        result = scan_projects_root(tmp_path)
        domains = [c.domain for c in result]
        assert "README.md" not in domains
        assert "real-domain" in domains

    def test_file_at_level2_skipped(self, tmp_path: Path) -> None:
        """Files at level 2 (not directories) are skipped without error."""
        _, scan_projects_root = _import_scanner()
        (tmp_path / "gabiito").mkdir()
        (tmp_path / "gabiito" / "notes.txt").touch()
        (tmp_path / "gabiito" / "real-project").mkdir()
        result = scan_projects_root(tmp_path)
        names = [c.name for c in result]
        assert "notes.txt" not in names
        assert "real-project" in names

    def test_mixed_valid_and_invalid_at_both_levels(self, tmp_path: Path) -> None:
        """Mix of valid dirs + symlinks + hidden + files returns only valid dirs."""
        _, scan_projects_root = _import_scanner()
        root = tmp_path
        (root / "domain-a" / "proj1").mkdir(parents=True)
        (root / "domain-a" / "proj2").mkdir(parents=True)
        (root / "domain-a" / ".hidden").mkdir(parents=True)
        (root / "domain-a" / "file.txt").touch()
        (root / ".hidden-domain" / "proj").mkdir(parents=True)
        (root / "file.txt").touch()
        real_target = tmp_path / "external"
        real_target.mkdir()
        (root / "linked-domain").symlink_to(real_target)
        result = scan_projects_root(root)
        assert len(result) == 2
        domains = [c.domain for c in result]
        assert all(d == "domain-a" for d in domains)
        names = sorted(c.name for c in result)
        assert names == ["proj1", "proj2"]
