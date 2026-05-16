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
    # Scrollback is restored so the terminal can scroll Claude's history.
    # (The duplicate-frame problem was caused by un-hardened fit resize
    # storms, now fixed — no need to drop scrollback.)
    assert "-S" in args and "-" in args
    assert "-p" in args
    assert "-e" in args
    assert out == "visible screen"


def test_resize_window_clears_history_to_drop_duplicate_banner() -> None:
    """Each resize makes Claude reprint its banner into scrollback; with
    -S - that stacked duplicates. resize_window must clear-history after
    resize-window so old (duplicate) scrollback is dropped — scroll is kept
    for output produced AFTER the resize."""
    adapter = LibTmuxAdapter()
    session = MagicMock()
    server = MagicMock()
    server.sessions.get.return_value = session
    adapter._get_server = lambda: server  # type: ignore[method-assign]

    adapter.resize_window("s", 60, 30)

    cmds = [c.args[0] for c in session.cmd.call_args_list]
    assert "resize-window" in cmds
    assert "clear-history" in cmds
    # clear-history must come AFTER resize-window (drop the post-resize dupes).
    assert cmds.index("clear-history") > cmds.index("resize-window")
