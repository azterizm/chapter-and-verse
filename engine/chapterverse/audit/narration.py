"""Detect narration and absence phrases in sentences (requirements R3).

A sentence that narrates the system's inputs or asserts absence of legal rules
outside what the cited statute itself states is treated as an abstention.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from chapterverse.audit.prompt import is_abstention

__all__ = ["ABSENCE_PHRASES", "INPUT_PHRASES", "is_abstention", "is_narration"]

# Phrases referring to the provided inputs or text excerpts.
INPUT_PHRASES: Final[tuple[str, ...]] = (
    "provided passage",
    "the passages",
    "passages provided",
    "provisions provided",
    "text provided",
    "the excerpt",
    "the extract",
)

# Phrases asserting absence of information or statement in the law.
# Note: "does not contain" and "does not apply" are omitted because statutory text uses them.
ABSENCE_PHRASES: Final[tuple[str, ...]] = (
    "do not state",
    "does not state",
    "not stated",
    "do not specify",
    "does not specify",
    "no information",
    "not mentioned",
    "do not mention",
    "does not mention",
    "do not say",
    "does not say",
)


def is_narration(text: str, cited_texts: Sequence[str] = ()) -> bool:
    """True when text refers to its inputs or asserts absence not in the cited text.

    Args:
        text: The sentence text to check.
        cited_texts: The full text of each passage cited by the sentence.

    Returns:
        True if the sentence is narration or an ungrounded absence assertion.
    """
    lowered = text.lower()
    for phrase in INPUT_PHRASES:
        if phrase in lowered:
            return True

    lowered_cited = [c.lower() for c in cited_texts]
    for phrase in ABSENCE_PHRASES:
        if phrase in lowered and not any(phrase in c for c in lowered_cited):
            return True

    return False
