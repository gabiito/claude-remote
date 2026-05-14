"""Red tests for WU-3 — FakeTmuxAdapter lifecycle and call recording.

FakeTmuxAdapter is the test double for TmuxAdapter Protocol.
LibTmuxAdapter is covered by the WU-8 integration suite (requires tmux binary).

Tests verify:
  - create/kill/exists/get_pane_pid lifecycle
  - kill_session_externally (simulates crash without going through kill_session)
  - call recording via .calls list
  - TmuxOperationError raised on duplicate create_session
  - pids increment across sessions
"""

from pathlib import Path

import pytest

from claude_remote.services.exceptions import TmuxOperationError
from claude_remote.services.tmux_adapter import FakeTmuxAdapter


@pytest.fixture()
def adapter() -> FakeTmuxAdapter:
    return FakeTmuxAdapter()


# ---------------------------------------------------------------------------
# create_session
# ---------------------------------------------------------------------------


def test_create_returns_positive_pid(adapter: FakeTmuxAdapter, tmp_path: Path) -> None:
    pid = adapter.create_session("test-session", tmp_path, "claude")
    assert isinstance(pid, int)
    assert pid is not None and pid > 0


def test_create_pids_increment(adapter: FakeTmuxAdapter, tmp_path: Path) -> None:
    pid1 = adapter.create_session("session-a", tmp_path, "claude")
    pid2 = adapter.create_session("session-b", tmp_path, "claude")
    assert pid1 is not None
    assert pid2 is not None
    assert pid1 != pid2


def test_duplicate_create_raises_tmux_operation_error(
    adapter: FakeTmuxAdapter, tmp_path: Path
) -> None:
    adapter.create_session("dup-session", tmp_path, "claude")
    with pytest.raises(TmuxOperationError):
        adapter.create_session("dup-session", tmp_path, "claude")


# ---------------------------------------------------------------------------
# session_exists
# ---------------------------------------------------------------------------


def test_session_exists_false_before_create(adapter: FakeTmuxAdapter) -> None:
    assert adapter.session_exists("nonexistent") is False


def test_session_exists_true_after_create(adapter: FakeTmuxAdapter, tmp_path: Path) -> None:
    adapter.create_session("my-session", tmp_path, "claude")
    assert adapter.session_exists("my-session") is True


def test_session_exists_false_after_kill(adapter: FakeTmuxAdapter, tmp_path: Path) -> None:
    adapter.create_session("kill-me", tmp_path, "claude")
    adapter.kill_session("kill-me")
    assert adapter.session_exists("kill-me") is False


# ---------------------------------------------------------------------------
# kill_session
# ---------------------------------------------------------------------------


def test_kill_session_returns_true_when_existed(
    adapter: FakeTmuxAdapter, tmp_path: Path
) -> None:
    adapter.create_session("to-kill", tmp_path, "claude")
    result = adapter.kill_session("to-kill")
    assert result is True


def test_kill_session_returns_false_when_not_existed(adapter: FakeTmuxAdapter) -> None:
    result = adapter.kill_session("nonexistent")
    assert result is False


# ---------------------------------------------------------------------------
# get_pane_pid
# ---------------------------------------------------------------------------


def test_get_pane_pid_returns_pid_after_create(
    adapter: FakeTmuxAdapter, tmp_path: Path
) -> None:
    pid = adapter.create_session("pid-session", tmp_path, "claude")
    assert adapter.get_pane_pid("pid-session") == pid


def test_get_pane_pid_returns_none_for_unknown(adapter: FakeTmuxAdapter) -> None:
    assert adapter.get_pane_pid("nonexistent") is None


# ---------------------------------------------------------------------------
# kill_session_externally
# ---------------------------------------------------------------------------


def test_kill_session_externally_makes_session_disappear(
    adapter: FakeTmuxAdapter, tmp_path: Path
) -> None:
    """kill_session_externally simulates external crash (no .calls entry)."""
    adapter.create_session("external-kill", tmp_path, "claude")
    adapter.kill_session_externally("external-kill")
    assert adapter.session_exists("external-kill") is False


def test_kill_session_externally_not_recorded_in_calls(
    adapter: FakeTmuxAdapter, tmp_path: Path
) -> None:
    """kill_session_externally must NOT appear in .calls (it bypasses the API)."""
    adapter.create_session("trace-session", tmp_path, "claude")
    initial_call_count = len(adapter.calls)
    adapter.kill_session_externally("trace-session")
    # No new call recorded
    assert len(adapter.calls) == initial_call_count


# ---------------------------------------------------------------------------
# call recording
# ---------------------------------------------------------------------------


def test_calls_recorded_for_create(adapter: FakeTmuxAdapter, tmp_path: Path) -> None:
    adapter.create_session("recorded", tmp_path, "claude")
    assert len(adapter.calls) == 1
    method, kwargs = adapter.calls[0]
    assert method == "create_session"
    assert kwargs["name"] == "recorded"
    assert kwargs["command"] == "claude"
    assert kwargs["cwd"] == tmp_path


def test_calls_recorded_for_kill(adapter: FakeTmuxAdapter, tmp_path: Path) -> None:
    adapter.create_session("to-trace-kill", tmp_path, "bash")
    adapter.kill_session("to-trace-kill")
    methods = [c[0] for c in adapter.calls]
    assert "kill_session" in methods


def test_calls_recorded_for_exists(adapter: FakeTmuxAdapter) -> None:
    adapter.session_exists("check-this")
    assert any(c[0] == "session_exists" for c in adapter.calls)


def test_calls_recorded_for_get_pane_pid(adapter: FakeTmuxAdapter, tmp_path: Path) -> None:
    adapter.create_session("pid-check", tmp_path, "bash")
    adapter.get_pane_pid("pid-check")
    assert any(c[0] == "get_pane_pid" for c in adapter.calls)
