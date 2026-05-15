"""Tests for GET /settings + POST /ui/settings — WU-5 (RED).

Spec requirements: REQ-N8, REQ-N9, REQ-N10, NFR-5.

Cases:
  1. GET /settings returns 200 HTML with form.
  2. GET /settings pre-fills current prefs (checked toggles + time values + topic).
  3. POST /ui/settings happy path: all toggles + quiet hours → 200 + toast + HX-Trigger.
  4. POST /ui/settings updates DB row.
  5. POST /ui/settings with NO checkbox fields → all 6 toggles become False (HTMX semantics).
  6. POST /ui/settings with bad time format → 400 + error fragment.
  7. POST /ui/settings with empty time strings → DB stores NULL.
  8. POST /ui/settings valid quiet hours → DB stores the values.
  9. GET /settings shows all 6 toggle inputs.
 10. GET /settings shows ntfy_topic value.
 11. POST /ui/settings returns HX-Trigger: settings-saved header on success.
 12. POST /ui/settings 400 does NOT set HX-Trigger header.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.db.notifications import NotificationsRepository

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_db(tmp_path):
    db = tmp_path / "test.db"
    apply_migrations(db, MIGRATIONS_DIR)
    return db


@pytest.fixture()
def tmp_projects_root(tmp_path):
    root = tmp_path / "projects"
    root.mkdir()
    return root


@pytest.fixture()
def st_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def notifications_repo(st_settings):
    return NotificationsRepository(
        connection_factory=lambda: get_connection_for(st_settings.db_path)
    )


@pytest.fixture()
def st_app(st_settings):
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: st_settings
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def st_client(st_app) -> AsyncClient:
    from httpx import ASGITransport

    async with AsyncClient(
        transport=ASGITransport(app=st_app),
        base_url="http://test",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# WU-5 tests
# ---------------------------------------------------------------------------


async def test_get_settings_returns_200(st_client: AsyncClient) -> None:
    """GET /settings → 200 with HTML content type."""
    response = await st_client.get("/settings")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_get_settings_contains_form(st_client: AsyncClient) -> None:
    """GET /settings → page contains a form targeting /ui/settings."""
    response = await st_client.get("/settings")
    assert response.status_code == 200
    assert "/ui/settings" in response.text


async def test_get_settings_has_all_six_toggle_inputs(st_client: AsyncClient) -> None:
    """GET /settings → page contains all 6 event-type toggle checkboxes."""
    response = await st_client.get("/settings")
    assert response.status_code == 200
    html = response.text
    for field in (
        "notify_on_notification",
        "notify_on_stop",
        "notify_on_session_end",
        "notify_on_session_start",
        "notify_on_pre_tool_use",
        "notify_on_post_tool_use",
    ):
        assert field in html, f"Missing toggle: {field}"


async def test_get_settings_prefills_checked_toggle(
    st_client: AsyncClient,
    notifications_repo: NotificationsRepository,
) -> None:
    """GET /settings → notify_on_notification is checked by default (seed value=1)."""
    response = await st_client.get("/settings")
    assert response.status_code == 200
    # notify_on_notification=1 in the seed row → should be checked
    assert "checked" in response.text


async def test_get_settings_shows_ntfy_topic(
    st_client: AsyncClient,
    notifications_repo: NotificationsRepository,
) -> None:
    """GET /settings → ntfy_topic value appears in the page."""
    prefs = notifications_repo.get()
    response = await st_client.get("/settings")
    assert response.status_code == 200
    assert prefs.ntfy_topic in response.text


async def test_get_settings_prefills_quiet_hours(
    st_client: AsyncClient,
    notifications_repo: NotificationsRepository,
) -> None:
    """GET /settings → quiet hours fields show current values when set."""
    notifications_repo.update(quiet_hours_start="22:00", quiet_hours_end="08:00")
    response = await st_client.get("/settings")
    assert response.status_code == 200
    assert "22:00" in response.text
    assert "08:00" in response.text


async def test_post_settings_happy_path_returns_200_and_toast(
    st_client: AsyncClient,
) -> None:
    """POST /ui/settings with valid data → 200 + toast containing confirmation text."""
    response = await st_client.post(
        "/ui/settings",
        data={
            "notify_on_notification": "on",
            "notify_on_stop": "on",
            "quiet_hours_start": "",
            "quiet_hours_end": "",
        },
    )
    assert response.status_code == 200
    assert "Settings saved" in response.text


async def test_post_settings_returns_hx_trigger_header(
    st_client: AsyncClient,
) -> None:
    """POST /ui/settings success → response header HX-Trigger: settings-saved."""
    response = await st_client.post(
        "/ui/settings",
        data={"quiet_hours_start": "", "quiet_hours_end": ""},
    )
    assert response.status_code == 200
    assert response.headers.get("hx-trigger") == "settings-saved"


async def test_post_settings_updates_db(
    st_client: AsyncClient,
    notifications_repo: NotificationsRepository,
) -> None:
    """POST /ui/settings → DB row updated with submitted values."""
    response = await st_client.post(
        "/ui/settings",
        data={
            "notify_on_notification": "on",
            "notify_on_stop": "on",
            "quiet_hours_start": "23:00",
            "quiet_hours_end": "07:00",
        },
    )
    assert response.status_code == 200
    prefs = notifications_repo.get()
    assert prefs.notify_on_notification is True
    assert prefs.notify_on_stop is True
    assert prefs.quiet_hours_start == "23:00"
    assert prefs.quiet_hours_end == "07:00"


async def test_post_settings_no_checkboxes_all_toggles_false(
    st_client: AsyncClient,
    notifications_repo: NotificationsRepository,
) -> None:
    """POST /ui/settings with NO checkbox fields → all 6 toggles become False.

    This is the critical HTMX checkbox semantics test: unchecked checkboxes are
    omitted from the POST body entirely. Form(default=False) must handle this.
    """
    # First turn everything on
    notifications_repo.update(
        notify_on_notification=True,
        notify_on_stop=True,
        notify_on_session_end=True,
        notify_on_session_start=True,
        notify_on_pre_tool_use=True,
        notify_on_post_tool_use=True,
    )
    # Submit with NO checkbox fields at all (simulates all unchecked)
    response = await st_client.post(
        "/ui/settings",
        data={"quiet_hours_start": "", "quiet_hours_end": ""},
    )
    assert response.status_code == 200
    prefs = notifications_repo.get()
    assert prefs.notify_on_notification is False
    assert prefs.notify_on_stop is False
    assert prefs.notify_on_session_end is False
    assert prefs.notify_on_session_start is False
    assert prefs.notify_on_pre_tool_use is False
    assert prefs.notify_on_post_tool_use is False


async def test_post_settings_invalid_time_format_returns_400(
    st_client: AsyncClient,
) -> None:
    """POST /ui/settings with invalid time format → 400."""
    response = await st_client.post(
        "/ui/settings",
        data={"quiet_hours_start": "25:99", "quiet_hours_end": ""},
    )
    assert response.status_code == 400


async def test_post_settings_invalid_time_no_hx_trigger(
    st_client: AsyncClient,
) -> None:
    """POST /ui/settings with invalid time format → NO HX-Trigger header."""
    response = await st_client.post(
        "/ui/settings",
        data={"quiet_hours_start": "not-a-time", "quiet_hours_end": ""},
    )
    assert response.status_code == 400
    assert "hx-trigger" not in response.headers


async def test_post_settings_empty_time_strings_store_null(
    st_client: AsyncClient,
    notifications_repo: NotificationsRepository,
) -> None:
    """POST /ui/settings with empty time strings → DB stores NULL."""
    # First set some values
    notifications_repo.update(quiet_hours_start="22:00", quiet_hours_end="08:00")
    # Submit empty strings
    response = await st_client.post(
        "/ui/settings",
        data={"quiet_hours_start": "", "quiet_hours_end": ""},
    )
    assert response.status_code == 200
    prefs = notifications_repo.get()
    assert prefs.quiet_hours_start is None
    assert prefs.quiet_hours_end is None


async def test_post_settings_valid_quiet_hours_stored(
    st_client: AsyncClient,
    notifications_repo: NotificationsRepository,
) -> None:
    """POST /ui/settings with valid quiet hours → stored correctly in DB."""
    response = await st_client.post(
        "/ui/settings",
        data={"quiet_hours_start": "22:00", "quiet_hours_end": "08:00"},
    )
    assert response.status_code == 200
    prefs = notifications_repo.get()
    assert prefs.quiet_hours_start == "22:00"
    assert prefs.quiet_hours_end == "08:00"
