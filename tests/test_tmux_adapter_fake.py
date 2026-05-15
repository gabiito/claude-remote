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


# ---------------------------------------------------------------------------
# capture_pane — REQ-T1
# ---------------------------------------------------------------------------


def test_fake_capture_pane_returns_stored_content(
    adapter: FakeTmuxAdapter, tmp_path: Path
) -> None:
    """capture_pane returns the string set by set_pane_content (REQ-T1 happy path)."""
    adapter.create_session("my-session", tmp_path, "bash")
    adapter.set_pane_content("my-session", "hello from pane")
    result = adapter.capture_pane("my-session")
    assert result == "hello from pane"


def test_fake_capture_pane_raises_on_missing_session(adapter: FakeTmuxAdapter) -> None:
    """capture_pane raises TmuxOperationError for a session never created (REQ-T1 not-found).

    NOTE: CONTRACT DIVERGENCE from the existing 4 methods (kill_session, session_exists,
    get_pane_pid, create_session) — those never raise on missing session. capture_pane and
    send_keys RAISE because routes need to distinguish 'session gone' from 'session present
    but empty' to serve different HTTP responses. See ADR #651.
    """
    with pytest.raises(TmuxOperationError) as exc_info:
        adapter.capture_pane("gone-session")
    assert exc_info.value.operation == "capture_pane"


def test_fake_capture_pane_empty_by_default_for_known_session(
    adapter: FakeTmuxAdapter, tmp_path: Path
) -> None:
    """capture_pane returns empty string for a session with no pane content set."""
    adapter.create_session("my-session", tmp_path, "bash")
    result = adapter.capture_pane("my-session")
    assert result == ""


# ---------------------------------------------------------------------------
# send_keys — REQ-T2
# ---------------------------------------------------------------------------


def test_fake_send_keys_records_call_and_appends_to_content(
    adapter: FakeTmuxAdapter, tmp_path: Path
) -> None:
    """send_keys(send_enter=True) appends to pane content and records the call (REQ-T2)."""
    adapter.create_session("my-session", tmp_path, "bash")
    adapter.send_keys("my-session", "hello", send_enter=True)
    assert ("my-session", "hello", True) in adapter.sent_keys
    content = adapter.capture_pane("my-session")
    assert "hello\n" in content


def test_fake_send_keys_raises_on_missing_session(adapter: FakeTmuxAdapter) -> None:
    """send_keys raises TmuxOperationError for a session that does not exist (REQ-T2 not-found).

    Same contract divergence as capture_pane — see ADR #651.
    """
    with pytest.raises(TmuxOperationError) as exc_info:
        adapter.send_keys("gone", "text")
    assert exc_info.value.operation == "send_keys"


def test_fake_send_keys_no_enter_when_send_enter_false(
    adapter: FakeTmuxAdapter, tmp_path: Path
) -> None:
    """send_keys with send_enter=False records the call with False and does not append newline."""
    adapter.create_session("my-session", tmp_path, "bash")
    adapter.send_keys("my-session", "/clear", send_enter=False)
    last = adapter.sent_keys[-1]
    assert last == ("my-session", "/clear", False)
    content = adapter.capture_pane("my-session")
    # Text appended without newline
    assert "/clear" in content
    assert "/clear\n" not in content


def test_fake_set_pane_content_overwrites(
    adapter: FakeTmuxAdapter, tmp_path: Path
) -> None:
    """set_pane_content overwrites any previously stored content (REQ-T3 set-get)."""
    adapter.create_session("s", tmp_path, "bash")
    adapter.set_pane_content("s", "first")
    adapter.set_pane_content("s", "output text")
    assert adapter.capture_pane("s") == "output text"


# ---------------------------------------------------------------------------
# sent_keys accumulation — REQ-T3
# ---------------------------------------------------------------------------


def test_fake_sent_keys_starts_empty(adapter: FakeTmuxAdapter) -> None:
    """sent_keys is empty on a fresh FakeTmuxAdapter."""
    assert adapter.sent_keys == []


def test_fake_sent_keys_accumulates_multiple_calls(
    adapter: FakeTmuxAdapter, tmp_path: Path
) -> None:
    """Two send_keys calls result in len(fake.sent_keys) == 2 (REQ-T3 accumulate)."""
    adapter.create_session("s", tmp_path, "bash")
    adapter.send_keys("s", "a")
    adapter.send_keys("s", "b")
    assert len(adapter.sent_keys) == 2
