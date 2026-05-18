"""Image upload service — pure filesystem logic, no FastAPI imports.

Responsibilities:
  - Magic-byte validation (stdlib only — no python-magic dependency)
  - UUID filename generation and directory creation under <project.path>/.claude/uploads/
  - Best-effort idempotent file deletion
  - Stale-file sweep with injectable clock for deterministic testing

Constants defined here are the SINGLE source of truth for all image-upload
configuration consumed by routes/ui.py and app.py.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import Iterable
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants — single source of truth (design §8)
# ---------------------------------------------------------------------------

MAX_IMAGE_BYTES: int = 10 * 1024 * 1024  # 10 MiB hard cap
UPLOAD_TTL_SECONDS: int = 60  # deferred-delete window after send_keys
STALE_SWEEP_SECONDS: float = 600  # startup sweep age (10 minutes)
UPLOAD_SUBDIR: tuple[str, str] = (".claude", "uploads")  # under project.path
IMAGE_PATH_TEMPLATE: str = "{path}"  # THE single on-device-swappable knob
#                                       flip to "@{path}" if Claude needs @ prefix

# Magic-byte allowlist: (prefix_bytes, mime_type, file_extension)
# WEBP is special-cased: RIFF at [0:4] + WEBP at [8:12]
_MAGIC: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
    # WebP handled separately — RIFF container requires two-range check
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ImageValidationError(Exception):
    """Raised when magic-byte validation fails or the file is otherwise invalid."""


# ---------------------------------------------------------------------------
# sniff_extension — magic-byte only; Content-Type ignored
# ---------------------------------------------------------------------------


def sniff_extension(data: bytes) -> str:
    """Return the validated file extension (.png/.jpg/.webp/.gif).

    Inspects the raw bytes only — the client-supplied Content-Type is never
    consulted (design ADR-7).

    Args:
        data: raw file bytes (must be non-empty and long enough for a magic prefix).

    Returns:
        One of ``.png``, ``.jpg``, ``.webp``, ``.gif``.

    Raises:
        ImageValidationError: if the bytes do not start with a known image magic
            sequence, or if data is empty.
    """
    if not data:
        raise ImageValidationError("Empty file body — cannot determine image type.")

    # WebP: RIFF at [0:4] AND WEBP at [8:12]
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"

    # All other formats: simple prefix match
    for magic_prefix, _mime, ext in _MAGIC:
        if data[: len(magic_prefix)] == magic_prefix:
            return ext

    raise ImageValidationError(
        f"Unsupported image format — magic bytes do not match any allowed type "
        f"(PNG, JPEG, WebP, GIF). Got: {data[:16]!r}"
    )


# ---------------------------------------------------------------------------
# write_image — creates upload dir + writes file with UUID name
# ---------------------------------------------------------------------------


def write_image(project_path: str, data: bytes, ext: str) -> Path:
    """Write image bytes to <project_path>/.claude/uploads/<uuid4hex><ext>.

    Creates the uploads directory (mode 0700) if it does not exist.
    The client-supplied filename is NEVER used — the name is always a
    randomly-generated UUID hex string (design §2, ADR-7).

    Args:
        project_path: absolute path of the project root (``Project.path``).
        data: validated image bytes.
        ext: server-derived extension, e.g. ``.png`` (from ``sniff_extension``).

    Returns:
        Absolute Path to the written file.
    """
    upload_dir = Path(project_path).joinpath(*UPLOAD_SUBDIR)
    upload_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(upload_dir, 0o700)

    filename = f"{uuid.uuid4().hex}{ext}"
    dest = upload_dir / filename
    dest.write_bytes(data)
    return dest


# ---------------------------------------------------------------------------
# unlink_best_effort — idempotent, swallows FileNotFoundError / OSError
# ---------------------------------------------------------------------------


def unlink_best_effort(path: Path) -> None:
    """Delete *path*, silently ignoring FileNotFoundError and OSError.

    Safe to call multiple times on the same path (idempotent). Used both for
    the deferred TTL cleanup (``loop.call_later``) and for rollback after a
    failed ``send_keys``.
    """
    with contextlib.suppress(FileNotFoundError, OSError):
        path.unlink()


# ---------------------------------------------------------------------------
# resolve_staged_ref — containment-secure ref→Path mapping (design ADR-4)
# ---------------------------------------------------------------------------


def resolve_staged_ref(project_path: str, ref: str) -> Path | None:
    """Resolve an opaque attachment ref to an absolute Path inside the uploads dir.

    The ref is the UUID basename returned by the stage endpoint (e.g. ``abc123.png``).
    This function is the SINGLE authority that maps ref → path. The client never
    holds or sends server paths; it only holds opaque refs.

    Security invariant: the resolved *real* path must be inside
    ``<project_path>/.claude/uploads/`` after ``Path.resolve()`` (which collapses
    ``..`` and follows symlinks). Any attempt to escape via traversal, symlink, absolute
    path, or foreign-instance ref returns ``None`` — never raises.

    Args:
        project_path: absolute path of the project root (``Project.path``).
        ref: opaque attachment ref, expected to be a UUID basename (e.g. ``abc.png``).

    Returns:
        Resolved absolute ``Path`` if the ref is valid and the file exists inside
        the uploads dir; ``None`` otherwise.
    """
    if not ref or "/" in ref or "\\" in ref or ref in (".", ".."):
        return None  # cheap reject of obvious traversal shapes
    uploads = Path(project_path).joinpath(*UPLOAD_SUBDIR).resolve()
    candidate = (uploads / ref).resolve()  # collapses .. and follows symlinks
    if not candidate.is_relative_to(uploads):  # containment by RESOLUTION (py3.9+)
        return None
    if not candidate.is_file():
        return None
    return candidate


# ---------------------------------------------------------------------------
# sweep_stale_uploads — startup sweep with injectable clock
# ---------------------------------------------------------------------------


def sweep_stale_uploads(
    project_paths: Iterable[str],
    *,
    now: float,
    max_age: float = STALE_SWEEP_SECONDS,
) -> int:
    """Delete upload files whose mtime is older than *max_age* seconds.

    Designed for deterministic testing: ``now`` and ``max_age`` are always
    injected — no calls to ``time.time()`` or ``time.sleep`` inside this
    function.

    Args:
        project_paths: iterable of project root paths (``Project.path`` strings).
        now: current epoch time (inject for tests, use ``time.time()`` in prod).
        max_age: age threshold in seconds (default: STALE_SWEEP_SECONDS = 600).

    Returns:
        Number of files successfully removed.
    """
    removed = 0
    for project_path in project_paths:
        upload_dir = Path(project_path).joinpath(*UPLOAD_SUBDIR)
        if not upload_dir.is_dir():
            continue
        for entry in upload_dir.iterdir():
            if not entry.is_file():
                continue
            try:
                mtime = entry.stat().st_mtime
                if now - mtime > max_age:
                    unlink_best_effort(entry)
                    # Count as removed only if the unlink succeeded (file gone)
                    if not entry.exists():
                        removed += 1
            except Exception:  # noqa: BLE001
                # Per-file errors must not propagate (design §4, spec startup sweep)
                pass
    return removed
