"""Shared render-time DTOs used across home, ui, and projects_view routers.

Extracted here so InstanceView is never duplicated (DoD §2).
"""

from __future__ import annotations

from typing import TypedDict

from claude_remote.db.instances import Instance


class InstanceView(TypedDict):
    """Thin render-time DTO pairing an instance with its derived live status."""

    instance: Instance
    live_status: str
