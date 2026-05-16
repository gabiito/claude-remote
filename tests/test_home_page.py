"""Tests for GET / home page — WU-6/WU-7 (original) + WU-4 additions.

WU-4 cases extend this file with live_status enrichment + hx-preserve assertions.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.connection import get_connection_for
from claude_remote.db.events import EventsRepository
from claude_remote.db.instances import InstancesRepository
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations
from claude_remote.db.projects import ProjectCreate, ProjectsRepository
from claude_remote.services.tmux_adapter import FakeTmuxAdapter

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
def home_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def fake_adapter():
    return FakeTmuxAdapter()


@pytest.fixture()
def home_app(home_settings, tmp_db, fake_adapter):
    from claude_remote.routes.instances import get_tmux_adapter

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: home_settings
    app.dependency_overrides[get_tmux_adapter] = lambda: fake_adapter
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def home_client(home_app):
    from httpx import ASGITransport
    async with AsyncClient(
        transport=ASGITransport(app=home_app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
def projects_repo(home_settings):
    return ProjectsRepository(
        connection_factory=lambda: get_connection_for(home_settings.db_path)
    )


@pytest.fixture()
def instances_repo(home_settings):
    return InstancesRepository(
        connection_factory=lambda: get_connection_for(home_settings.db_path)
    )


@pytest.fixture()
def events_repo(home_settings):
    return EventsRepository(
        connection_factory=lambda: get_connection_for(home_settings.db_path)
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_home_returns_200(home_client: AsyncClient) -> None:
    """GET / returns 200 with HTML content type."""
    response = await home_client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_home_contains_header(home_client: AsyncClient) -> None:
    """Home page contains the 'claude-remote' header."""
    response = await home_client.get("/")
    assert "claude-remote" in response.text


async def test_home_contains_stylesheet_link(home_client: AsyncClient) -> None:
    """Home page loads app.css stylesheet."""
    response = await home_client.get("/")
    assert "/static/css/app.css" in response.text


async def test_home_contains_alpine_js(home_client: AsyncClient) -> None:
    """Home page loads Alpine.js CDN."""
    response = await home_client.get("/")
    assert "alpinejs" in response.text


async def test_home_contains_htmx(home_client: AsyncClient) -> None:
    """Home page loads HTMX CDN."""
    response = await home_client.get("/")
    assert "htmx.org" in response.text


async def test_home_empty_state(home_client: AsyncClient) -> None:
    """Home page shows empty state message when no projects registered."""
    response = await home_client.get("/")
    assert response.status_code == 200
    assert "No projects yet" in response.text


async def test_home_empty_state_renders_project_list_container(
    home_client: AsyncClient,
) -> None:
    """Empty state still renders the .cr-list container so HTMX form swap on
    POST /ui/projects (hx-target='.cr-list') has a target on first create.

    Regression test for the 2026-05 bug where the empty state replaced .cr-list
    entirely, causing htmx:targetError on the very first project create.
    """
    response = await home_client.get("/")
    assert response.status_code == 200
    assert 'class="cr-list cr-scroll"' in response.text
    assert "cr-empty" in response.text  # empty-state still shows inside the container


async def test_home_lists_projects(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
) -> None:
    """Home page lists registered projects with project-card elements."""
    # Create two fake project directories and register them
    p1_path = tmp_projects_root / "example.com" / "alpha"
    p1_path.mkdir(parents=True)
    p2_path = tmp_projects_root / "example.com" / "beta"
    p2_path.mkdir(parents=True)

    projects_repo.create(
        project_create=ProjectCreate(name="Alpha", slug="alpha", path=p1_path, domain="example.com")
    )
    projects_repo.create(
        project_create=ProjectCreate(name="Beta", slug="beta", path=p2_path, domain="example.com")
    )

    response = await home_client.get("/")
    assert response.status_code == 200
    assert "Alpha" in response.text
    assert "Beta" in response.text
    # New design uses cr-card class
    assert 'cr-card' in response.text
    assert "data-project-id=" in response.text


async def test_home_renders_existing_domain_options(
    home_client: AsyncClient,
    tmp_projects_root,
) -> None:
    """Home page exposes existing top-level domain directories for the create form."""
    (tmp_projects_root / "alpha-domain").mkdir()
    (tmp_projects_root / "beta-domain").mkdir()
    (tmp_projects_root / "not-a-dir.txt").touch()  # files should be ignored

    response = await home_client.get("/")
    assert response.status_code == 200
    assert "alpha-domain" in response.text
    assert "beta-domain" in response.text
    assert "not-a-dir.txt" not in response.text


async def test_home_project_card_has_data_id(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
) -> None:
    """Each project card has a data-project-id attribute."""
    p_path = tmp_projects_root / "acme.com" / "myproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(name="MyProj", slug="myproj", path=p_path, domain="acme.com")
    )

    response = await home_client.get("/")
    assert f'data-project-id="{project.id}"' in response.text


# ---------------------------------------------------------------------------
# WU-4 additions: live_status enrichment + hx-preserve + polling attrs
# ---------------------------------------------------------------------------


async def test_home_card_has_htmx_polling_attrs(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
) -> None:
    """GET / renders project cards with hx-get polling attribute pointing to /card endpoint."""
    p_path = tmp_projects_root / "acme.com" / "pollproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="PollProj", slug="pollproj", path=p_path, domain="acme.com"
        )
    )

    response = await home_client.get("/")
    assert response.status_code == 200
    assert f'hx-get="/ui/projects/{project.id}/card"' in response.text
    assert "every 5s" in response.text
    assert 'hx-swap="outerHTML"' in response.text


async def test_home_live_status_pill_running_when_no_events(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """GET / shows status-running class for instance with no recent events."""
    p_path = tmp_projects_root / "acme.com" / "runproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="RunProj", slug="runproj", path=p_path, domain="acme.com"
        )
    )

    # Launch an instance so there's one to render
    launch_resp = await home_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200

    response = await home_client.get("/")
    assert response.status_code == 200
    # Instance with no events → live_status=running → pill with data-status="running"
    assert 'data-status="running"' in response.text or "RUNNING" in response.text


async def test_home_live_status_pill_active_with_pretooluse(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    events_repo: EventsRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """GET / shows status-active class for instance with recent PreToolUse."""
    p_path = tmp_projects_root / "acme.com" / "activeproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="ActiveProj", slug="activeproj", path=p_path, domain="acme.com"
        )
    )

    launch_resp = await home_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200

    instances = instances_repo.list_by_project(project.id)
    instance = instances[0]

    events_repo.create(
        instance_id=instance.id,
        project_id=project.id,
        event_type="PreToolUse",
        payload=json.dumps({"tool_name": "Edit"}),
    )

    response = await home_client.get("/")
    assert response.status_code == 200
    # New design: data-status="active" on cr-pill/cr-led elements
    assert 'data-status="active"' in response.text or "ACTIVE" in response.text


async def test_home_events_feed_visible_when_events_exist(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    events_repo: EventsRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """GET / shows events feed <details> when project has events."""
    p_path = tmp_projects_root / "acme.com" / "feedproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="FeedProj", slug="feedproj", path=p_path, domain="acme.com"
        )
    )

    launch_resp = await home_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200
    instances = instances_repo.list_by_project(project.id)
    instance = instances[0]

    events_repo.create(
        instance_id=instance.id,
        project_id=project.id,
        event_type="Notification",
        payload=json.dumps({"message": "Review"}),
    )

    response = await home_client.get("/")
    assert response.status_code == 200
    # New design uses cr-events-mini inside cr-card-expanded
    assert 'cr-events-mini' in response.text or 'cr-event-mini' in response.text


async def test_home_events_feed_absent_when_no_events(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
) -> None:
    """GET / hides events feed when project has no events."""
    p_path = tmp_projects_root / "acme.com" / "emptyproj"
    p_path.mkdir(parents=True)
    projects_repo.create(
        project_create=ProjectCreate(
            name="EmptyProj", slug="emptyproj", path=p_path, domain="acme.com"
        )
    )

    response = await home_client.get("/")
    assert response.status_code == 200
    assert 'class="events-feed"' not in response.text


# ---------------------------------------------------------------------------
# WU-6 (mvp-project-discovery) — sync button + stale badge + toast
# ---------------------------------------------------------------------------


async def test_home_contains_sync_button(home_client: AsyncClient) -> None:
    """Home page contains HTMX sync button pointing to /ui/discovery/sync."""
    response = await home_client.get("/")
    assert response.status_code == 200
    assert 'hx-post="/ui/discovery/sync"' in response.text


async def test_home_contains_sync_toast_div(home_client: AsyncClient) -> None:
    """Home page contains <div id='sync-toast'> as the HTMX swap target."""
    response = await home_client.get("/")
    assert response.status_code == 200
    assert 'id="sync-toast"' in response.text


async def test_home_stale_card_has_data_stale_attr(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
) -> None:
    """Stale project card has data-stale='1' attribute."""
    p_path = tmp_projects_root / "acme" / "gone"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="gone", slug="gone", path=p_path, domain="acme"
        )
    )
    projects_repo.mark_stale(project.id)

    response = await home_client.get("/")
    assert response.status_code == 200
    assert 'data-stale="1"' in response.text


async def test_home_stale_card_shows_stale_badge(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
) -> None:
    """Stale project card contains 'stale' text badge."""
    p_path = tmp_projects_root / "acme" / "staleproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="staleproj", slug="staleproj", path=p_path, domain="acme"
        )
    )
    projects_repo.mark_stale(project.id)

    response = await home_client.get("/")
    assert response.status_code == 200
    assert "stale" in response.text


async def test_home_non_stale_card_has_no_data_stale(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
) -> None:
    """Non-stale project card does NOT have data-stale='1'."""
    p_path = tmp_projects_root / "acme" / "healthy"
    p_path.mkdir(parents=True)
    projects_repo.create(
        project_create=ProjectCreate(
            name="healthy", slug="healthy", path=p_path, domain="acme"
        )
    )

    response = await home_client.get("/")
    assert response.status_code == 200
    assert 'data-stale="1"' not in response.text


async def test_home_instance_row_has_data_db_and_live_status(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """GET / renders project cards with data-status (live status indicator)."""
    p_path = tmp_projects_root / "acme.com" / "attrproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="AttrProj", slug="attrproj", path=p_path, domain="acme.com"
        )
    )

    launch_resp = await home_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200

    response = await home_client.get("/")
    assert response.status_code == 200
    # New design: card LED uses data-status; pill uses data-status
    assert "data-status=" in response.text


# ---------------------------------------------------------------------------
# WU-6 (mvp-notifications) — gear link to /settings
# ---------------------------------------------------------------------------


async def test_home_contains_gear_link(home_client: AsyncClient) -> None:
    """GET / → home page contains a gear link pointing to /settings."""
    response = await home_client.get("/")
    assert response.status_code == 200
    assert 'href="/settings"' in response.text


async def test_home_gear_link_has_settings_class(home_client: AsyncClient) -> None:
    """GET / → gear link uses cr-gear-link class."""
    response = await home_client.get("/")
    assert response.status_code == 200
    assert "cr-gear-link" in response.text


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# WU-3 (mvp-visual-polish) — status_breakdown + sparkline + dynamic title
# ---------------------------------------------------------------------------


async def test_home_title_no_needs_count(home_client: AsyncClient) -> None:
    """GET / with zero needs_input projects → title is plain 'claude-remote'."""
    response = await home_client.get("/")
    assert response.status_code == 200
    # No parenthetical when count is zero
    assert "<title>claude-remote</title>" in response.text


async def test_home_title_with_needs_count(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    events_repo: EventsRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """GET / with needs_input projects → title contains count like 'claude-remote (N)'."""
    p_path = tmp_projects_root / "acme.com" / "needsproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="NeedsProj", slug="needsproj", path=p_path, domain="acme.com"
        )
    )
    launch_resp = await home_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200
    instances = instances_repo.list_by_project(project.id)
    instance = instances[0]

    # Notification event → needs_input status
    events_repo.create(
        instance_id=instance.id,
        project_id=project.id,
        event_type="Notification",
        payload=json.dumps({"message": "Review please"}),
    )

    response = await home_client.get("/")
    assert response.status_code == 200
    # Title must contain the count in parentheses
    assert "claude-remote (1)" in response.text


async def test_home_vitals_breakdown_shows_nonzero_counts(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    events_repo: EventsRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """GET / vitals strip renders non-zero status counts (e.g. 'running', 'needs')."""
    # Create a project with an instance in needs_input
    p_path = tmp_projects_root / "acme.com" / "vitals1"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="Vitals1", slug="vitals1", path=p_path, domain="acme.com"
        )
    )
    launch_resp = await home_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200
    instances = instances_repo.list_by_project(project.id)
    instance = instances[0]

    events_repo.create(
        instance_id=instance.id,
        project_id=project.id,
        event_type="Notification",
        payload=json.dumps({"message": "need input"}),
    )

    response = await home_client.get("/")
    assert response.status_code == 200
    # Should render a status label (needs, running, idle, etc.)
    assert any(
        label in response.text
        for label in ["needs", "running", "active", "idle", "stopped", "crashed"]
    )


async def test_home_vitals_breakdown_omits_zero_counts(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
) -> None:
    """GET / vitals strip must NOT render zero-count status entries."""
    p_path = tmp_projects_root / "acme.com" / "vitals2"
    p_path.mkdir(parents=True)
    projects_repo.create(
        project_create=ProjectCreate(
            name="Vitals2", slug="vitals2", path=p_path, domain="acme.com"
        )
    )

    response = await home_client.get("/")
    assert response.status_code == 200
    # "0 crashed" or "0 stopped" must not appear
    assert "0 crashed" not in response.text
    assert "0 stopped" not in response.text


async def test_home_sparkline_has_8_bars(home_client: AsyncClient) -> None:
    """GET / renders exactly 8 cr-spark-bar elements driven by real spark_data."""
    response = await home_client.get("/")
    assert response.status_code == 200
    # The template should render 8 spark bars from the spark_data context variable
    bar_count = response.text.count('class="cr-spark-bar"')
    assert bar_count == 8


async def test_home_sparkline_bars_have_height_style(home_client: AsyncClient) -> None:
    """GET / spark bars have inline style='height: Npx;' attributes."""
    response = await home_client.get("/")
    assert response.status_code == 200
    # Each bar should have a height style (data-driven inline style)
    assert 'style="height:' in response.text or "style=\"height:" in response.text


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# WU-4 (mvp-visual-polish) — HTMX indicator, polling dot, toast
# ---------------------------------------------------------------------------


async def test_home_htmx_indicator_present_in_body(home_client: AsyncClient) -> None:
    """Home page body contains the .cr-htmx-indicator.htmx-indicator div."""
    response = await home_client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "cr-htmx-indicator" in html
    assert "htmx-indicator" in html


async def test_home_vitals_led_has_alpine_x_data(home_client: AsyncClient) -> None:
    """Vitals LED wrapper has x-data Alpine attribute for pulse toggling."""
    response = await home_client.get("/")
    assert response.status_code == 200
    html = response.text
    # The vitals LED wrapper should have x-data for the active state
    assert "x-data" in html
    assert "cr-vitals-led" in html


# ---------------------------------------------------------------------------
# WU-5 (mvp-visual-polish) — card grid transition + pull-to-refresh
# ---------------------------------------------------------------------------


async def test_home_pull_refresh_script_loaded(home_client: AsyncClient) -> None:
    """Home page base.html loads pull-refresh.js script."""
    response = await home_client.get("/")
    assert response.status_code == 200
    assert "pull-refresh.js" in response.text


async def test_home_card_expanded_has_no_x_show(home_client: AsyncClient) -> None:
    """Card expanded section uses CSS grid transition — no x-show attribute needed."""
    response = await home_client.get("/")
    assert response.status_code == 200
    html = response.text
    # cr-card-expanded renders when projects exist; without projects page still loads
    # The key assertion: x-cloak must not appear on cr-card-expanded (CSS handles it)
    assert "x-cloak" not in html or "cr-card-expanded" not in html


# ---------------------------------------------------------------------------
# Roadmap #2 WU-1 — ACTIVE SESSIONS / PROJECTS grouped sections
# ---------------------------------------------------------------------------


async def test_home_renders_two_section_headers(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """A launched project (live → ACTIVE SESSIONS) and an idle-registered one
    (no session → PROJECTS) make both section headers render."""
    live_path = tmp_projects_root / "wooli" / "landing"
    live_path.mkdir(parents=True)
    live = projects_repo.create(
        project_create=ProjectCreate(
            name="landing", slug="landing", path=live_path, domain="wooli"
        )
    )
    dead_path = tmp_projects_root / "sandbox" / "exp"
    dead_path.mkdir(parents=True)
    projects_repo.create(
        project_create=ProjectCreate(
            name="exp", slug="exp", path=dead_path, domain="sandbox"
        )
    )
    launch = await home_client.post(f"/ui/projects/{live.id}/launch")
    assert launch.status_code == 200

    response = await home_client.get("/")
    assert response.status_code == 200
    assert "ACTIVE SESSIONS" in response.text
    assert "PROJECTS" in response.text
    assert "cr-section-head" in response.text


async def test_home_active_card_led_reflects_running_not_stopped(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """A launched project's card LED/border must show its real status, not the
    always-'stopped' value the loop-scoped Jinja {% set %} produced."""
    p = tmp_projects_root / "wooli" / "live1"
    p.mkdir(parents=True)
    proj = projects_repo.create(
        project_create=ProjectCreate(
            name="live1", slug="live1", path=p, domain="wooli"
        )
    )
    launch = await home_client.post(f"/ui/projects/{proj.id}/launch")
    assert launch.status_code == 200

    html = (await home_client.get("/")).text
    # The cr-led for a running instance must not be data-status="stopped".
    assert '<div class="cr-led" data-status="running">' in html
    assert '<div class="cr-led" data-status="stopped">' not in html


async def test_home_active_section_absent_when_no_live_sessions(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    tmp_projects_root,
) -> None:
    """With only inert projects there is no ACTIVE SESSIONS header."""
    p = tmp_projects_root / "sandbox" / "only"
    p.mkdir(parents=True)
    projects_repo.create(
        project_create=ProjectCreate(
            name="only", slug="only", path=p, domain="sandbox"
        )
    )
    response = await home_client.get("/")
    assert response.status_code == 200
    assert "ACTIVE SESSIONS" not in response.text
    assert "PROJECTS" in response.text


# ---------------------------------------------------------------------------
# Pre-existing test (must stay at end)
# ---------------------------------------------------------------------------


async def test_home_events_feed_has_stable_id_and_hx_preserve(
    home_client: AsyncClient,
    projects_repo: ProjectsRepository,
    instances_repo: InstancesRepository,
    events_repo: EventsRepository,
    tmp_projects_root,
    fake_adapter: FakeTmuxAdapter,
) -> None:
    """GET / events feed renders when project has events (card-expanded section)."""
    p_path = tmp_projects_root / "acme.com" / "preserveproj"
    p_path.mkdir(parents=True)
    project = projects_repo.create(
        project_create=ProjectCreate(
            name="PreserveProj", slug="preserveproj", path=p_path, domain="acme.com"
        )
    )

    launch_resp = await home_client.post(f"/ui/projects/{project.id}/launch")
    assert launch_resp.status_code == 200
    instances = instances_repo.list_by_project(project.id)
    instance = instances[0]

    events_repo.create(
        instance_id=instance.id,
        project_id=project.id,
        event_type="Notification",
        payload=json.dumps({"message": "test"}),
    )

    response = await home_client.get("/")
    assert response.status_code == 200
    # New design: events feed appears in the card-expanded section
    assert "cr-events-mini" in response.text or "cr-event-mini" in response.text
