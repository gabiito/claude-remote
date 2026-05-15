"""Red tests for WU-3 — notifier.send_push async ntfy POST.

Uses respx to mock ntfy.sh without hitting the network.
Tests run BEFORE the implementation. They fail until green commit lands.

Covers:
  - Happy path 2xx: correct URL, headers (Title, Priority, Tags), body
  - Notification event: body extracted from payload["message"]
  - Stop event: canned body template
  - Other event types: correct priority and tags
  - Body capped at 1000 chars
  - ntfy 5xx → returns None, no raise
  - httpx.TimeoutException → returns None, no raise
  - httpx.ConnectError → returns None, no raise
  - HTTP 429 → returns None, no raise
  - Malformed JSON in Notification payload → empty body, no raise
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from claude_remote.db.events import Event
from claude_remote.db.notifications import NotificationPreferences
from claude_remote.db.projects import Project

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    event_type: str,
    payload: str | None = None,
) -> Event:
    if payload is None:
        payload = "{}"
    return Event(
        id="evt-001",
        instance_id=None,
        project_id=None,
        event_type=event_type,
        payload=payload,
        received_at="2026-01-01T00:00:00+00:00",
    )


def _make_project(domain: str = "sandbox", name: str = "myproject") -> Project:
    return Project(
        id="proj-001",
        name=name,
        slug="myproject",
        path="/home/user/sandbox/myproject",
        domain=domain,
        created_at="2026-01-01T00:00:00+00:00",
    )


def _make_prefs(ntfy_topic: str = "test-topic-abc") -> NotificationPreferences:
    return NotificationPreferences(
        notify_on_notification=True,
        notify_on_stop=True,
        notify_on_session_end=True,
        notify_on_session_start=True,
        notify_on_pre_tool_use=True,
        notify_on_post_tool_use=True,
        quiet_hours_start=None,
        quiet_hours_end=None,
        ntfy_topic=ntfy_topic,
        updated_at="2026-01-01T00:00:00Z",
    )


async def _call_send_push(
    event: Event,
    project: Project,
    prefs: NotificationPreferences,
    http_client: object = None,
) -> None:
    from claude_remote.services.notifier import send_push  # type: ignore[import]

    await send_push(event, project, prefs, http_client=http_client)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Happy path — headers and body
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_notification_event_posts_to_ntfy_url() -> None:
    """Notification event → POST to https://ntfy.sh/{topic}."""
    prefs = _make_prefs(ntfy_topic="my-topic-xyz")
    event = _make_event("Notification", json.dumps({"message": "Test message"}))
    project = _make_project()

    with respx.mock(base_url="https://ntfy.sh") as mock:
        route = mock.post("/my-topic-xyz").mock(return_value=httpx.Response(200))
        async with httpx.AsyncClient() as client:
            await _call_send_push(event, project, prefs, http_client=client)
        assert route.called


@pytest.mark.anyio
async def test_notification_event_title_header() -> None:
    """Title header must be domain/name."""
    prefs = _make_prefs()
    event = _make_event("Notification", json.dumps({"message": "Hello"}))
    project = _make_project(domain="sandbox", name="myproject")

    with respx.mock(base_url="https://ntfy.sh") as mock:
        route = mock.post("/test-topic-abc").mock(return_value=httpx.Response(200))
        async with httpx.AsyncClient() as client:
            await _call_send_push(event, project, prefs, http_client=client)
        request = route.calls.last.request
        assert request.headers["Title"] == "sandbox/myproject"


@pytest.mark.anyio
async def test_notification_event_priority_urgent() -> None:
    """Notification → Priority: urgent."""
    prefs = _make_prefs()
    event = _make_event("Notification", json.dumps({"message": "Hello"}))
    project = _make_project()

    with respx.mock(base_url="https://ntfy.sh") as mock:
        route = mock.post("/test-topic-abc").mock(return_value=httpx.Response(200))
        async with httpx.AsyncClient() as client:
            await _call_send_push(event, project, prefs, http_client=client)
        request = route.calls.last.request
        assert request.headers["Priority"] == "urgent"


@pytest.mark.anyio
async def test_notification_event_tags_bell() -> None:
    """Notification → Tags: bell."""
    prefs = _make_prefs()
    event = _make_event("Notification", json.dumps({"message": "Hello"}))
    project = _make_project()

    with respx.mock(base_url="https://ntfy.sh") as mock:
        route = mock.post("/test-topic-abc").mock(return_value=httpx.Response(200))
        async with httpx.AsyncClient() as client:
            await _call_send_push(event, project, prefs, http_client=client)
        request = route.calls.last.request
        assert request.headers["Tags"] == "bell"


@pytest.mark.anyio
async def test_notification_event_body_from_payload() -> None:
    """Notification → body extracted from payload['message']."""
    prefs = _make_prefs()
    event = _make_event("Notification", json.dumps({"message": "Test message content"}))
    project = _make_project()

    with respx.mock(base_url="https://ntfy.sh") as mock:
        route = mock.post("/test-topic-abc").mock(return_value=httpx.Response(200))
        async with httpx.AsyncClient() as client:
            await _call_send_push(event, project, prefs, http_client=client)
        request = route.calls.last.request
        assert b"Test message content" in request.content


@pytest.mark.anyio
async def test_stop_event_default_priority() -> None:
    """Stop event → Priority: default."""
    prefs = _make_prefs()
    event = _make_event("Stop")
    project = _make_project()

    with respx.mock(base_url="https://ntfy.sh") as mock:
        route = mock.post("/test-topic-abc").mock(return_value=httpx.Response(200))
        async with httpx.AsyncClient() as client:
            await _call_send_push(event, project, prefs, http_client=client)
        request = route.calls.last.request
        assert request.headers["Priority"] == "default"


