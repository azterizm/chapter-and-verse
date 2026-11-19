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


# ============================================================================
# Self references tests (specs/self-refs/requirements.md R1.1 - R1.4, R2.1)
# ============================================================================

ERA_S100_1_TEXT = (
    "(1) An employee who is dismissed shall be regarded for the purposes of this Part as "
    "unfairly dismissed if the reason (or, if more than one, the principal reason) for the "
    "dismissal is that— (a) having been designated by the employer to carry out activities in "
    "connection with preventing or reducing risks to health and safety at work, the employee "
    "carried out (or proposed to carry out) any such activities, (b) being a representative of "
    "workers on matters of health and safety at work or member of a safety committee— (i) in "
    "accordance with arrangements established under or by virtue of any enactment, or (ii) by "
    "reason of being acknowledged as such by the employer, the employee performed (or proposed "
    "to perform) any functions as such a representative or a member of such a committee, (c) "
    "being an employee at a place where— (i) there was no such representative or safety "
    "committee, or (ii) there was such a representative or safety committee but it was not "
    "reasonably practicable for the employee to raise the matter by those means, he brought to his "
    "employer's attention, by reasonable means, circumstances connected with his work which he "
    "reasonably believed were harmful or potentially harmful to health or safety, (d) in "
    "circumstances of danger which the employee reasonably believed to be serious and imminent "
    "and which he could not reasonably have been expected to avert, he left (or proposed to "
    "leave) or (while the danger persisted) refused to return to his place of work or any "
    "dangerous part of his place of work, or (e) in circumstances of danger which the employee "
    "reasonably believed to be serious and imminent, he took (or proposed to take) appropriate "
    "steps to protect himself or other persons from the danger."
)
ERA_S100_2_TEXT = (
    "(2) For the purposes of subsection (1)(e) whether steps which an employee took (or "
    "proposed to take) were appropriate is to be judged by reference to all the circumstances "
    "including, in particular, his knowledge and the facilities and advice available to him at "
    "the time."
)
ERA_S100_COORDS = {
    "P1": "uk/ukpga/1996/18/s100/1",
    "P2": "uk/ukpga/1996/18/s100/2",
}
ERA_S100_TEXTS = {
    "P1": ERA_S100_1_TEXT,
    "P2": ERA_S100_2_TEXT,
}


@pytest.mark.parametrize(
    "sentence",
    [
        "Under section 100, an employee who is dismissed is regarded as unfairly dismissed [P1].",
        "Under subsection (1), the employee is regarded as unfairly dismissed [P1].",
        "An employee is regarded as unfairly dismissed if the reason is one of those set out [P1].",
    ],
)
def test_self_reference_repro_three_sentences_pass(sentence: str) -> None:
    check = check_refs(
        sentence,
        KNOWN,
        cited_texts=ERA_S100_TEXTS,
        cited_coordinates=ERA_S100_COORDS,
    )
    assert check.ok


@pytest.mark.parametrize(
    "sentence",
    [
        "Under section 100(1), the employee is regarded as unfairly dismissed [P1].",
        "Under s.100, the employee is regarded as unfairly dismissed [P1].",
        "Under s 100, the employee is regarded as unfairly dismissed [P1].",
        "Under subsection (1), the employee is regarded as unfairly dismissed [P1].",
    ],
)
def test_self_reference_cited_provision_and_ancestors_pass(sentence: str) -> None:
    check = check_refs(
        sentence,
        KNOWN,
        cited_texts=ERA_S100_TEXTS,
        cited_coordinates=ERA_S100_COORDS,
    )
    assert check.ok


@pytest.mark.parametrize(
    ("sentence", "failure_snippet"),
    [
        (
            "Under subsection (2), the employee is regarded as unfairly dismissed [P1].",
            "subsection (2)",
        ),
        (
            "Under section 100(2), the employee is regarded as unfairly dismissed [P1].",
            "section 100(2)",
        ),
        (
            "Under paragraph (a), the employee carried out activities [P1].",
            "paragraph (a)",
        ),
    ],
)
def test_sibling_or_descendant_fails(sentence: str, failure_snippet: str) -> None:
    # Citing only P1 (s100(1))
    check = check_refs(
        sentence,
        KNOWN,
        cited_texts={"P1": ERA_S100_1_TEXT},
        cited_coordinates={"P1": "uk/ukpga/1996/18/s100/1"},
    )
    assert not check.ok
    assert any(f.code == MODEL_CITATION and failure_snippet in f.detail for f in check.failures)


def test_cited_sibling_passes() -> None:
    # When P2 (s100(2)) is cited, subsection (2) passes
    check = check_refs(
        "Under subsection (2), appropriateness is judged by all the circumstances [P2].",
        KNOWN,
        cited_texts=ERA_S100_TEXTS,
        cited_coordinates=ERA_S100_COORDS,
    )
    assert check.ok


@pytest.mark.parametrize(
    ("sentence", "failure_snippet"),
    [
        (
            (
                "Under section 98, an employee who is dismissed is regarded as unfairly "
                "dismissed [P1]."
            ),
            "section 98",
        ),
        (
            "Under section 94, an employee has the right not to be unfairly dismissed [P1].",
            "section 94",
        ),
    ],
)
def test_different_section_fails(sentence: str, failure_snippet: str) -> None:
    check = check_refs(
        sentence,
        KNOWN,
        cited_texts=ERA_S100_TEXTS,
        cited_coordinates=ERA_S100_COORDS,
    )
    assert not check.ok
    assert any(f.code == MODEL_CITATION and failure_snippet in f.detail for f in check.failures)


def test_relative_references_resolve_against_cited_section() -> None:
    # P2 is s.105(3). Subsection (1) cannot resolve against s.100 when only P2 is cited.
    coords = {"P1": "uk/ukpga/1996/18/s100/1", "P2": "uk/ukpga/1996/18/s105/3"}
    texts = {"P1": ERA_S100_1_TEXT, "P2": "Text of section 105(3)."}
    check = check_refs(
        "Under subsection (1), the employee is regarded as unfairly dismissed [P2].",
        KNOWN,
        cited_texts=texts,
        cited_coordinates=coords,
    )
    assert not check.ok
    assert any(f.code == MODEL_CITATION and "subsection (1)" in f.detail for f in check.failures)

    # Relative reference in sentence cannot resolve against un-cited section 98 mentioned in prose
    check_other = check_refs(
        "Under section 98, subsection (1) applies [P1].",
        KNOWN,
        cited_texts=texts,
        cited_coordinates=coords,
    )
    assert not check_other.ok
    assert any(f.code == MODEL_CITATION and "section 98" in f.detail for f in check_other.failures)


def test_audit_verifier_passes_self_reference(fixture_text_store: object) -> None:
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
    # P1 is s.124(1)
    v_s124 = verifier.check_sentence(premise, "Under section 124 the award is capped [P1].")
    assert not any("MODEL_CITATION" in str(d) for d in v_s124.details)

    v_sub1 = verifier.check_sentence(premise, "Under subsection (1) the award is capped [P1].")
    assert not any("MODEL_CITATION" in str(d) for d in v_sub1.details)

    # Subsection (2) does not exist in s.124 (only (1), (1ZA), (1A), etc.)
    v_sub2 = verifier.check_sentence(premise, "Under subsection (2) the award is capped [P1].")
    assert any("MODEL_CITATION: subsection (2)" in str(d) for d in v_sub2.details)
