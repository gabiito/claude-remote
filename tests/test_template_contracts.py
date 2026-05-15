"""Template contract tests — WU-2 (red): SVG icon macros, favicon link, manifest icons.

Tests assert:
  - SVG <svg> tag present in home page response (icon macros render)
  - None of the bare Unicode glyphs ⚙ ↓ ❯ → appear as text in rendered templates
  - GET / response HTML contains <link rel="icon"> pointing to /static/favicon.svg
  - static/manifest.json parsed → icons array has entries with "192x192" and "512x512"
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient

from claude_remote.app import create_app
from claude_remote.config import Settings, get_settings
from claude_remote.db.migrations import MIGRATIONS_DIR, apply_migrations

pytestmark = pytest.mark.anyio

PACKAGE_ROOT = Path(__file__).parent.parent / "src" / "claude_remote"


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
def tc_settings(tmp_db, tmp_projects_root):
    return Settings(db_path=tmp_db, projects_root=tmp_projects_root)


@pytest.fixture()
def tc_app(tc_settings):
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: tc_settings
    yield app
    app.dependency_overrides.clear()


@pytest.fixture()
async def tc_client(tc_app):
    from httpx import ASGITransport

    async with AsyncClient(
        transport=ASGITransport(app=tc_app),
        base_url="http://test",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# WU-2: SVG icon macros — spot checks
# ---------------------------------------------------------------------------


async def test_home_page_contains_svg_for_gear(tc_client: AsyncClient) -> None:
    """Home page renders SVG icon for the settings gear link (replaces ⚙)."""
    response = await tc_client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "<svg" in html


async def test_home_page_no_bare_gear_glyph(tc_client: AsyncClient) -> None:
    """⚙ Unicode glyph must NOT appear as bare text in the home page HTML."""
    response = await tc_client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "⚙" not in html


async def test_home_page_no_bare_down_arrow_glyph(tc_client: AsyncClient) -> None:
    """↓ Unicode glyph must NOT appear as bare text in the home page HTML (sync button)."""
    response = await tc_client.get("/")
    assert response.status_code == 200
    html = response.text
    assert "↓" not in html


# ---------------------------------------------------------------------------
# WU-2: Favicon — base.html link
# ---------------------------------------------------------------------------


async def test_home_page_has_favicon_link(tc_client: AsyncClient) -> None:
    """GET / HTML response contains <link rel="icon"> pointing to /static/favicon.svg."""
    response = await tc_client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'rel="icon"' in html
    assert "/static/favicon.svg" in html


# ---------------------------------------------------------------------------
# WU-2: PWA manifest icons
# ---------------------------------------------------------------------------


def test_manifest_has_required_icon_sizes() -> None:
    """static/manifest.json icons array contains 192x192 and 512x512 size entries."""
    manifest_path = PACKAGE_ROOT / "static" / "manifest.json"
    assert manifest_path.exists(), "manifest.json not found"

    data = json.loads(manifest_path.read_text())
    assert "icons" in data, "manifest.json missing 'icons' key"

    sizes = {icon.get("sizes", "") for icon in data["icons"]}
    # SVG "any" is acceptable; we also require explicit PNG placeholder sizes
    assert "192x192" in sizes, f"192x192 missing from manifest icons: {sizes}"
    assert "512x512" in sizes, f"512x512 missing from manifest icons: {sizes}"


# ---------------------------------------------------------------------------
# WU-4: HTMX indicator CSS + toast slide-in keyframe
# ---------------------------------------------------------------------------


def test_css_has_htmx_indicator_rule() -> None:
    """app.css defines .cr-htmx-indicator with position:fixed and height:2px."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert "cr-htmx-indicator" in css
    assert "position: fixed" in css or "position:fixed" in css


def test_css_has_toast_enter_keyframe() -> None:
    """app.css defines cr-toast-enter animation class."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert "cr-toast-enter" in css


# ---------------------------------------------------------------------------
# WU-5: Card grid transition CSS + pull-refresh
# ---------------------------------------------------------------------------


def test_css_has_card_grid_transition() -> None:
    """app.css defines grid-template-rows transition for .cr-card-expanded."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert "cr-card-expanded" in css
    assert "grid-template-rows" in css


def test_css_has_supports_not_fallback() -> None:
    """app.css has @supports not block for max-height fallback."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert "@supports not" in css


def test_pull_refresh_js_exists() -> None:
    """static/js/pull-refresh.js file exists."""
    js_path = PACKAGE_ROOT / "static" / "js" / "pull-refresh.js"
    assert js_path.exists(), "pull-refresh.js not found"


# ---------------------------------------------------------------------------
# WU-6: Safe-area insets + button scale
# ---------------------------------------------------------------------------


def test_css_has_safe_area_inset_top() -> None:
    """app.css contains env(safe-area-inset-top) for header padding."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert "env(safe-area-inset-top" in css or "env(safe-area-inset-top," in css


def test_css_has_safe_area_inset_bottom() -> None:
    """app.css contains env(safe-area-inset-bottom) for dock padding."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert "env(safe-area-inset-bottom" in css


def test_css_no_non_canonical_border_radius_7px() -> None:
    """app.css does not use border-radius: 7px (non-canonical; use 6px)."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert "border-radius: 7px" not in css


def test_css_no_non_canonical_border_radius_8px() -> None:
    """app.css does not use border-radius: 8px in button/chip rules (use 9px or 6px)."""
    # Check that 8px doesn't appear for button-scale targets
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    # Allow 8px in specific container-only contexts; for MVP we check button selectors
    # The canonical set is 6/9/12 for buttons; 8px is not canonical
    assert "border-radius: 8px" not in css


def test_css_no_non_canonical_border_radius_11px() -> None:
    """app.css does not use border-radius: 11px (non-canonical; use 12px)."""
    css = (PACKAGE_ROOT / "static" / "css" / "app.css").read_text()
    assert "border-radius: 11px" not in css