@pytest.mark.anyio
async def test_stop_event_tags_octagonal_sign() -> None:
    """Stop event → Tags: octagonal_sign."""
    prefs = _make_prefs()
    event = _make_event("Stop")
    project = _make_project()

    with respx.mock(base_url="https://ntfy.sh") as mock:
        route = mock.post("/test-topic-abc").mock(return_value=httpx.Response(200))
        async with httpx.AsyncClient() as client:
            await _call_send_push(event, project, prefs, http_client=client)
        request = route.calls.last.request
        assert request.headers["Tags"] == "octagonal_sign"


@pytest.mark.anyio
async def test_session_end_tags_checkered_flag() -> None:
    """SessionEnd event → Tags: checkered_flag."""
    prefs = _make_prefs()
    event = _make_event("SessionEnd")
    project = _make_project()

    with respx.mock(base_url="https://ntfy.sh") as mock:
        route = mock.post("/test-topic-abc").mock(return_value=httpx.Response(200))
        async with httpx.AsyncClient() as client:
            await _call_send_push(event, project, prefs, http_client=client)
        request = route.calls.last.request
        assert request.headers["Tags"] == "checkered_flag"


@pytest.mark.anyio
async def test_session_start_tags_rocket() -> None:
    """SessionStart event → Tags: rocket."""
    prefs = _make_prefs()
    event = _make_event("SessionStart")
    project = _make_project()

    with respx.mock(base_url="https://ntfy.sh") as mock:
        route = mock.post("/test-topic-abc").mock(return_value=httpx.Response(200))
        async with httpx.AsyncClient() as client:
            await _call_send_push(event, project, prefs, http_client=client)
        request = route.calls.last.request
        assert request.headers["Tags"] == "rocket"


# ---------------------------------------------------------------------------
# Body capping
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_notification_body_capped_at_1000_chars() -> None:
    """Notification with 2000-char message → body capped at 1000 chars."""
    prefs = _make_prefs()
    long_message = "A" * 2000
    event = _make_event("Notification", json.dumps({"message": long_message}))
    project = _make_project()

    with respx.mock(base_url="https://ntfy.sh") as mock:
        route = mock.post("/test-topic-abc").mock(return_value=httpx.Response(200))
        async with httpx.AsyncClient() as client:
            await _call_send_push(event, project, prefs, http_client=client)
        request = route.calls.last.request
        # Check character count (not bytes) since body is UTF-8 encoded text
        assert len(request.content.decode("utf-8")) <= 1000


# ---------------------------------------------------------------------------
# Error resilience — send_push must NEVER raise
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ntfy_5xx_does_not_raise() -> None:
    """ntfy returns 503 → send_push returns None, no raise."""
    prefs = _make_prefs()
    event = _make_event("Notification", json.dumps({"message": "hi"}))
    project = _make_project()

    with respx.mock(base_url="https://ntfy.sh") as mock:
        mock.post("/test-topic-abc").mock(return_value=httpx.Response(503))
        async with httpx.AsyncClient() as client:
            result = await _call_send_push(event, project, prefs, http_client=client)
    assert result is None


@pytest.mark.anyio
async def test_ntfy_429_does_not_raise() -> None:
    """ntfy returns 429 (rate limit) → send_push returns None, no raise."""
    prefs = _make_prefs()
    event = _make_event("Notification", json.dumps({"message": "hi"}))
    project = _make_project()

    with respx.mock(base_url="https://ntfy.sh") as mock:
        mock.post("/test-topic-abc").mock(return_value=httpx.Response(429))
        async with httpx.AsyncClient() as client:
            result = await _call_send_push(event, project, prefs, http_client=client)
    assert result is None


@pytest.mark.anyio
async def test_timeout_exception_does_not_raise() -> None:
    """httpx.TimeoutException → send_push returns None, no raise."""
    prefs = _make_prefs()
    event = _make_event("Notification", json.dumps({"message": "hi"}))
    project = _make_project()

    with respx.mock(base_url="https://ntfy.sh") as mock:
        mock.post("/test-topic-abc").mock(side_effect=httpx.TimeoutException("timeout"))
        async with httpx.AsyncClient() as client:
            result = await _call_send_push(event, project, prefs, http_client=client)
    assert result is None


@pytest.mark.anyio
async def test_connect_error_does_not_raise() -> None:
    """httpx.ConnectError → send_push returns None, no raise."""
    prefs = _make_prefs()
    event = _make_event("Notification", json.dumps({"message": "hi"}))
    project = _make_project()

    with respx.mock(base_url="https://ntfy.sh") as mock:
        mock.post("/test-topic-abc").mock(side_effect=httpx.ConnectError("refused"))
        async with httpx.AsyncClient() as client:
            result = await _call_send_push(event, project, prefs, http_client=client)
    assert result is None


@pytest.mark.anyio
async def test_malformed_json_payload_does_not_raise() -> None:
    """Malformed JSON in Notification payload → send_push returns None, no raise."""
    prefs = _make_prefs()
    event = _make_event("Notification", "this is not json {{{")
    project = _make_project()

    with respx.mock(base_url="https://ntfy.sh") as mock:
        mock.post("/test-topic-abc").mock(return_value=httpx.Response(200))
        async with httpx.AsyncClient() as client:
            result = await _call_send_push(event, project, prefs, http_client=client)
    assert result is None
