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
    """

    def create_session(self, name: str, cwd: Path, command: str) -> int | None:
        """Create a tmux session running command in cwd.

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

    Attributes:
        calls: list of (method_name, kwargs_dict) tuples recording every
            call made to the API methods (create_session, kill_session,
            session_exists, get_pane_pid).  kill_session_externally is
            intentionally excluded.
    """

    def __init__(self, starting_pid: int = 1000) -> None:
        self._sessions: dict[str, _FakeSession] = {}
        self._next_pid = starting_pid
        self.calls: list[tuple[str, dict[str, object]]] = []

    def create_session(self, name: str, cwd: Path, command: str) -> int | None:
        self.calls.append(("create_session", {"name": name, "cwd": cwd, "command": command}))
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

    def create_session(self, name: str, cwd: Path, command: str) -> int | None:
        """Create a tmux session and return the inner pane PID.

        Args:
            name: unique session name (globally unique across this tmux server).
            cwd: working directory for the session's initial window.
            command: shell command to run in the new window.

        Returns:
            pane_pid as int; None when PID capture fails (session still created).

        Raises:
            TmuxOperationError: if libtmux raises during session creation.
        """
        import libtmux.exc  # local import — REQ-14 isolation

        server = self._get_server()
        try:
            session = server.new_session(  # type: ignore[union-attr]
                session_name=name,
                start_directory=str(cwd),
                window_command=command,
                detach=True,
            )
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
        import libtmux.exc  # local import — REQ-14 isolation

        server = self._get_server()
        try:
            session = server.sessions.get(session_name=name)  # type: ignore[union-attr]
            if session is None:
                return None
            return self._read_pane_pid(session)  # pyright: ignore[reportUnknownArgumentType]
        except (libtmux.exc.LibTmuxException, KeyError, AttributeError):
            return None

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
