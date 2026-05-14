import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_healthz_returns_ok(async_client: AsyncClient) -> None:
    response = await async_client.get("/healthz")
    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    assert response.json() == {"status": "ok"}
