"""Tests for narration and absence phrase detection (requirements R3, R7)."""

from __future__ import annotations

import pytest

from chapterverse.audit.narration import (
    ABSENCE_PHRASES,
    INPUT_PHRASES,
    is_narration,
)


@pytest.mark.parametrize(
    "sentence",
    [
        "The provided passages do not state the general limitation period.",
        "According to the passages provided, the cap is £123,543.",
        "The text provided states that fraud is excluded.",
        "From the excerpt, one cannot tell.",
        "The extract shows section 124(1).",
        "From the provisions provided, the answer is no.",
        "This is taken from the provided passage.",
        "As the passages show, six years is the limit.",
    ],
)
def test_input_phrases_are_narration(sentence: str) -> None:
    assert is_narration(sentence) is True


@pytest.mark.parametrize(
    "sentence",
    [
        "The law does not state a limitation period.",
        "The sections do not state when the notice takes effect.",
        "The period is not stated.",
        "The Act does not specify the applicable rate.",
        "The rules do not specify the amount.",
        "There is no information on redundancy payments.",
        "Costs are not mentioned in this section.",
        "The provisions do not mention compensation.",
        "Section 124 does not mention the cap.",
        "The statute does not say what applies.",
        "The section does not say whether interest accrues.",
    ],
)
def test_absence_phrases_without_cited_match_are_narration(sentence: str) -> None:
    assert is_narration(sentence) is True
    assert is_narration(sentence, cited_texts=["The limitation period is six years."]) is True


def test_absence_phrase_excused_when_in_cited_text() -> None:
    cited = "Subsection (2) applies where the contract does not specify a completion date."
    sentence = "The section applies where the agreement does not specify a completion date."
    assert is_narration(sentence, cited_texts=[cited]) is False


def test_absence_phrase_excused_case_insensitive() -> None:
    cited = "WHERE THIS SECTION DOES NOT SPECIFY A PROCEDURE, THE GENERAL RULES APPLY."
    sentence = "The section does not specify a procedure."
    assert is_narration(sentence, cited_texts=[cited]) is False


def test_does_not_apply_and_does_not_contain_are_not_narration() -> None:
    assert is_narration("Section 2 does not apply to claims founded on fraud.") is False
    assert is_narration("The provision does not contain an exemption.") is False


def test_normal_supported_sentence_is_not_narration() -> None:
    sentence = (
        "Section 2 of the Limitation Act 1980 sets six years from the date on "
        "which the cause of action accrued."
    )
    assert is_narration(sentence) is False


def test_all_input_phrases_registered() -> None:
    expected = {
        "provided passage",
        "the passages",
        "passages provided",
        "provisions provided",
        "text provided",
        "the excerpt",
        "the extract",
    }
    assert set(INPUT_PHRASES) == expected


def test_all_absence_phrases_registered() -> None:
    expected = {
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
    }
    assert set(ABSENCE_PHRASES) == expected
    assert "does not apply" not in ABSENCE_PHRASES
    assert "does not contain" not in ABSENCE_PHRASES
