import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_root_returns_200(async_client: AsyncClient) -> None:
    response = await async_client.get("/")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_root_content_type_is_html(async_client: AsyncClient) -> None:
    response = await async_client.get("/")
    assert "text/html" in response.headers["content-type"]


@pytest.mark.asyncio
async def test_root_contains_viewport_meta(async_client: AsyncClient) -> None:
    response = await async_client.get("/")
    assert 'name="viewport"' in response.text


@pytest.mark.asyncio
async def test_root_contains_title(async_client: AsyncClient) -> None:
    response = await async_client.get("/")
    assert "<title>" in response.text
    # Title must be non-empty
    import re

    match = re.search(r"<title>(.*?)</title>", response.text, re.DOTALL)
    assert match is not None and match.group(1).strip() != ""


@pytest.mark.asyncio
async def test_root_contains_htmx_script(async_client: AsyncClient) -> None:
    response = await async_client.get("/")
    assert "htmx" in response.text.lower()


@pytest.mark.asyncio
async def test_root_contains_alpinejs_script(async_client: AsyncClient) -> None:
    response = await async_client.get("/")
    assert "alpinejs" in response.text.lower()
