"""TmuxAdapter Protocol, FakeTmuxAdapter, and LibTmuxAdapter.

Design §4.4 — single file for cohesion: any Protocol change forces both
implementations to update in the same edit. The fake is import-time free
(no libtmux usage), so production code paying for its presence is zero-cost.

Import isolation (REQ-14/S14.1): ``import libtmux`` appears only inside
LibTmuxAdapter method bodies. FakeTmuxAdapter does NOT import libtmux at all.
Importing this module in a test environment without libtmux installed is safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from claude_remote.services.exceptions import TmuxOperationError

if TYPE_CHECKING:
    pass  # libtmux imported lazily inside LibTmuxAdapter methods


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class TmuxAdapter(Protocol):
    """Structural interface for tmux session management.

    Method contracts:
      create_session  — returns pane_pid (int | None); raises TmuxOperationError
                        only when session creation itself fails.
      kill_session    — returns True if killed, False if not found; never raises.
      session_exists  — returns bool; returns False on any error; never raises.
      get_pane_pid    — returns int | None; returns None on any error; never raises.

    CONTRACT DIVERGENCE — capture_pane and send_keys (added in mvp-project-view):
      The four methods above follow a "never-raises on missing session" contract
      driven by idempotency (kill missing = success, exists missing = False).
      capture_pane and send_keys RAISE TmuxOperationError when the target
      session does not exist.  Routes need to DISTINGUISH "session gone" from
      "session present, output empty" to produce different HTTP responses.
      See ADR #651 (decisions/tmux-adapter-raise-on-missing-session).
    """

    def create_session(
        self,
        name: str,
        cwd: Path,
        command: str,
        *,
        cols: int | None = None,
        rows: int | None = None,
    ) -> int | None:
        """Create a tmux session running command in cwd.

        Args:
            cols/rows: optional initial window size. Sizing the window at
                creation means the pane program (Claude) renders at that
                width from the start — no SIGWINCH reprint / duplicate banner.
                None → tmux default (80x24).

        Returns:
            The pane PID (int) if captured; None if the session was created
            but the PID could not be read.

        Raises:
            TmuxOperationError: when session creation fails.
        """
        ...

    def kill_session(self, name: str) -> bool:
        """Kill a tmux session by name.

        Returns:
            True if the session existed and was killed; False if not found.
        """
        ...

    def session_exists(self, name: str) -> bool:
        """Check whether a named tmux session is alive."""
        ...

    def get_pane_pid(self, name: str) -> int | None:
        """Return the PID of the session's active pane, or None if unavailable."""
        ...

    def capture_pane(self, session_name: str) -> str:
        """Return the full pane scrollback for the named session.

        Returns:
            A single string with pane content (lines joined with newlines).

        Raises:
            TmuxOperationError: if the session does not exist. See CONTRACT
            DIVERGENCE note on TmuxAdapter for reasoning (ADR #651).
        """
        ...

    def send_keys(self, session_name: str, text: str, *, send_enter: bool = True) -> None:
        """Deliver text to the active pane of the named session.

        Args:
            session_name: target tmux session.
            text: text to deliver verbatim.
            send_enter: when True (default), appends an Enter keystroke after
                text. When False, sends text only (no newline appended).

        MVP limitation: ``literal=True`` is used in LibTmuxAdapter, so special
        key codes like ``C-c`` or ``ESC`` are NOT interpreted — they are sent
        as literal strings. This is intentional for safety.

        Raises:
            TmuxOperationError: if the session does not exist. See CONTRACT
            DIVERGENCE note on TmuxAdapter for reasoning (ADR #651).
        """
        ...

    def resize_window(self, session_name: str, cols: int, rows: int) -> None:
        """Resize the named session's window to ``cols`` x ``rows``.

        Makes the program in the pane (Claude) re-render at that width so the
        captured output fits the viewing device ("fit to screen").

        Raises:
            TmuxOperationError: if the session does not exist.
        """
        ...


# ---------------------------------------------------------------------------
# FakeTmuxAdapter — in-memory test double
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    cwd: Path
    command: str
    pane_pid: int


