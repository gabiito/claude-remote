"""ANSI escape → HTML span conversion (Catppuccin Mocha palette).

Wraps ansi2html.Ansi2HTMLConverter with inline=False so colors come from
CSS classes (.ansi30..37, .ansi40..47, .ansi90..97, .ansi1 for bold).
Caller is responsible for marking the result as Markup-safe in Jinja2
(the converter HTML-escapes input before adding spans, so output is
safe by construction).
"""

from __future__ import annotations

import html as _html
from functools import lru_cache

from ansi2html import Ansi2HTMLConverter


@lru_cache(maxsize=1)
def _converter() -> Ansi2HTMLConverter:
    """Return a cached Ansi2HTMLConverter configured for class-based output.

    inline=False → emits <span class="ansiN"> names; we map them in app.css.
    escaped=True → treats input as pre-escaped HTML (we pass raw text, not
    pre-escaped, so this is False — ansi2html handles escaping internally).
    dark_bg=True → the ansi2html scheme uses dark-background assumption,
    but since we use inline=False the actual scheme colors are irrelevant
    (all coloring is done by CSS classes).
    """
    return Ansi2HTMLConverter(inline=False, dark_bg=True)


def convert_ansi(raw: str) -> str:
    """Convert ANSI-escaped text to HTML with <span class="ansiN"> spans.

    The output is HTML-safe: ansi2html escapes input before wrapping spans.
    Empty input returns empty string (fast path).

    Args:
        raw: Raw string potentially containing ANSI escape sequences.

    Returns:
        HTML string with ANSI codes replaced by <span class="ansiN"> elements.
        On any parser exception, returns html.escape(raw) as a safe fallback.
    """
    if not raw:
        return ""
    try:
        # full=False returns body only — no <html><head>... wrapper
        return _converter().convert(raw, full=False)
    except Exception:  # noqa: BLE001 — graceful degradation on malformed input
        return _html.escape(raw)
