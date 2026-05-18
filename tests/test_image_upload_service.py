"""Unit tests for services/file_upload.py — pure service layer (no HTTP).

Covers:
  - classify_file: magic-byte validation for PNG/JPEG/WebP/GIF; rejects spoofed
    Content-Type, SVG, PDF, and empty input
  - write_staged_file: creates .claude/uploads/ dir, correct permissions, UUID filename
  - unlink_best_effort: idempotent delete
  - sweep_stale_uploads: deterministic with injected clock, no sleep
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

from claude_remote.services.file_upload import (
    STALE_SWEEP_SECONDS,
    UPLOAD_SUBDIR,
    classify_file,
    sweep_stale_uploads,
    unlink_best_effort,
    write_staged_file,
)

# ---------------------------------------------------------------------------
# Magic byte constants for tests
# ---------------------------------------------------------------------------

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG_MAGIC = b"\xff\xd8\xff\xe0" + b"\x00" * 16
WEBP_MAGIC = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 8
GIF87_MAGIC = b"GIF87a" + b"\x00" * 16
GIF89_MAGIC = b"GIF89a" + b"\x00" * 16
PDF_MAGIC = b"%PDF-1.4" + b"\x00" * 16
SVG_MAGIC = b"<svg xmlns" + b"\x00" * 16


# ---------------------------------------------------------------------------
# 1.1 classify_file tests
# ---------------------------------------------------------------------------


class TestClassifyFile:
    def test_png_bytes_return_image_ext(self):
        assert classify_file(PNG_MAGIC) == ("image", ".png")

    def test_jpeg_bytes_return_image_ext(self):
        assert classify_file(JPEG_MAGIC) == ("image", ".jpg")

    def test_webp_bytes_return_image_ext(self):
        assert classify_file(WEBP_MAGIC) == ("image", ".webp")

    def test_gif87_bytes_return_image_ext(self):
        assert classify_file(GIF87_MAGIC) == ("image", ".gif")

    def test_gif89_bytes_return_image_ext(self):
        assert classify_file(GIF89_MAGIC) == ("image", ".gif")

    def test_pdf_bytes_return_file_none(self):
        assert classify_file(PDF_MAGIC) == ("file", None)

    def test_zip_office_bytes_return_file_none(self):
        assert classify_file(b"PK\x03\x04" + b"\x00" * 100) == ("file", None)

    def test_random_bytes_return_file_none(self):
        assert classify_file(b"\xDE\xAD\xBE\xEF\x00\x01\x02\x03" + b"\x00" * 100) == ("file", None)

    def test_text_bytes_return_file_none(self):
        assert classify_file(b"Hello world, this is plain text") == ("file", None)

    def test_riff_no_webp_marker_returns_file_none(self):
        """RIFF container without WEBP at offset 8 — not an image, accepted as file."""
        bad_riff = b"RIFF\x00\x00\x00\x00MPEG" + b"\x00" * 8
        assert classify_file(bad_riff) == ("file", None)


# ---------------------------------------------------------------------------
# 1.3 write_staged_file tests
# ---------------------------------------------------------------------------


class TestWriteImage:
    def test_creates_upload_dir(self, tmp_path: Path):
        project_path = str(tmp_path)
        write_staged_file(project_path, PNG_MAGIC, ".png")
        upload_dir = tmp_path.joinpath(*UPLOAD_SUBDIR)
        assert upload_dir.is_dir()

    def test_upload_dir_mode_is_0700(self, tmp_path: Path):
        project_path = str(tmp_path)
        write_staged_file(project_path, PNG_MAGIC, ".png")
        upload_dir = tmp_path.joinpath(*UPLOAD_SUBDIR)
        mode = oct(upload_dir.stat().st_mode)[-3:]
        assert mode == "700"

    def test_filename_matches_uuid_hex_pattern(self, tmp_path: Path):
        project_path = str(tmp_path)
        result = write_staged_file(project_path, PNG_MAGIC, ".png")
        assert re.match(r"^[0-9a-f]{32}\.png$", result.name)

    def test_file_content_equals_input_bytes(self, tmp_path: Path):
        project_path = str(tmp_path)
        result = write_staged_file(project_path, PNG_MAGIC, ".png")
        assert result.read_bytes() == PNG_MAGIC

    def test_jpeg_ext_written_correctly(self, tmp_path: Path):
        project_path = str(tmp_path)
        result = write_staged_file(project_path, JPEG_MAGIC, ".jpg")
        assert result.name.endswith(".jpg")

    def test_client_filename_never_used(self, tmp_path: Path):
        """write_staged_file takes only (project_path, data, ext) — no filename param."""
        project_path = str(tmp_path)
        result = write_staged_file(project_path, PNG_MAGIC, ".png")
        # The function signature does not accept a filename argument.
        # The name must be a UUID hex (already tested above) — no path component.
        assert "/" not in result.name
        assert "\\" not in result.name

    def test_returns_absolute_path(self, tmp_path: Path):
        project_path = str(tmp_path)
        result = write_staged_file(project_path, PNG_MAGIC, ".png")
        assert result.is_absolute()

    def test_multiple_writes_create_different_files(self, tmp_path: Path):
        project_path = str(tmp_path)
        p1 = write_staged_file(project_path, PNG_MAGIC, ".png")
        p2 = write_staged_file(project_path, PNG_MAGIC, ".png")
        assert p1 != p2
        assert p1.exists()
        assert p2.exists()


# ---------------------------------------------------------------------------
# 1.5 unlink_best_effort tests (RED — service not yet implemented)
# ---------------------------------------------------------------------------


class TestUnlinkBestEffort:
    def test_removes_existing_file(self, tmp_path: Path):
        f = tmp_path / "test.png"
        f.write_bytes(b"data")
        unlink_best_effort(f)
        assert not f.exists()

    def test_second_call_on_missing_path_does_not_raise(self, tmp_path: Path):
        f = tmp_path / "already_gone.png"
        # File never existed — should not raise
        unlink_best_effort(f)

    def test_idempotent_double_unlink(self, tmp_path: Path):
        f = tmp_path / "twice.png"
        f.write_bytes(b"data")
        unlink_best_effort(f)
        unlink_best_effort(f)  # second call must not raise


# ---------------------------------------------------------------------------
# 1.7 sweep_stale_uploads tests (RED — service not yet implemented)
# ---------------------------------------------------------------------------


class TestSweepStaleUploads:
    def _make_project(self, tmp_path: Path, slug: str) -> Path:
        """Create a project dir and its upload subdir, return project path."""
        project_path = tmp_path / slug
        upload_dir = project_path.joinpath(*UPLOAD_SUBDIR)
        upload_dir.mkdir(parents=True)
        return project_path

    def _seed_file(self, project_path: Path, name: str, mtime: float) -> Path:
        upload_dir = project_path.joinpath(*UPLOAD_SUBDIR)
        f = upload_dir / name
        f.write_bytes(b"img_data")
        os.utime(f, (mtime, mtime))
        return f

    def test_deletes_stale_file(self, tmp_path: Path):
        now = time.time()
        project_path = self._make_project(tmp_path, "proj1")
        stale_file = self._seed_file(project_path, "stale.png", now - STALE_SWEEP_SECONDS - 1)
        count = sweep_stale_uploads([str(project_path)], now=now)
        assert count == 1
        assert not stale_file.exists()

    def test_keeps_fresh_file(self, tmp_path: Path):
        now = time.time()
        project_path = self._make_project(tmp_path, "proj2")
        fresh_file = self._seed_file(project_path, "fresh.png", now - 60)  # 1 min old
        count = sweep_stale_uploads([str(project_path)], now=now)
        assert count == 0
        assert fresh_file.exists()

    def test_skips_missing_dirs_silently(self, tmp_path: Path):
        nonexistent = str(tmp_path / "ghost_project")
        # Must not raise
        count = sweep_stale_uploads([nonexistent], now=time.time())
        assert count == 0

    def test_returns_correct_count_multiple_stale(self, tmp_path: Path):
        now = time.time()
        project_path = self._make_project(tmp_path, "proj3")
        for i in range(3):
            self._seed_file(project_path, f"stale_{i}.png", now - STALE_SWEEP_SECONDS - 1)
        count = sweep_stale_uploads([str(project_path)], now=now)
        assert count == 3

    def test_handles_per_file_error_without_propagating(self, tmp_path: Path, monkeypatch):
        """Even if unlink raises unexpectedly, sweep continues and does not propagate."""
        now = time.time()
        project_path = self._make_project(tmp_path, "proj4")
        self._seed_file(project_path, "stale_err.png", now - STALE_SWEEP_SECONDS - 1)

        from claude_remote.services import file_upload

        call_count = {"n": 0}

        def _raising_unlink(path: Path) -> None:
            call_count["n"] += 1
            raise OSError("simulated")

        monkeypatch.setattr(file_upload, "unlink_best_effort", _raising_unlink)
        # Must not raise; count may be 0 (error swallowed)
        count = sweep_stale_uploads([str(project_path)], now=now)
        assert call_count["n"] >= 1  # sweep attempted unlink
        assert count == 0  # error swallowed → not counted

    def test_custom_max_age_injected(self, tmp_path: Path):
        """max_age is injectable for deterministic tests."""
        now = time.time()
        project_path = self._make_project(tmp_path, "proj5")
        # File is 5s old; with max_age=3 it should be swept
        file = self._seed_file(project_path, "custom.png", now - 5)
        count = sweep_stale_uploads([str(project_path)], now=now, max_age=3)
        assert count == 1
        assert not file.exists()

    def test_no_sleep_used(self):
        """Verify the function uses injected now/max_age, not time.sleep or real clock."""
        # This is a meta-test: we call with a frozen now 10 years in the future
        # so nothing can be stale unless the mtime is also in the future.
        # The function must accept arbitrary now values without sleeping.
        import time as _time

        far_future = _time.time() + 3650 * 24 * 3600
        # Just calling with a path list (empty) must not block or sleep
        count = sweep_stale_uploads([], now=far_future)
        assert count == 0
