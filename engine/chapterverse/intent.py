"""Deterministic query intent detection (no LLM, no I/O)."""

from __future__ import annotations

import re
from typing import Final

__all__ = ["QUOTE_PHRASES", "is_quote_request"]

QUOTE_PHRASES: Final[tuple[str, ...]] = (
    "what are the words of",
    "text of section",
    "exact wording",
    "exact words",
    "exact text",
    "word for word",
    "full text of",
    "wording of",
    "verbatim",
    "quote",
)

_QUOTE_PATTERN: Final = re.compile(
    r"\b(?:" + "|".join(re.escape(phrase) for phrase in QUOTE_PHRASES) + r")\b"
)


def is_quote_request(query: str) -> bool:
    """True when ``query`` asks for the exact wording of legislation."""
    if not query:
        return False
    normalised = " ".join(query.lower().split())
    return _QUOTE_PATTERN.search(normalised) is not None
