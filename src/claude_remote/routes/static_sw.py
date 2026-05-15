"""Service worker route — serves /sw.js from the static dir with root scope.

ADR-10: SW scope defaults to the directory it's served from. Serving from
/static/sw.js would scope to /static/*. This dedicated route at /sw.js
gives root scope to the worker, with Service-Worker-Allowed: / as belt-and-suspenders.

Cache-Control: no-cache ensures the browser byte-compares the SW on every page load.
When SW_VERSION changes, the browser re-installs the worker.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

_SW_PATH = Path(__file__).parent.parent / "static" / "sw.js"


@router.get("/sw.js")
async def serve_sw_js() -> FileResponse:
    """Serve the service worker file from /sw.js (root scope)."""
    return FileResponse(
        _SW_PATH,
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache",
        },
    )
