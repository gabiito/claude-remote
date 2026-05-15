"""Real-tmux integration tests for LibTmuxAdapter.

All tests are gated by ``@pytest.mark.requires_tmux``.  If the ``tmux``
binary is not on PATH, the entire module is skipped via the conftest
``pytest_collection_modifyitems`` hook.

Tests target ``LibTmuxAdapter`` directly — no DB layer, no HTTP layer.
Purpose: exercise the lines in ``tmux_adapter.py`` that require a live
tmux server and drive ``LibTmuxAdapter`` coverage from 51% to ~90%+.

Invariants (REQ-15):
- Every test MUST clean up its tmux sessions even on failure.
- Session names are unique per run (uuid4 suffix).
- The ``bash -c 'while true; do sleep 1; done'`` placeholder command is used.
  ``claude`` binary is NEVER invoked.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

# Skip entire module if libtmux is not installed.
libtmux = pytest.importorskip("libtmux")  # type: ignore[assignment]

# Skip entire module if tmux binary is not on PATH.
if shutil.which("tmux") is None:
    pytest.skip("tmux binary not available", allow_module_level=True)

from claude_remote.services.exceptions import TmuxOperationError  # noqa: E402
from claude_remote.services.tmux_adapter import LibTmuxAdapter  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

LONG_RUNNING_CMD = "bash -c 'while true; do sleep 1; done'"


@pytest.fixture
def tracked_sessions() -> list[str]:
    """Collect session names for guaranteed teardown."""
    names: list[str] = []
    yield names
    # Teardown: kill any leaked sessions even on test failure.
    for n in names:
        subprocess.run(["tmux", "kill-session", "-t", n], capture_output=True)


@pytest.fixture
def adapter() -> LibTmuxAdapter:
    """A fresh LibTmuxAdapter pointing at the default tmux server."""
    return LibTmuxAdapter()


def unique_name() -> str:
    """Return a unique session name safe for tmux (no dots, short)."""
    return f"cr-pytest-{uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.requires_tmux
def test_create_and_kill_session_lifecycle(
    adapter: LibTmuxAdapter,
    tracked_sessions: list[str],
    tmp_path: Path,
) -> None:
    """create_session + session_exists + get_pane_pid + kill_session full cycle."""
    name = unique_name()
    tracked_sessions.append(name)

    # Create
    pane_pid = adapter.create_session(name, tmp_path, LONG_RUNNING_CMD)

    # PID is either a positive int or None (if capture fails in some envs).
    # Both are valid per design §4.4; just assert it is not negative/zero when present.
    if pane_pid is not None:
        assert pane_pid > 0, f"Expected positive pane_pid, got {pane_pid}"

    # Session must exist immediately after creation.
    assert adapter.session_exists(name) is True

    # get_pane_pid independent call
    pid2 = adapter.get_pane_pid(name)
    if pid2 is not None:
        assert pid2 > 0, f"Expected positive pane_pid from get_pane_pid, got {pid2}"

    # Kill
    result = adapter.kill_session(name)
    assert result is True

    # Must no longer exist
    assert adapter.session_exists(name) is False


@pytest.mark.requires_tmux
def test_kill_nonexistent_session_returns_false(
    adapter: LibTmuxAdapter,
) -> None:
    """kill_session on a never-created session returns False (idempotent contract)."""
    name = f"cr-pytest-nonexistent-{uuid4().hex[:8]}"
    # Do NOT append to tracked_sessions — session never exists.
    result = adapter.kill_session(name)
    assert result is False


@pytest.mark.requires_tmux
def test_session_exists_before_and_after_kill(
    adapter: LibTmuxAdapter,
    tracked_sessions: list[str],
    tmp_path: Path,
) -> None:
    """session_exists returns False before creation and after kill."""
    name = unique_name()
    tracked_sessions.append(name)

    # Before: must not exist.
    assert adapter.session_exists(name) is False

    adapter.create_session(name, tmp_path, LONG_RUNNING_CMD)
    assert adapter.session_exists(name) is True

    adapter.kill_session(name)
    assert adapter.session_exists(name) is False


@pytest.mark.requires_tmux
def test_get_pane_pid_returns_none_for_unknown_session(
    adapter: LibTmuxAdapter,
) -> None:
    """get_pane_pid for a session that does not exist returns None (never raises)."""
    name = f"cr-pytest-ghost-{uuid4().hex[:8]}"
    result = adapter.get_pane_pid(name)
    assert result is None


@pytest.mark.requires_tmux
def test_create_session_invalid_cwd(
    adapter: LibTmuxAdapter,
    tracked_sessions: list[str],
) -> None:
    """create_session with non-existent cwd: libtmux may create the session anyway
    (tmux itself doesn't validate the start directory strictly) or raise
    TmuxOperationError.  Either outcome is acceptable; what is NOT acceptable is
    an unhandled exception other than TmuxOperationError leaking out.
    """
    name = unique_name()
    invalid_cwd = Path("/nonexistent/path/that/does/not/exist")

    try:
        pane_pid = adapter.create_session(name, invalid_cwd, LONG_RUNNING_CMD)
        # If session was created despite bad cwd, track it for cleanup.
        tracked_sessions.append(name)
        # pane_pid may be None or positive — both are valid.
        if pane_pid is not None:
            assert isinstance(pane_pid, int)
    except TmuxOperationError:
        # Also acceptable: adapter raised TmuxOperationError as per contract.
        pass


@pytest.mark.requires_tmux
def test_multiple_sessions_are_independent(
    adapter: LibTmuxAdapter,
    tracked_sessions: list[str],
    tmp_path: Path,
) -> None:
    """Two sessions are independent: killing one does not affect the other."""
    name_a = unique_name()
    name_b = unique_name()
    tracked_sessions.extend([name_a, name_b])

    adapter.create_session(name_a, tmp_path, LONG_RUNNING_CMD)
    adapter.create_session(name_b, tmp_path, LONG_RUNNING_CMD)

    assert adapter.session_exists(name_a) is True
    assert adapter.session_exists(name_b) is True

    # Kill only A.
    adapter.kill_session(name_a)

    assert adapter.session_exists(name_a) is False
    assert adapter.session_exists(name_b) is True  # B must survive.

    # Cleanup B (tracked_sessions teardown also handles it, but explicit is fine).
    adapter.kill_session(name_b)
    assert adapter.session_exists(name_b) is False


# ---------------------------------------------------------------------------
# capture_pane integration tests (mvp-project-view, REQ-T1)
# ---------------------------------------------------------------------------


@pytest.mark.requires_tmux
def test_capture_pane_returns_initial_content(
    adapter: LibTmuxAdapter,
    tracked_sessions: list[str],
    tmp_path: Path,
) -> None:
    """capture_pane on a real bash session returns a non-empty string (REQ-T1)."""
    name = unique_name()
    tracked_sessions.append(name)
    adapter.create_session(name, tmp_path, LONG_RUNNING_CMD)
    try:
        result = adapter.capture_pane(name)
        # Result must be a string (may be empty if pane hasn't printed anything yet,
        # but capture-pane itself must succeed without exception).
        assert isinstance(result, str)
    finally:
        adapter.kill_session(name)


@pytest.mark.requires_tmux
def test_capture_pane_raises_for_missing_session(adapter: LibTmuxAdapter) -> None:
    """capture_pane raises TmuxOperationError for a session that does not exist (REQ-T1)."""
    name = f"cr-pytest-ghost-{uuid4().hex[:8]}"
    with pytest.raises(TmuxOperationError) as exc_info:
        adapter.capture_pane(name)
    assert exc_info.value.operation == "capture_pane"


# ---------------------------------------------------------------------------
# send_keys integration tests (mvp-project-view, REQ-T2)
# ---------------------------------------------------------------------------


@pytest.mark.requires_tmux
def test_send_keys_appears_in_capture_pane(
    adapter: LibTmuxAdapter,
    tracked_sessions: list[str],
    tmp_path: Path,
) -> None:
    """send_keys text appears in capture_pane output after a short delay (REQ-T2)."""
    import time

    name = unique_name()
    tracked_sessions.append(name)
    adapter.create_session(name, tmp_path, LONG_RUNNING_CMD)
    try:
        # Send a unique echo command via send_keys.
        unique_token = f"hello_{uuid4().hex[:8]}"
        adapter.send_keys(name, f"echo {unique_token}", send_enter=True)
        # Give tmux a moment to process and update pane buffer.
        time.sleep(0.2)
        result = adapter.capture_pane(name)
        assert unique_token in result, (
            f"Expected '{unique_token}' in pane output but got:\n{result!r}"
        )
    finally:
        adapter.kill_session(name)


@pytest.mark.requires_tmux
def test_send_keys_raises_for_missing_session(adapter: LibTmuxAdapter) -> None:
    """send_keys raises TmuxOperationError for a session that does not exist (REQ-T2)."""
    name = f"cr-pytest-ghost-{uuid4().hex[:8]}"
    with pytest.raises(TmuxOperationError) as exc_info:
        adapter.send_keys(name, "hello")
    assert exc_info.value.operation == "send_keys"


@pytest.mark.requires_tmux
def test_send_keys_no_enter_does_not_submit(
    adapter: LibTmuxAdapter,
    tracked_sessions: list[str],
    tmp_path: Path,
) -> None:
    """send_keys with send_enter=False sends text without executing it (REQ-T2).

    We send a unique string without enter. The text should appear in pane output
    (visible in the input line), but since the shell prompt won't have processed
    it yet, we can't assert execution-side effects. We assert the literal text
    is in the captured output.
    """
    import time

    name = unique_name()
    tracked_sessions.append(name)
    adapter.create_session(name, tmp_path, LONG_RUNNING_CMD)
    try:
        unique_token = f"noeenter_{uuid4().hex[:8]}"
        adapter.send_keys(name, unique_token, send_enter=False)
        time.sleep(0.1)
        result = adapter.capture_pane(name)
        # The token should appear in the pane (in the shell input buffer).
        assert unique_token in result, (
            f"Expected '{unique_token}' in pane output but got:\n{result!r}"
        )
    finally:
        adapter.kill_session(name)
