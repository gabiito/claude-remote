"""TmuxLauncher — service orchestrating tmux session lifecycle.

Responsibilities:
  - validate input and check for active instances before launching
  - reconcile drifted instances (DB says running, tmux session gone)
  - delegate actual tmux operations to the TmuxAdapter seam
  - delegate DB persistence to InstancesRepository + ProjectsRepository
  - raise domain exceptions; routes translate them to HTTP responses

This class is fully sync. Routes wrap calls in ``asyncio.to_thread`` to
avoid blocking the event loop on tmux IPC (design §4.4, ADR-4).

Concurrency note (MVP): ``list_active_for_project`` + ``INSERT`` is not
atomic. Two simultaneous ``POST /launch`` requests could both pass the
active-instance check and insert two 'starting' rows. SQLite serialises
writes, so the INSERTs won't corrupt data, but the 409 guard will not fire.
This is acceptable for single-user MVP; revisit with ``BEGIN IMMEDIATE``
if multi-user access is needed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import uuid4

from claude_remote.db.instances import TERMINAL_STATUSES, Instance, InstancesRepository
from claude_remote.db.projects import ProjectsRepository
from claude_remote.services.exceptions import (
    EmptyCommandError,
    InstanceAlreadyRunningError,
    InstanceNotFoundError,
    ProjectNotFoundError,
    TmuxOperationError,
)
from claude_remote.services.tmux_adapter import TmuxAdapter

logger = logging.getLogger(__name__)


class TmuxLauncher:
    """Orchestrate launch, stop, and reconciliation of tmux-backed Claude instances.

    Args:
        adapter: TmuxAdapter implementation (LibTmuxAdapter in production,
            FakeTmuxAdapter in tests).
        instances_repo: InstancesRepository for DB persistence.
        projects_repo: ProjectsRepository for project lookups.
    """

    def __init__(
        self,
        adapter: TmuxAdapter,
        instances_repo: InstancesRepository,
        projects_repo: ProjectsRepository,
    ) -> None:
        self._adapter = adapter
        self._instances = instances_repo
        self._projects = projects_repo

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def launch(self, project_id: str, *, command: str | None = None) -> Instance:
        """Launch a new Claude instance for the given project.

        Steps:
          1. Validate project exists.
          2. Validate command (None → 'claude'; empty/blank → EmptyCommandError).
          3. Reconcile drifted active instances for this project.
          4. Re-check for active instances → 409 if any remain.
          5. Insert 'starting' row.
          6. Call adapter.create_session → update to 'running'.
             On adapter failure: re-raise TmuxOperationError; row stays 'starting'
             (next reconcile flips it to 'crashed').

        Args:
            project_id: id of an existing project row.
            command: shell command to run. Defaults to 'claude' when None.
                Empty or blank string raises EmptyCommandError.

        Returns:
            Updated Instance with status='running'.

        Raises:
            ProjectNotFoundError: project_id not found.
            EmptyCommandError: command is empty or blank after strip().
            InstanceAlreadyRunningError: an active instance exists after
                reconciliation.
            TmuxOperationError: adapter.create_session raised.
        """
        project = self._projects.get(project_id)
        if project is None:
            raise ProjectNotFoundError(project_id)

        # Normalise command: None → default 'claude'; blank → error
        cmd = (command if command is not None else "claude").strip()
        if not cmd:
            raise EmptyCommandError()

        # Reconcile stale active instances before 409 check
        for inst in self._instances.list_active_for_project(project_id):
            self.reconcile(inst)

        # 409 guard: check again after reconciliation
        actives = self._instances.list_active_for_project(project_id)
        if actives:
            raise InstanceAlreadyRunningError(actives[0].id, actives[0].status)

        # Generate session name: claude-remote-{slug}-{8-hex-chars}
        session_name = f"claude-remote-{project.slug}-{uuid4().hex[:8]}"

        instance = self._instances.create(
            project_id=project_id,
            tmux_session_name=session_name,
            status="starting",
        )

        # Delegate to adapter — let TmuxOperationError bubble up.
        # The 'starting' row stays as audit trail; next reconcile → 'crashed'.
        from pathlib import Path

        pane_pid = self._adapter.create_session(session_name, Path(project.path), cmd)

        return self._instances.update_status(
            instance.id,
            status="running",
            pane_pid=pane_pid,
        )

    def stop(self, instance_id: str) -> Instance:
        """Stop a running instance.

        Idempotent: calling stop on an already-stopped or crashed instance
        returns the unchanged instance (no error, locked Q2).

        Args:
            instance_id: id of the instance to stop.

        Returns:
            Updated Instance (status='stopped') or unchanged if already terminal.

        Raises:
            InstanceNotFoundError: instance_id not found.
        """
        instance = self._instances.get(instance_id)
        if instance is None:
            raise InstanceNotFoundError(instance_id)

        if instance.status in TERMINAL_STATUSES:
            return instance  # idempotent — locked Q2

        # kill_session return value is ignored (already-dead is fine)
        self._adapter.kill_session(instance.tmux_session_name)

        return self._instances.mark_stopped(instance_id)

    def reconcile(self, instance: Instance) -> Instance:
        """Reconcile a single instance against the live tmux server state.

        Transitions:
          running/starting, session gone → 'crashed' with stopped_at=now()
          running/starting, session alive → unchanged
          stopped/crashed, session exists → log warning (orphan); unchanged
          stopped/crashed, no session → unchanged

        Args:
            instance: the Instance to reconcile.

        Returns:
            Possibly updated Instance.
        """
        if instance.status in TERMINAL_STATUSES:
            # Inverse drift: check if orphan tmux session exists for a terminal instance
            if self._adapter.session_exists(instance.tmux_session_name):
                logger.warning(
                    "Orphan tmux session detected: %s (instance %s is %s). "
                    "Session cleanup deferred to a future slice.",
                    instance.tmux_session_name,
                    instance.id,
                    instance.status,
                )
            return instance

        # Active instance — check if session is still alive
        if self._adapter.session_exists(instance.tmux_session_name):
            return instance

        # Session gone → flip to crashed
        return self._instances.update_status(
            instance.id,
            status="crashed",
            stopped_at=datetime.now(UTC).isoformat(),
        )

    def reconcile_all(self) -> list[Instance]:
        """Reconcile all instances against live tmux state.

        O(N) tmux queries — acceptable for MVP scale (~10 projects max).
        If the list grows past ~50, batch via 'tmux list-sessions' once and
        intersect in memory.

        Returns:
            List of reconciled Instance objects (same order as list_all).
        """
        return [self.reconcile(inst) for inst in self._instances.list_all()]

    def get_with_reconcile(self, instance_id: str) -> Instance | None:
        """Load an instance, reconcile it, and return it; None if not found.

        Convenience method to keep DB calls out of route handlers.

        Args:
            instance_id: id of the instance to load.

        Returns:
            Reconciled Instance, or None if not found.
        """
        instance = self._instances.get(instance_id)
        if instance is None:
            return None
        return self.reconcile(instance)
