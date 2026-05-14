"""Slug generation utility.

Hand-rolled; zero deps. One regex + strip.

Unicode limitation: non-ASCII characters (e.g. accented letters) are treated
as non-alphanumeric and replaced with '-'. No transliteration is performed.
Example: "caña-brava" → "ca-a-brava".

If the caller needs transliteration, install python-slugify and swap this module.
"""

import re

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Convert text to a URL-safe slug.

    Steps:
      1. Strip leading/trailing whitespace.
      2. Lowercase.
      3. Replace any run of non-alphanumeric chars with a single '-'.
      4. Strip leading/trailing '-'.

    Returns "" for inputs that consist entirely of non-alphanumeric chars.
    Caller is responsible for handling the empty-string case (e.g. return 400).
    """
    return _SLUG_RE.sub("-", text.strip().lower()).strip("-")