class FakeTmuxAdapter:
    """In-memory TmuxAdapter for tests. Records every API call.

    Test helpers:
        kill_session_externally(name): simulate an external crash (session
            disappears from the internal dict without going through
            kill_session()).  Does NOT record a call — use this to test
            reconciliation drift scenarios.
        set_pane_content(name, content): set the string returned by
            capture_pane for the named session (REQ-T3).

    Attributes:
        calls: list of (method_name, kwargs_dict) tuples recording every
            call made to the API methods (create_session, kill_session,
            session_exists, get_pane_pid).  kill_session_externally is
            intentionally excluded.
        sent_keys: ordered list of (session_name, text, send_enter) tuples
            recording every send_keys call (REQ-T3).
    """

    def __init__(self, starting_pid: int = 1000) -> None:
        self._sessions: dict[str, _FakeSession] = {}
        self._next_pid = starting_pid
        self.calls: list[tuple[str, dict[str, object]]] = []
        # capture_pane / send_keys state (mvp-project-view, REQ-T1..T3)
        self._pane_contents: dict[str, str] = {}
        self.sent_keys: list[tuple[str, str, bool]] = []
        self.resizes: list[tuple[str, int, int]] = []

    def create_session(
        self,
        name: str,
        cwd: Path,
        command: str,
        *,
        cols: int | None = None,
        rows: int | None = None,
    ) -> int | None:
        self.calls.append(
            (
                "create_session",
                {"name": name, "cwd": cwd, "command": command, "cols": cols, "rows": rows},
            )
        )
        if name in self._sessions:
            raise TmuxOperationError(
                "create_session", RuntimeError(f"Session '{name}' already exists")
            )
        pid = self._next_pid
        self._next_pid += 1
        self._sessions[name] = _FakeSession(cwd=cwd, command=command, pane_pid=pid)
        return pid

    def kill_session(self, name: str) -> bool:
        self.calls.append(("kill_session", {"name": name}))
        return self._sessions.pop(name, None) is not None

    def session_exists(self, name: str) -> bool:
        self.calls.append(("session_exists", {"name": name}))
        return name in self._sessions

    def get_pane_pid(self, name: str) -> int | None:
        self.calls.append(("get_pane_pid", {"name": name}))
        sess = self._sessions.get(name)
        return sess.pane_pid if sess else None

    def kill_session_externally(self, name: str) -> None:
        """Simulate external session loss (e.g. user ran `tmux kill-session`).

        Removes the session from the internal store WITHOUT going through
        kill_session(), so it does NOT appear in .calls.  Use this in tests
        to set up reconciliation drift scenarios.
        """
        self._sessions.pop(name, None)

    # ------------------------------------------------------------------
    # capture_pane / send_keys (mvp-project-view, REQ-T1..T3)
    #
    # CONTRACT DIVERGENCE: both raise TmuxOperationError on missing session
    # (unlike kill_session/session_exists/get_pane_pid which never raise).
    # Routes need to distinguish "session gone" from "session present but empty".
    # See ADR #651 (decisions/tmux-adapter-raise-on-missing-session).
    # ------------------------------------------------------------------

    def set_pane_content(self, session_name: str, content: str) -> None:
        """Test helper: set the string returned by capture_pane for a session.

        Overwrites any previously stored content.  The session MUST exist
        (created via create_session) before calling this helper.
        """
        self._pane_contents[session_name] = content

    def capture_pane(self, session_name: str) -> str:
        """Return stored pane content for session_name.

        Returns:
            The string set by set_pane_content, or "" if no content was set.

        Raises:
            TmuxOperationError: if session_name is not in the active sessions.
        """
        if session_name not in self._sessions:
            raise TmuxOperationError(
                "capture_pane",
                RuntimeError(f"session not found: {session_name}"),
            )
        return self._pane_contents.get(session_name, "")

    def send_keys(self, session_name: str, text: str, *, send_enter: bool = True) -> None:
        """Record a send_keys call and append text to the session's pane content.

        The text (with optional newline) is appended to _pane_contents so that
        tests can call capture_pane() immediately and observe the sent text.

        Raises:
            TmuxOperationError: if session_name is not in the active sessions.
        """
        if session_name not in self._sessions:
            raise TmuxOperationError(
                "send_keys",
                RuntimeError(f"session not found: {session_name}"),
            )
        self.sent_keys.append((session_name, text, send_enter))
        suffix = "\n" if send_enter else ""
        self._pane_contents[session_name] = (
            self._pane_contents.get(session_name, "") + text + suffix
        )

    def resize_window(self, session_name: str, cols: int, rows: int) -> None:
        """Record a resize_window call.

        Raises:
            TmuxOperationError: if session_name is not in the active sessions.
        """
        if session_name not in self._sessions:
            raise TmuxOperationError(
                "resize_window",
                RuntimeError(f"session not found: {session_name}"),
            )
        self.resizes.append((session_name, cols, rows))


