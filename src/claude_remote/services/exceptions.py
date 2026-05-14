"""Domain exceptions for the claude-remote service layer.

All exceptions in this module are raised by services (TmuxLauncher, etc.)
and caught by route handlers that translate them to HTTP error responses.
Centralising them here prevents circular imports: both tmux_adapter.py and
routes/instances.py can import from this single leaf module.
"""


class ProjectNotFoundError(Exception):
    """Raised when a project_id does not exist in the DB."""

    def __init__(self, project_id: str) -> None:
        super().__init__(f"Project '{project_id}' not found")
        self.project_id = project_id


class InstanceNotFoundError(Exception):
    """Raised when an instance_id does not exist in the DB."""

    def __init__(self, instance_id: str) -> None:
        super().__init__(f"Instance '{instance_id}' not found")
        self.instance_id = instance_id


class InstanceAlreadyRunningError(Exception):
    """Raised when a project already has an active (starting/running) instance."""

    def __init__(self, instance_id: str, status: str) -> None:
        super().__init__(
            f"Project already has an active instance '{instance_id}' (status={status})"
        )
        self.instance_id = instance_id
        self.status = status


class EmptyCommandError(Exception):
    """Raised when the launch command is empty or blank after stripping."""

    def __init__(self) -> None:
        super().__init__("Command must be a non-empty string after stripping whitespace")


class TmuxOperationError(Exception):
    """Raised when a tmux operation fails in the adapter layer."""

    def __init__(self, operation: str, cause: Exception) -> None:
        super().__init__(f"tmux operation '{operation}' failed: {cause}")
        self.operation = operation
        self.cause = cause
