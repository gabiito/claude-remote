"""Tests for services/ansi_html.py — convert_ansi() — WU-1 (red).

Covers:
  - Empty input → empty string (fast path)
  - Plain text (no escapes) → preserved, no <span> tags
  - Red foreground \\x1b[31m → span with class containing "ansi31"
  - Bold \\x1b[1m → span with class containing "ansi1"
  - Malformed escape → output is safe (no raw \\x1b bytes)
  - HTML special chars in input → properly escaped in output (XSS safety)
"""

from __future__ import annotations


def test_empty_input_returns_empty() -> None:
    """convert_ansi('') must return '' immediately (fast path)."""
    from claude_remote.services.ansi_html import convert_ansi

    result = convert_ansi("")
    assert result == ""


def test_plain_text_unchanged() -> None:
    """Plain text without ANSI escapes is preserved; no <span> tags added."""
    from claude_remote.services.ansi_html import convert_ansi

    result = convert_ansi("plain text")
    assert "plain text" in result
    assert '<span class="ansi' not in result


def test_red_foreground_produces_ansi31_span() -> None:
    """\\x1b[31m (red foreground) → HTML contains class containing 'ansi31'."""
    from claude_remote.services.ansi_html import convert_ansi

    result = convert_ansi("\x1b[31mred\x1b[0m")
    assert "red" in result
    assert "ansi31" in result


def test_bold_produces_ansi1_span() -> None:
    """\\x1b[1m (bold) → HTML contains class containing 'ansi1'."""
    from claude_remote.services.ansi_html import convert_ansi

    result = convert_ansi("\x1b[1mBold\x1b[0m")
    assert "Bold" in result
    assert "ansi1" in result


def test_malformed_escape_does_not_leak_raw_bytes() -> None:
    """Truncated/malformed escape sequence must not leave raw \\x1b bytes in output."""
    from claude_remote.services.ansi_html import convert_ansi

    result = convert_ansi("text\x1b[incomplete")
    # No raw ESC byte in HTML output
    assert "\x1b" not in result
    # Text portion must be present
    assert "text" in result


def test_html_special_chars_are_escaped() -> None:
    """< > & in input must be HTML-escaped (XSS safety)."""
    from claude_remote.services.ansi_html import convert_ansi

    result = convert_ansi("<script>alert('xss')</script>")
    assert "<script>" not in result
    assert "&lt;" in result or "&#60;" in result or "&lt;script" in result