# ---------------------------------------------------------------------------
# LibTmuxAdapter — production adapter wrapping libtmux
# ---------------------------------------------------------------------------


class LibTmuxAdapter:
    """Production TmuxAdapter using libtmux.

    ``import libtmux`` is deferred to method bodies (REQ-14/S14.1 import
    isolation).  Importing this module in a test environment without libtmux
    installed does NOT raise ImportError.
    """

    def __init__(self) -> None:
        # Lazy import: libtmux not needed until methods are called.
        # The server instance is created on first use in each method to ensure
        # it reflects the current tmux server state.
        self._server = None

    def _get_server(self) -> object:
        """Return (or create) the libtmux.Server instance."""
        import libtmux  # local import — REQ-14 isolation

        if self._server is None:
            self._server = libtmux.Server()
        return self._server

    def create_session(
        self,
        name: str,
        cwd: Path,
        command: str,
        *,
        cols: int | None = None,
        rows: int | None = None,
    ) -> int | None:
        """Create a tmux session and return the inner pane PID.

        Args:
            name: unique session name (globally unique across this tmux server).
            cwd: working directory for the session's initial window.
            command: shell command to run in the new window.
            cols/rows: optional initial window size (tmux new-session -x/-y).
                Born at this size so Claude renders correctly from the first
                paint — no SIGWINCH reprint / duplicate banner. None → 80x24.

        Returns:
            pane_pid as int; None when PID capture fails (session still created).

        Raises:
            TmuxOperationError: if libtmux raises during session creation.
        """
        import libtmux.exc  # local import — REQ-14 isolation

        server = self._get_server()
        new_kwargs: dict[str, object] = {
            "session_name": name,
            "start_directory": str(cwd),
            "window_command": command,
            "detach": True,
        }
        if cols is not None and rows is not None:
            new_kwargs["x"] = cols
            new_kwargs["y"] = rows
        try:
            session = server.new_session(**new_kwargs)  # type: ignore[union-attr]
            if cols is not None and rows is not None:
                # Keep the size on a detached (clientless) session.
                session.set_option("window-size", "manual")  # pyright: ignore[reportUnknownMemberType]
        except libtmux.exc.LibTmuxException as exc:
            raise TmuxOperationError("create_session", exc) from exc
        return self._read_pane_pid(session)  # pyright: ignore[reportUnknownArgumentType]

    def kill_session(self, name: str) -> bool:
        """Kill the named tmux session.

        Returns:
            True if killed; False if the session did not exist.
        """
        import libtmux.exc  # local import — REQ-14 isolation

        server = self._get_server()
        try:
            server.kill_session(name)  # type: ignore[union-attr]
            return True
        except libtmux.exc.LibTmuxException:
            return False

    def session_exists(self, name: str) -> bool:
        """Return True if the named session is alive on this tmux server."""
        import libtmux.exc  # local import — REQ-14 isolation

        server = self._get_server()
        try:
            return bool(server.has_session(name))  # type: ignore[union-attr]
        except libtmux.exc.LibTmuxException:
            return False

    def get_pane_pid(self, name: str) -> int | None:
        """Return the PID of the active pane for the named session, or None."""
        server = self._get_server()
        try:
            session = server.sessions.get(session_name=name)  # type: ignore[union-attr]
            if session is None:
                return None
            return self._read_pane_pid(session)  # pyright: ignore[reportUnknownArgumentType]
        except Exception:
            # Catches LibTmuxException, ObjectDoesNotExist (not a LibTmuxException
            # subclass in libtmux 0.40), KeyError, AttributeError, and any other
            # library-internal exception variant.  Contract: never raises.
            return None

    def capture_pane(self, session_name: str) -> str:
        """Return the pane scrollback for the named session.

        Calls ``tmux capture-pane -S - -p -e`` — full history so the viewer
        can scroll back through Claude's output. (An earlier visible-only
        variant fixed duplicate frames but killed scrolling; the duplication
        was really caused by un-hardened fit resize storms, now fixed.)

        Returns:
            Single string with all pane content.

        Raises:
            TmuxOperationError: if the session does not exist (ADR #651 —
            CONTRACT DIVERGENCE from kill_session / session_exists which
            never raise on missing sessions).
        """
        server = self._get_server()
        session = server.sessions.get(  # type: ignore[union-attr]
            default=None, session_name=session_name
        )
        if session is None:
            raise TmuxOperationError(
                "capture_pane",
                RuntimeError(f"session not found: {session_name}"),
            )
        pane = session.active_pane  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        # -S - full scrollback (scrollable history), -p print, -e keep ANSI.
        # libtmux 0.40 lacks an `e=True` kwarg so call the raw tmux command
        # (ADR-V1 — libtmux kwarg fallback).
        result = pane.cmd("capture-pane", "-S", "-", "-p", "-e")  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        raw = result.stdout  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        output: str = raw if isinstance(raw, str) else "\n".join(raw or [])
        return output

    def send_keys(self, session_name: str, text: str, *, send_enter: bool = True) -> None:
        """Deliver text to the active pane of the named session.

        Args:
            session_name: target tmux session.
            text: text to deliver verbatim.
            send_enter: when True (default), appends an Enter keystroke.

        MVP limitation: ``literal=True`` is used so special key codes like
        ``C-c`` or ``ESC`` are NOT interpreted — they are sent as literal
        strings.  This is intentional for safety.

        Raises:
            TmuxOperationError: if the session does not exist (ADR #651 —
            CONTRACT DIVERGENCE from kill_session / session_exists which
            never raise on missing sessions).
        """
        server = self._get_server()
        session = server.sessions.get(  # type: ignore[union-attr]
            default=None, session_name=session_name
        )
        if session is None:
            raise TmuxOperationError(
                "send_keys",
                RuntimeError(f"session not found: {session_name}"),
            )
        pane = session.active_pane  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
        pane.send_keys(text, enter=send_enter, literal=True)  # pyright: ignore[reportUnknownMemberType]

    def resize_window(self, session_name: str, cols: int, rows: int) -> None:
        """Resize the named session's window so the pane program re-renders at
        ``cols`` x ``rows``. ``window-size`` is forced to ``manual`` so an
        unattached session honors ``resize-window`` (tmux >= 2.9).

        Raises:
            TmuxOperationError: if the session does not exist or tmux errors.
        """
        import libtmux.exc  # local import — REQ-14 isolation

        server = self._get_server()
        session = server.sessions.get(  # type: ignore[union-attr]
            default=None, session_name=session_name
        )
        if session is None:
            raise TmuxOperationError(
                "resize_window",
                RuntimeError(f"session not found: {session_name}"),
            )
        try:
            session.set_option("window-size", "manual")  # pyright: ignore[reportUnknownMemberType]
            session.cmd(  # pyright: ignore[reportUnknownMemberType]
                "resize-window", "-x", str(cols), "-y", str(rows)
            )
            # NOTE: deliberately NO clear-history. It wiped the scrollback the
            # user needs to scroll. The duplicate banner from SIGWINCH reprints
            # is the accepted lesser evil (cosmetic, top of buffer). Scraping
            # tmux can't give both; true dedupe needs a transcript view.
        except libtmux.exc.LibTmuxException as exc:
            raise TmuxOperationError("resize_window", exc) from exc

    @staticmethod
    def _read_pane_pid(session: object) -> int | None:
        """Extract pane PID from a libtmux Session object.

        libtmux 0.40+ exposes ``pane.pane_pid`` as a string from the tmux
        format string ``#{pane_pid}``.  Falls back to
        ``pane.cmd("display-message", "-p", "#{pane_pid}")`` if the attribute
        is absent.
        """
        try:
            pane = session.active_pane  # type: ignore[union-attr]
            if pane is None:
                return None
            pid_str = getattr(pane, "pane_pid", None)  # pyright: ignore[reportUnknownArgumentType]
            if not pid_str:
                result = pane.cmd("display-message", "-p", "#{pane_pid}")  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
                pid_str = result.stdout[0] if result.stdout else None  # pyright: ignore[reportUnknownMemberType,reportUnknownVariableType]
            if pid_str is None:
                return None
            return int(pid_str)  # pyright: ignore[reportUnknownArgumentType]
        except (AttributeError, ValueError, IndexError):
            return None
