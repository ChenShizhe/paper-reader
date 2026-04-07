"""Cite-key convention utilities for the paper-reader pipeline.

Convention: {firstauthor_lastname}{year}{shortword}
  - All lowercase, no spaces or special characters
  - Examples: smith2024neural, doe1991optimal

NOTE: Existing paper-bank directories are not retroactively renamed;
this convention applies to new runs only.
"""

from __future__ import annotations

import re

# Stop words excluded from title keywords
STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "of", "for", "in", "on", "at", "to", "by",
    "and", "or", "with", "from", "via", "into", "over",
})


def generate_cite_key(
    first_author_lastname: str,
    year: "str | int | None",
    title_keywords: str,
) -> str:
    """Generate a cite-key following the {lastname}{year}{shortword} convention.

    Args:
        first_author_lastname: Last name of the first author.  Pass an empty
            string (or the directory basename as a fallback) when unknown.
        year: 4-digit publication year, or None / empty string if unknown.
            Unknown year is omitted from the generated key.
        title_keywords: Full title or keyword string; 1-2 non-stop words are
            appended after the year segment.

    Returns:
        A lowercase alphanumeric string.  Returns an empty string only when all
        three inputs are empty or produce no usable tokens.

    Examples:
        >>> generate_cite_key("Smith", 2024, "Neural Methods for Sequences")
        'smith2024neural'
        >>> generate_cite_key("Doe", 1991, "Optimal Control")
        'doe1991optimal'
        >>> generate_cite_key("", None, "")
        ''
    """
    # Normalize author lastname: lowercase, keep only alphanumeric characters
    lastname = re.sub(r"[^a-z0-9]", "", first_author_lastname.lower())

    # Year segment: only accept a 4-digit string
    year_str = ""
    if year is not None:
        year_candidate = str(year).strip()
        if re.fullmatch(r"\d{4}", year_candidate):
            year_str = year_candidate

    # Title keywords: pick up to 2 non-stop lowercase words
    words = re.findall(r"[a-zA-Z0-9]+", title_keywords)
    key_words: list[str] = []
    for word in words:
        lowered = word.lower()
        if lowered not in STOP_WORDS:
            key_words.append(lowered)
        if len(key_words) >= 2:
            break

    return lastname + year_str + "".join(key_words)
