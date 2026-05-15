"""Hook receiver endpoint — POST /hooks/{event_type}.

Hard invariant: this endpoint MUST always return HTTP 200. It NEVER raises
4xx or 5xx to callers. Claude Code's hook system must not be broken if our
backend encounters any error (DB failure, programming bug, etc.).

The outer try/except Exception catches everything and returns a safe 200
response so Claude Code's lifecycle is never interrupted.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.events import EVENT_TYPES, EventsRepository
from claude_remote.db.instances import InstancesRepository
from claude_remote.db.notifications import NotificationsRepository
from claude_remote.db.projects import ProjectsRepository
from claude_remote.services import notifier

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hooks", tags=["hooks"])


# ---------------------------------------------------------------------------
# DI factories
# ---------------------------------------------------------------------------


def get_events_repo(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> EventsRepository:
    """Dependency provider: EventsRepository pointing at settings.db_path."""
    return EventsRepository(
        connection_factory=lambda: get_connection_for(settings.db_path)
    )


def get_instances_repo_for_hooks(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> InstancesRepository:
    """Dependency provider: InstancesRepository for hook token lookup."""
    return InstancesRepository(
        connection_factory=lambda: get_connection_for(settings.db_path)
    )


def get_projects_repo_for_hooks(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ProjectsRepository:
    """Dependency provider: ProjectsRepository for loading project by id."""
    return ProjectsRepository(
        connection_factory=lambda: get_connection_for(settings.db_path)
    )


def get_notifications_repo_for_hooks(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> NotificationsRepository:
    """Dependency provider: NotificationsRepository for notification prefs."""
    return NotificationsRepository(
        connection_factory=lambda: get_connection_for(settings.db_path)
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/{event_type}", status_code=200)
async def receive_hook(
    event_type: str,
    request: Request,
    events_repo: EventsRepository = Depends(get_events_repo),  # noqa: B008
    instances_repo: InstancesRepository = Depends(get_instances_repo_for_hooks),  # noqa: B008
    projects_repo: ProjectsRepository = Depends(get_projects_repo_for_hooks),  # noqa: B008
    notifications_repo: NotificationsRepository = Depends(get_notifications_repo_for_hooks),  # noqa: B008
) -> JSONResponse:
    """Receive a Claude Code hook event.

    Hard invariant: ALWAYS returns HTTP 200. Never raises 4xx/5xx.
    Claude Code's hook flow must not be disrupted by any error on our side.

    Validation order:
      1. event_type in EVENT_TYPES  → 200 received:false reason:unknown_event_type
      2. ?token= query param present → 200 received:false reason:missing_token
      3. instances_repo.get_by_hook_token(token) → 200 received:false reason:unknown_token
      4. Parse JSON body (fallback to raw on decode error)
      5. events_repo.create(...)  → 200 received:true event_id:...

    Any exception in steps 2-5 is caught by the outer try/except and returns:
      200 received:false reason:internal_error
    """
    # Outer guard: catch ALL exceptions — no 5xx ever escapes this handler
    try:
        # Step 1: validate event_type (before token lookup to short-circuit fast)
        if event_type not in EVENT_TYPES:
            return JSONResponse(
                status_code=200,
                content={"received": False, "reason": "unknown_event_type"},
            )

        # Step 2: validate token query param
        token = request.query_params.get("token")
        if not token:
            return JSONResponse(
                status_code=200,
                content={"received": False, "reason": "missing_token"},
            )

        # Step 3: look up instance by token
        instance = instances_repo.get_by_hook_token(token)
        if instance is None:
            logger.warning("Hook received with unknown token (prefix: %s...)", token[:8])
            return JSONResponse(
                status_code=200,
                content={"received": False, "reason": "unknown_token"},
            )

        # Step 4: parse body — on JSON decode error, wrap raw bytes
        try:
            body = await request.json()
        except Exception:
            raw_bytes = await request.body()
            body = {"raw": raw_bytes.decode("utf-8", errors="replace")}

        # Step 5: persist event
        event = events_repo.create(
            instance_id=instance.id,
            project_id=instance.project_id,
            event_type=event_type,
            payload=json.dumps(body),
        )

        # Step 6: dispatch to notifier (fire-and-forget, never-raise)
        # Inner try/except is distinct from the outer one: it catches repo/wiring
        # failures so they never escalate. The outer try/except is the final net.
        try:
            project = projects_repo.get(instance.project_id)
            if project is not None:
                prefs = notifications_repo.get()
                asyncio.create_task(notifier.dispatch(event, project, prefs))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Notifier wiring failed for event %s: %s", event.id, exc)

        return JSONResponse(
            status_code=200,
            content={"received": True, "event_id": event.id},
        )

    except Exception as exc:
        logger.exception(
            "Hook receiver internal error for event_type=%r: %s",
            event_type,
            exc,
        )
        return JSONResponse(
            status_code=200,
            content={"received": False, "reason": "internal_error"},
        )
