"""Passage references and model-written citations (agent B4)."""

from __future__ import annotations

import pytest

from chapterverse.audit.refs import (
    MALFORMED_REF,
    MODEL_CITATION,
    NO_PASSAGE_REF,
    UNKNOWN_PASSAGE,
    check_refs,
    model_citations,
    split_refs,
)

KNOWN = frozenset({"P1", "P2", "P3"})


@pytest.mark.parametrize(
    ("sentence", "text", "ids"),
    [
        ("The cap is £5 [P1].", "The cap is £5.", ("P1",)),
        ("The cap is £5 [P1, P3].", "The cap is £5.", ("P1", "P3")),
        ("The cap is £5 [P1][P2].", "The cap is £5.", ("P1", "P2")),
        ("The cap is £5 [P1] [P2].", "The cap is £5.", ("P1", "P2")),
        ("The cap is £5 [ p01 ; P3 ].", "The cap is £5.", ("P1", "P3")),
        ("It applies [P2], and it ends [P2].", "It applies, and it ends.", ("P2",)),
        ("The cap is £5 [P1 and P2].", "The cap is £5.", ("P1", "P2")),
        ("No reference here.", "No reference here.", ()),
    ],
)
def test_split_refs_strips_and_collects(sentence: str, text: str, ids: tuple[str, ...]) -> None:
    assert split_refs(sentence) == (text, ids)


def test_known_references_pass() -> None:
    check = check_refs("The award cannot exceed £123,543 [P2, P1].", KNOWN)
    assert check.ok
    assert check.text == "The award cannot exceed £123,543."
    assert check.passage_ids == ("P2", "P1")


def test_an_unknown_passage_id_fails() -> None:
    check = check_refs("The award cannot exceed £123,543 [P2, P9].", KNOWN)
    assert [f.code for f in check.failures] == [UNKNOWN_PASSAGE]
    assert check.failures[0].detail == "P9"
    assert not check.ok
    assert [f.code for f in check_refs("It is capped [P0].", KNOWN).failures] == [UNKNOWN_PASSAGE]


@pytest.mark.parametrize(
    "sentence",
    [
        "Under section 124(1ZA) the award cannot exceed £123,543 [P2].",
        "Under s.124 the award is capped [P1].",
        "Regulation 5 sets the rate [P1].",
        "Article 3 applies [P1].",
        "The amount in subsection (1ZA) applies [P1].",
        "Paragraph (a) sets a figure [P2].",
        "SI 2024/123 sets the figure [P2].",
        "The provision uk/ukpga/1996/18/s124 sets a limit [P1].",
        "See legislation.gov.uk/ukpga/1996/18/section/124 for the limit [P1].",
        "There is no section 342 [P1].",
    ],
)
def test_a_citation_written_by_the_model_fails(sentence: str) -> None:
    check = check_refs(sentence, KNOWN)
    assert MODEL_CITATION in [f.code for f in check.failures]
    assert check.passage_ids  # the reference itself was fine


@pytest.mark.parametrize(
    "sentence",
    [
        "The Employment Rights Act 1996 caps the compensatory award [P2].",
        "ERA 1996 caps the compensatory award [P2].",
        "The limit does not apply in some unfair dismissal cases [P3].",
    ],
)
def test_an_instrument_name_in_prose_is_left_to_the_verifier(sentence: str) -> None:
    assert check_refs(sentence, KNOWN).ok
    assert model_citations(split_refs(sentence)[0]) == ()


def test_a_malformed_reference_fails() -> None:
    codes = [f.code for f in check_refs("The award is capped (P1).", KNOWN).failures]
    assert codes == [MALFORMED_REF]
    codes = [f.code for f in check_refs("The award is capped [P1.", KNOWN).failures]
    assert codes == [MALFORMED_REF]


@pytest.mark.parametrize(
    "sentence",
    [
        "The award cannot exceed £123,543.",
        "The Employment Rights Act 1996 caps the award.",
        "The employee may complain to a tribunal.",
        "It is 52 weeks' pay.",
    ],
)
def test_a_checkable_sentence_without_a_reference_fails(sentence: str) -> None:
    assert [f.code for f in check_refs(sentence, KNOWN).failures] == [NO_PASSAGE_REF]


@pytest.mark.parametrize("sentence", ["Yes.", "The passages do not say.", "In short, it does."])
def test_a_sentence_stating_nothing_checkable_needs_no_reference(sentence: str) -> None:
    check = check_refs(sentence, KNOWN)
    assert check.ok
    assert check.passage_ids == ()
