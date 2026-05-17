"""Metrics screen — GET /metrics (page) + GET /metrics/poll (5s fragment).

Roadmap #3. Host (psutil) + per active session + app aggregates. Never 500s
on a metrics hiccup (the service layer degrades gracefully).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.events import EventsRepository
from claude_remote.db.instances import InstancesRepository
from claude_remote.db.push_subscriptions import PushSubscriptionsRepository
from claude_remote.routes._templates import templates as TEMPLATES
from claude_remote.routes.home import build_home_cards
from claude_remote.routes.instances import get_events_repo, get_instances_repo
from claude_remote.routes.projects import get_projects_repo
from claude_remote.services import metrics as metrics_svc

router = APIRouter(tags=["metrics"])

_TERMINAL = ("stopped", "crashed")


def get_push_repo_for_metrics(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> PushSubscriptionsRepository:
    return PushSubscriptionsRepository(
        connection_factory=lambda: get_connection_for(settings.db_path)
    )


def _build_ctx(
    projects_repo: Any,
    instances_repo: Any,
    events_repo: Any,
    push_repo: Any,
    settings: Settings,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    cards = build_home_cards(projects_repo, instances_repo, events_repo, now)
    host = metrics_svc.collect_host(settings.projects_root)
    app = metrics_svc.collect_app(cards, events_repo, push_repo, now=now)  # pyright: ignore[reportArgumentType]

    sessions: list[dict[str, Any]] = []
    for card in cards:
        ivs = card["instance_views"]
        prim = next((iv for iv in ivs if iv["live_status"] not in _TERMINAL), None)
        if prim is None:
            continue
        inst = prim["instance"]
        proj: Any = card["project"]  # ProjectCardContext types this 'object'
        sess = metrics_svc.collect_session(inst.pane_pid)
        sessions.append(
            {
                "domain": proj.domain,
                "name": proj.name,
                "project_id": proj.id,
                "status": prim["live_status"],
                **sess,
            }
        )
    sessions.sort(key=lambda s: (s["domain"], s["name"]))
    return {"host": host, "sessions": sessions, "app": app}


@router.get("/metrics", response_class=HTMLResponse)
async def metrics_page(
    request: Request,
    projects_repo: Any = Depends(get_projects_repo),  # noqa: B008
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
    events_repo: EventsRepository = Depends(get_events_repo),  # noqa: B008
    push_repo: PushSubscriptionsRepository = Depends(get_push_repo_for_metrics),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> HTMLResponse:
    """Full metrics page."""
    ctx = _build_ctx(projects_repo, instances_repo, events_repo, push_repo, settings)
    return TEMPLATES.TemplateResponse(  # type: ignore[return-value]
        request, "metrics.html", ctx
    )


@router.get("/metrics/poll", response_class=HTMLResponse)
async def metrics_poll(
    request: Request,
    projects_repo: Any = Depends(get_projects_repo),  # noqa: B008
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
    events_repo: EventsRepository = Depends(get_events_repo),  # noqa: B008
    push_repo: PushSubscriptionsRepository = Depends(get_push_repo_for_metrics),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> HTMLResponse:
    """Just the metrics body — polled every 5s, swapped innerHTML."""
    ctx = _build_ctx(projects_repo, instances_repo, events_repo, push_repo, settings)
    content: str = TEMPLATES.get_template("partials/metrics_body.html").render(  # type: ignore[attr-defined]
        request=request, **ctx
    )
    return HTMLResponse(content=content, status_code=200)
