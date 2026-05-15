"""Shared Jinja2Templates singleton for all route modules.

Importing from app.py would create circular imports (app imports routers;
routers need TEMPLATES).  This thin module breaks the cycle.

Custom Jinja2 filters registered here (ADR-3):
  - ``format_relative``  → services/timefmt.py
  - ``extract_snippet``  → services/event_snippet.py
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from claude_remote.services.event_snippet import extract_snippet
from claude_remote.services.timefmt import format_relative

_PACKAGE_ROOT = Path(__file__).parent.parent
templates = Jinja2Templates(directory=_PACKAGE_ROOT / "templates")

# Register display helpers as Jinja2 filters so templates can call them inline.
templates.env.filters["format_relative"] = format_relative
templates.env.filters["extract_snippet"] = extract_snippet
