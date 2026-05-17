"""Server-Sent Events — push re-rendered partials instead of HTMX polling.

mvp-sse. ``/sse/home`` and ``/sse/metrics`` replace the 5s poll of the home
list and the metrics body. On connect we render once (initial frame), then
on every (debounced) bus tick we re-render the same partial the poll route
used and push it down the open connection.

Single-process only — see services/event_bus.py. The terminal output pane
is intentionally NOT here: it is continuous tmux capture, not a discrete
event, so it stays on its own poll.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse

from claude_remote.config import Settings, get_settings
from claude_remote.db.events import EventsRepository
from claude_remote.db.instances import InstancesRepository
from claude_remote.routes._templates import templates as TEMPLATES
from claude_remote.routes.home import build_home_cards
from claude_remote.routes.instances import get_events_repo, get_instances_repo
from claude_remote.routes.metrics import build_metrics_ctx, get_push_repo_for_metrics
from claude_remote.routes.projects import get_projects_repo
from claude_remote.services.event_bus import bus
from claude_remote.services.session_grouping import (
    filter_cards_by_domain,
    group_and_sort_cards,
)

router = APIRouter(tags=["sse"])

# Periodic re-render cadence. Both views need a timer, not pure event-
# driven: metrics are time-varying samples (CPU/RAM/uptime) and home's
# live_status uses time windows (active=60s, needs_input=300s) that decay
# with wall-clock even when no hook fires. A bus tick just refreshes home
# *sooner*. A real frame every interval also keeps the connection warm
# through idle proxies (Tailscale) — no separate keepalive needed.
_HOME_INTERVAL = 5.0
_METRICS_INTERVAL = 2.0
# Collapse a burst of hook events (Claude fires several in a row) into one
# re-render.
_DEBOUNCE = 0.25

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Disable proxy/CDN response buffering so frames flush immediately.
    "X-Accel-Buffering": "no",
}


def _frame(html: str) -> str:
    """Encode HTML as one SSE ``data:`` event (every line must be prefixed)."""
    body = "\n".join(f"data: {line}" for line in html.split("\n"))
    return f"{body}\n\n"


async def _event_stream(
    request: Request, render: Callable[[], str], *, interval: float
) -> AsyncGenerator[str]:
    async def render_html() -> str:
        return await asyncio.to_thread(render)

    # Subscribe BEFORE the initial paint so an event firing between the
    # first render and subscription is not lost (real connect-time race).
    async with bus.subscribe() as q:
        yield _frame(await render_html())  # initial paint
        while True:
            if await request.is_disconnected():
                break
            try:
                await asyncio.wait_for(q.get(), timeout=interval)
                # A bus tick → coalesce the burst, then refresh early.
                await asyncio.sleep(_DEBOUNCE)
                with suppress(asyncio.QueueEmpty):
                    while True:
                        q.get_nowait()
            except TimeoutError:
                pass  # periodic refresh (time-varying data)
            if await request.is_disconnected():
                break
            yield _frame(await render_html())


@router.get("/sse/home")
async def sse_home(
    request: Request,
    domain: str = Query("all"),  # noqa: B008
    projects_repo: Any = Depends(get_projects_repo),  # noqa: B008
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
    events_repo: EventsRepository = Depends(get_events_repo),  # noqa: B008
) -> StreamingResponse:
    def render() -> str:
        now = datetime.now(UTC)
        _all = build_home_cards(projects_repo, instances_repo, events_repo, now)
        cards = filter_cards_by_domain(_all, domain)  # pyright: ignore[reportArgumentType]
        return TEMPLATES.get_template("partials/home_list.html").render(  # type: ignore[attr-defined,no-any-return]
            request=request,
            cards=cards,
            grouped=group_and_sort_cards(cards),  # pyright: ignore[reportArgumentType]
        )

    return StreamingResponse(
        _event_stream(request, render, interval=_HOME_INTERVAL),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get("/sse/metrics")
async def sse_metrics(
    request: Request,
    projects_repo: Any = Depends(get_projects_repo),  # noqa: B008
    instances_repo: InstancesRepository = Depends(get_instances_repo),  # noqa: B008
    events_repo: EventsRepository = Depends(get_events_repo),  # noqa: B008
    push_repo: Any = Depends(get_push_repo_for_metrics),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> StreamingResponse:
    def render() -> str:
        ctx = build_metrics_ctx(
            projects_repo, instances_repo, events_repo, push_repo, settings
        )
        return TEMPLATES.get_template("partials/metrics_body.html").render(  # type: ignore[attr-defined,no-any-return]
            request=request, **ctx
        )

    return StreamingResponse(
        _event_stream(request, render, interval=_METRICS_INTERVAL),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )
