import pytest
from httpx import ASGITransport, AsyncClient

from claude_remote.app import create_app


@pytest.fixture()
async def async_client() -> AsyncClient:
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),  # type: ignore[arg-type]
        base_url="http://test",
    ) as client:
        yield client  # type: ignore[misc]
