"""Deterministic quote-intent detection (R1)."""

from __future__ import annotations

import pytest

from chapterverse.intent import QUOTE_PHRASES, is_quote_request


def test_quote_phrases_constant() -> None:
    assert isinstance(QUOTE_PHRASES, tuple)
    assert len(QUOTE_PHRASES) == 10


@pytest.mark.parametrize(
    "query",
    [
        "Quote s.124 Employment Rights Act 1996",
        "QUOTE THE EXACT STATUTORY WORDING OF SECTION 172(1)(a) TO (d) OF THE COMPANIES ACT 2006.",
        "Give me s.124 ERA 1996 verbatim.",
        "What is the exact wording of section 124 ERA 1996?",
        "Show the Exact Words of s.124 ERA 1996.",
        "Show the exact text of s.124 ERA 1996.",
        "Give me s.124 ERA 1996 word for word.",
        "Full text of section 124 ERA 1996",
        "What is the wording of s.124 ERA 1996?",
        "What are the words of s.124 ERA 1996?",
        "Give me the text of section 124 ERA 1996.",
        "Give me the   text\nof   section 124 ERA 1996.",
    ],
)
def test_quote_requests_detected(query: str) -> None:
    assert is_quote_request(query) is True


@pytest.mark.parametrize(
    "query",
    [
        "",
        "quota",
        "What is the statutory quota for this scheme?",
        "quoted company",
        "What is a quoted company under the Companies Act 2006?",
        "quoted price",
        "How is the quoted price calculated under section 124?",
        "quotation marks",
        "When must quotation marks be used in section 124?",
        "What is the limit of the compensatory award under s.124 ERA 1996?",
    ],
)
def test_non_quote_queries_not_detected(query: str) -> None:
    assert is_quote_request(query) is False
