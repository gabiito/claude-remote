"""Tests for the slugify function.

WU-3 — RED tests (must fail until services/slug.py is implemented).
Parametrized table covers all design §4.6 behavior cases.
"""

import pytest

from claude_remote.services.slug import slugify


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Hello", "hello"),
        ("  spaced  out  ", "spaced-out"),
        ("foo!@#bar", "foo-bar"),
        ("foo---bar", "foo-bar"),
        ("!!!", ""),
        # Unicode limitation: non-ASCII chars are treated as non-alphanumeric.
        # "caña-brava" → "ca" + "-" + "a-brava" → "ca-a-brava"
        # This is a documented limitation; no transliteration is performed.
        ("caña-brava", "ca-a-brava"),
        ("my-cool-project", "my-cool-project"),
        ("My Cool Project", "my-cool-project"),
        ("   ", ""),
        ("123", "123"),
        ("-hello-", "hello"),
        ("foo  bar", "foo-bar"),
    ],
)
def test_slugify(text: str, expected: str) -> None:
    assert slugify(text) == expected
