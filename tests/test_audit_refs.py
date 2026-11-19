"""Passage references and model-written citations (agent B4)."""

from __future__ import annotations

import pytest
from legal_rag_verifier.judge import STRICT_FIGURES, Judge
from legal_rag_verifier.premise import Premise

from chapterverse.audit.refs import (
    MALFORMED_REF,
    MODEL_CITATION,
    NO_PASSAGE_REF,
    UNKNOWN_PASSAGE,
    check_refs,
    model_citations,
    split_refs,
)
from chapterverse.audit.verify import AuditVerifier
from chapterverse.contracts import TextStore

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


# ============================================================================
# Grounded references tests (R1.1 - R1.5) using ERA s.124(1A)
# ============================================================================

ERA_S124_1A_TEXT = (
    "Subsection (1) shall not apply to compensation awarded, or a compensatory award made, "
    "to a person in a case where he is regarded as unfairly dismissed by virtue of "
    "section 100, 103A, 105(3) or 105(6A)."
)
ERA_S124_1ZA_TEXT = (
    "(1ZA) The amount specified in this subsection is the lower of- (a) £123,543, "
    "and (b) 52 multiplied by a week's pay of the person concerned."
)


@pytest.mark.parametrize(
    "sentence",
    [
        "Under section 100 the compensatory award limit does not apply [P1].",
        "Under s.100 the compensatory award limit does not apply [P1].",
        "Under s 100 the compensatory award limit does not apply [P1].",
        "Under section 103A the limit does not apply [P1].",
        "The limit does not apply where dismissal is by virtue of section 105(3) [P1].",
        "The limit does not apply where dismissal is by virtue of section 105(6A) [P1].",
        "Subsection (1) does not apply to this compensation [P1].",
    ],
)
def test_grounded_references_in_cited_passage_pass(sentence: str) -> None:
    cited = {"P1": ERA_S124_1A_TEXT, "P2": ERA_S124_1ZA_TEXT}
    check = check_refs(sentence, KNOWN, cited_texts=cited)
    assert check.ok
    assert check.passage_ids == ("P1",)


def test_grounded_relative_reference_passes() -> None:
    text = "The amount in subsection (1ZA) applies to the compensatory award."
    cited = {"P1": "The amount specified in subsection (1ZA) is the limit."}
    check = check_refs(f"{text} [P1].", KNOWN, cited_texts=cited)
    assert check.ok
    assert check.text == f"{text}."


@pytest.mark.parametrize(
    "sentence",
    [
        "Under section 999 the limit does not apply [P1].",
        "Under section 100 the limit does not apply [P2].",  # P2 does not have s100
        "Section 100 applies [P1], but section 999 does not [P1].",
        "Regulation 5 applies [P1].",
    ],
)
def test_ungrounded_reference_fails(sentence: str) -> None:
    cited = {"P1": ERA_S124_1A_TEXT, "P2": ERA_S124_1ZA_TEXT}
    check = check_refs(sentence, KNOWN, cited_texts=cited)
    assert not check.ok
    assert MODEL_CITATION in [f.code for f in check.failures]


@pytest.mark.parametrize(
    "sentence",
    [
        "SI 2024/123 applies to the award [P1].",
        "The coordinate uk/ukpga/1996/18/s124 sets the limit [P1].",
        "See legislation.gov.uk/ukpga/1996/18/section/124 for the limit [P1].",
    ],
)
def test_si_coordinate_and_url_fail_even_when_in_passage(sentence: str) -> None:
    passage_with_all = (
        f"{ERA_S124_1A_TEXT} See SI 2024/123 and uk/ukpga/1996/18/s124 "
        "at legislation.gov.uk/ukpga/1996/18/section/124."
    )
    cited = {"P1": passage_with_all}
    check = check_refs(sentence, KNOWN, cited_texts=cited)
    assert not check.ok
    assert MODEL_CITATION in [f.code for f in check.failures]


def test_grounded_reference_with_unknown_or_malformed_passage_fails() -> None:
    cited = {"P1": ERA_S124_1A_TEXT}
    check_unknown = check_refs(
        "Under section 100 the limit does not apply [P9].", KNOWN, cited_texts=cited
    )
    assert not check_unknown.ok
    assert UNKNOWN_PASSAGE in [f.code for f in check_unknown.failures]

    check_malformed = check_refs(
        "Under section 100 the limit does not apply (P1).", KNOWN, cited_texts=cited
    )
    assert not check_malformed.ok
    assert MALFORMED_REF in [f.code for f in check_malformed.failures]


def test_list_passes_only_if_every_item_is_grounded() -> None:
    cited = {"P1": ERA_S124_1A_TEXT}
    # All items grounded
    pass_1 = check_refs(
        "The limit does not apply under sections 100, 103A or 105(3) [P1].",
        KNOWN,
        cited_texts=cited,
    )
    assert pass_1.ok

    pass_2 = check_refs(
        "The limit does not apply under section 100, 103A, 105(3) or 105(6A) [P1].",
        KNOWN,
        cited_texts=cited,
    )
    assert pass_2.ok

    # One item ungrounded
    fail_1 = check_refs(
        "The limit does not apply under sections 100, 103A or 999 [P1].",
        KNOWN,
        cited_texts=cited,
    )
    assert not fail_1.ok
    assert MODEL_CITATION in [f.code for f in fail_1.failures]


def test_audit_verifier_passes_grounded_reference(fixture_text_store: object) -> None:
    assert isinstance(fixture_text_store, TextStore)
    records = fixture_text_store.get("uk/ukpga/1996/18/s124")
    premise = Premise.from_records(records)

    class DummyChat:
        model = "dummy"

        def __call__(self, messages: list[dict[str, str]]) -> dict[str, object]:
            return {"content": "{}", "ms": 1.0, "tokens": 1, "finish": "stop"}

    verifier = AuditVerifier(
        Judge(DummyChat()), ["P1", "P2", "P3", "P4", "P5", "P6"], STRICT_FIGURES
    )
    # P3 is s.124(1A) which mentions section 100
    v_bad = verifier.check_sentence(premise, "Under section 999 the limit does not apply [P3].")
    assert any("MODEL_CITATION: section 999" in str(d) for d in v_bad.details)

    v_good = verifier.check_sentence(
        premise, "Under section 100 the compensatory award limit does not apply [P3]."
    )
    assert not any("MODEL_CITATION" in str(d) for d in v_good.details)
