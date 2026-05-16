"""LibTmuxAdapter.capture_pane must capture the VISIBLE screen, not the full
scrollback. Claude is a full-screen TUI: -S - concatenated every past repaint
frame, so resizing (fit) showed duplicated content. Unit-test the args via a
mocked libtmux chain (no real tmux needed).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from claude_remote.services.tmux_adapter import LibTmuxAdapter


def test_capture_pane_does_not_read_full_scrollback() -> None:
    adapter = LibTmuxAdapter()
    pane = MagicMock()
    pane.cmd.return_value = MagicMock(stdout="visible screen")
    session = MagicMock()
    session.active_pane = pane
    server = MagicMock()
    server.sessions.get.return_value = session
    adapter._get_server = lambda: server  # type: ignore[method-assign]

    out = adapter.capture_pane("s")

    args = pane.cmd.call_args.args
    assert args[0] == "capture-pane"
    # No full-scrollback flag — that duplicated the TUI on every repaint.
    assert "-S" not in args
    # Still print + keep ANSI escapes.
    assert "-p" in args
    assert "-e" in args
    assert out == "visible screen"
