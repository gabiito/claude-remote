"""Shared Jinja2Templates singleton for all route modules.

Importing from app.py would create circular imports (app imports routers;
routers need TEMPLATES).  This thin module breaks the cycle.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

_PACKAGE_ROOT = Path(__file__).parent.parent
templates = Jinja2Templates(directory=_PACKAGE_ROOT / "templates")
