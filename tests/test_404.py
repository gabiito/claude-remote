import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_unknown_route_returns_404(async_client: AsyncClient) -> None:
    response = await async_client.get("/does-not-exist")
    assert response.status_code == 404
