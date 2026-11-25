"""Tests for S5.1b2: judge on cited sections (R1, R2, R5)."""

from __future__ import annotations

import json
import logging
from typing import Any

from legal_rag_verifier.judge import STRICT_FIGURES, Judge
from legal_rag_verifier.premise import Passage, PassageKind, Premise
from legal_rag_verifier.verifier import GroundedBy, Reason, Verdict, Verifier

from chapterverse.audit.policy import (
    ALL_PASSAGES_CONFIG,
    CITED_PASSAGES_CONFIG,
    SECTION_PASSAGES_CONFIG,
    VERIFIER_CONFIGS,
    build_policy,
    verifier_config,
)
from chapterverse.audit.verify import AuditVerifier, cited_nli_scorer, make_verifier
from chapterverse.contracts import JudgeReply, Policy, ProviderCall
from chapterverse.sections import section_coordinate


class RecordingChat:
    """Fake JudgeChat that records prompts sent to the judge and replies with a fixed script."""

    model = "fake/judge"

    def __init__(self, script: list[dict[str, Any]] | None = None) -> None:
        self.script = list(script or [])
        self.prompts: list[str] = []

    def __call__(self, messages: list[dict[str, str]], /) -> JudgeReply:
        self.prompts.append(messages[1]["content"])
        reply = self.script.pop(0) if self.script else {"label": "supported", "passage": 1}
        return {"content": json.dumps(reply), "ms": 1.0, "tokens": 5, "finish": "stop"}


def _section_policy() -> Policy:
    return build_policy("qwen27-ar4", verifier_config_name=SECTION_PASSAGES_CONFIG)


def _multi_section_premise() -> Premise:
    p1 = Passage(
        text="(1) Subject to subsection (2), an employee is entitled to a basic award.",
        coordinate="uk/ukpga/1996/18/s119/1",
        kind=PassageKind.PROVISION,
        heading="Employment Rights Act 1996, section 119(1)",
    )
    p2 = Passage(
        text="(2) The maximum amount of a week's pay is £700.",
        coordinate="uk/ukpga/1996/18/s119/2",
        kind=PassageKind.PROVISION,
        heading="Employment Rights Act 1996, section 119(2)",
        elements=("A week's pay cannot exceed £700.",),
    )
    p3 = Passage(
        text="(1) The compensatory award shall not exceed £500.",
        coordinate="uk/ukpga/1996/18/s124/1",
        kind=PassageKind.PROVISION,
        heading="Employment Rights Act 1996, section 124(1)",
    )
    fact = Passage(
        text="Employment Rights Act 1996, section 119 has been in force since 1996.",
        coordinate=None,
        kind=PassageKind.FACT,
        heading="Employment Rights Act 1996, section 119",
    )
    return Premise(
        passages=(p1, p2, p3, fact),
        citations=frozenset({"s119/1", "s119/2", "s124/1"}),
        titles=("Employment Rights Act 1996",),
    )


def _single_section_premise() -> Premise:
    p1 = Passage(
        text="(1) An employee is entitled to a basic award.",
        coordinate="uk/ukpga/1996/18/s119/1",
        kind=PassageKind.PROVISION,
        heading="Employment Rights Act 1996, section 119(1)",
    )
    p2 = Passage(
        text="(2) The maximum amount of a week's pay is £700.",
        coordinate="uk/ukpga/1996/18/s119/2",
        kind=PassageKind.PROVISION,
        heading="Employment Rights Act 1996, section 119(2)",
        elements=("A week's pay cannot exceed £700.",),
    )
    return Premise(
        passages=(p1, p2),
        citations=frozenset({"s119/1", "s119/2"}),
        titles=("Employment Rights Act 1996",),
    )


def _passages_block(user_prompt: str) -> str:
    head = user_prompt.split("Answer so far.", 1)[0]
    return head.removeprefix("Passages:\n").strip()


# ---------------------------------------------------------------------------- R1/R5: section helper


def test_section_coordinate_acts_sis_regnal_and_schedules() -> None:
    # Acts
    assert section_coordinate("uk/ukpga/1996/18/s124/1ZA/a") == "uk/ukpga/1996/18/s124"
    assert section_coordinate("uk/ukpga/1996/18/s124") == "uk/ukpga/1996/18/s124"
    # SIs (uksi, wsi, ssi)
    assert section_coordinate("uk/uksi/2011/3006/reg4/1/a") == "uk/uksi/2011/3006/reg4"
    assert section_coordinate("uk/wsi/2013/2729/art5/2") == "uk/wsi/2013/2729/art5"
    assert section_coordinate("uk/ssi/2020/1/r3/1") == "uk/ssi/2020/1/r3"
    # Regnal-year Acts
    assert section_coordinate("uk/ukpga/Vict/24-25/100/s18/1") == "uk/ukpga/Vict/24-25/100/s18"
    assert (
        section_coordinate("uk/ukpga/Edw7and1Geo5/10/15/s1/a") == "uk/ukpga/Edw7and1Geo5/10/15/s1"
    )
    # Schedules: schN plus next segment; bare schN stays as itself
    assert section_coordinate("uk/ukpga/1996/18/sch1/para2/a") == "uk/ukpga/1996/18/sch1/para2"
    assert section_coordinate("uk/ukpga/1996/18/sch/para10/1") == "uk/ukpga/1996/18/sch/para10"
    assert section_coordinate("uk/ukpga/1996/18/sch1") == "uk/ukpga/1996/18/sch1"
    assert (
        section_coordinate("uk/ukpga/Vict/24-25/100/sch1/para2/1")
        == "uk/ukpga/Vict/24-25/100/sch1/para2"
    )
    # No provision part: own section
    assert section_coordinate("uk/ukpga/1996/18") == "uk/ukpga/1996/18"
    assert section_coordinate("uk/ukpga/Vict/24-25/100") == "uk/ukpga/Vict/24-25/100"


# ---------------------------------------------------------------------------- R1: kept candidates


def test_section_judge_keeps_all_windows_of_cited_section_numbered_from_1() -> None:
    premise = _multi_section_premise()
    # Citing P1 (s119/1) keeps P1 (s119/1), P2 (s119/2), joined (P1, P2), and P2's element.
    # P3 (s124/1) and FACT (coordinate=None) are dropped.
    chat = RecordingChat([{"label": "supported", "passage": 2}])
    calls: list[ProviderCall] = []
    verifier = make_verifier(chat, _section_policy(), ["P1", "P2", "P3"], calls)

    verdict = verifier.check_sentence(premise, "The maximum week's pay is £700 [P1].")
    assert verdict.verdict is Verdict.EMIT
    assert len(chat.prompts) == 1
    block = _passages_block(chat.prompts[0])
    assert "[1] Employment Rights Act 1996, section 119(1):" in block
    assert "[2] Employment Rights Act 1996, section 119(2):" in block
    assert "[3] Employment Rights Act 1996, section 119(1):" in block
    assert (
        "[4] Employment Rights Act 1996, section 119(2): A week's pay cannot exceed £700." in block
    )
    assert "[5]" not in block
    assert "compensatory award" not in block
    assert "£500" not in block
    assert "in force since 1996" not in block
    assert verdict.nli is not None
    assert verdict.nli.window == 1
    assert verdict.aligned == (1,)


def test_single_section_premise_gives_exact_all_scope_judge_prompt() -> None:
    premise = _single_section_premise()
    all_chat = RecordingChat([{"label": "supported", "passage": 1}])
    sec_chat = RecordingChat([{"label": "supported", "passage": 1}])
    all_policy = build_policy("qwen27-ar4", verifier_config_name=ALL_PASSAGES_CONFIG)
    v_all = make_verifier(all_chat, all_policy, ["P1", "P2"], [])
    v_sec = make_verifier(sec_chat, _section_policy(), ["P1", "P2"], [])

    v_all.check_sentence(premise, "An employee receives a basic award [P1].")
    v_sec.check_sentence(premise, "An employee receives a basic award [P1].")

    assert sec_chat.prompts == all_chat.prompts


def test_section_judge_matches_full_claim_plus_kept_nli_premise() -> None:
    premise = _multi_section_premise()
    # Cite P3 (s124/1, index 2); judge contradicts passage 1 (which is P3 -> index 2).
    chat_sec = RecordingChat([{"label": "contradicted", "passage": 1}])
    v_sec = AuditVerifier(
        Judge(chat_sec), ["P1", "P2", "P3"], STRICT_FIGURES, judge_scope="section"
    )
    got = v_sec.check_sentence(premise, "The compensatory award shall not exceed £900 [P3].")

    chat_ref = RecordingChat([{"label": "contradicted", "passage": 1}])
    ref_premise = Premise(
        passages=(premise.passages[2],),
        citations=premise.citations,
        titles=premise.titles,
    )
    ref = Verifier(Judge(chat_ref), STRICT_FIGURES).check_sentence(
        ref_premise, "The compensatory award shall not exceed £900."
    )

    assert got.verdict == ref.verdict
    assert got.reasons == ref.reasons
    assert got.repair == ref.repair
    assert got.nli is not None
    assert got.nli.window == 2


def test_section_not_stated_points_to_kept_window_not_index_0() -> None:
    premise = _multi_section_premise()
    chat = RecordingChat([{"label": "not_stated", "passage": 0}])
    verifier = make_verifier(chat, _section_policy(), ["P1", "P2", "P3"], [])

    verdict = verifier.check_sentence(premise, "The claimant receives a bonus [P3].")
    assert verdict.verdict is Verdict.ROLLBACK
    assert verdict.reasons in ((Reason.NLI_NOT_ENTAILED,), (Reason.NEUTRAL_STRICT,))
    assert verdict.nli is not None
    assert verdict.nli.window == 2
    assert verdict.aligned == (2,)


def test_section_no_cited_passage_falls_back_and_logs_once_without_sentence(
    caplog: Any,
) -> None:
    premise = _multi_section_premise()
    chat = RecordingChat(
        [{"label": "supported", "passage": 1}, {"label": "supported", "passage": 1}]
    )
    verifier = make_verifier(chat, _section_policy(), ["P1", "P2", "P3"], [])

    sentence = "The statement holds; the rule applies."
    with caplog.at_level(logging.WARNING, logger="chapterverse.audit.verify"):
        verdict = verifier.check_sentence(premise, sentence)

    assert verdict.verdict is Verdict.EMIT
    assert len(chat.prompts) == 2
    block = _passages_block(chat.prompts[0])
    assert "compensatory award" in block
    assert "in force since 1996" in block
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "statement holds" not in msg
    assert "rule applies" not in msg


def test_section_claim_checks_still_see_uncited_section_figure() -> None:
    p1 = Passage(
        text="(1) Compensation may be awarded to a dismissed employee.",
        coordinate="uk/ukpga/1996/18/s119/1",
        kind=PassageKind.PROVISION,
        heading="Employment Rights Act 1996, section 119(1)",
    )
    p2 = Passage(
        text="(2) An employee must apply within the prescribed period.",
        coordinate="uk/ukpga/1996/18/s119/2",
        kind=PassageKind.PROVISION,
        heading="Employment Rights Act 1996, section 119(2)",
    )
    p3 = Passage(
        text="(1) The compensatory award shall not exceed £500.",
        coordinate="uk/ukpga/1996/18/s124/1",
        kind=PassageKind.PROVISION,
        heading="Employment Rights Act 1996, section 124(1)",
    )
    premise = Premise(
        passages=(p1, p2, p3),
        citations=frozenset({"s119/1", "s119/2", "s124/1"}),
        titles=("Employment Rights Act 1996",),
    )
    chat = RecordingChat([{"label": "supported", "passage": 1}])
    verifier = make_verifier(chat, _section_policy(), ["P1", "P2", "P3"], [])

    verdict = verifier.check_sentence(premise, "An employee may receive up to £500 [P1].")
    assert verdict.verdict is Verdict.EMIT
    assert verdict.claims[0].grounded_by is GroundedBy.PREMISE
    assert "£500" not in _passages_block(chat.prompts[0])


def test_windows_without_coordinate_kept_only_when_cited_window_has_none() -> None:
    p_coord = Passage(
        text="(1) Statutory rule alpha.",
        coordinate="uk/ukpga/1996/18/s119/1",
        kind=PassageKind.PROVISION,
        heading="Act, s.119(1)",
    )
    f1 = Passage(
        text="Fact one without coordinate.",
        coordinate=None,
        kind=PassageKind.FACT,
        heading="Fact 1",
    )
    f2 = Passage(
        text="Fact two without coordinate.",
        coordinate=None,
        kind=PassageKind.FACT,
        heading="Fact 2",
    )
    premise = Premise(passages=(p_coord, f1, f2))

    # Citing f1 (index 1, coordinate=None) keeps f1 and f2, drops p_coord.
    chat_none = RecordingChat([{"label": "supported", "passage": 1}])
    scorer_none = cited_nli_scorer(
        Judge(chat_none), premise, "Fact one holds.", [1], scope="section"
    )
    v_none = Verifier(scorer_none, STRICT_FIGURES)
    v_none.check_sentence(premise, "Fact one holds.")
    block_none = _passages_block(chat_none.prompts[0])
    assert "Fact one without coordinate" in block_none
    assert "Fact two without coordinate" in block_none
    assert "Statutory rule alpha" not in block_none

    # Citing p_coord (index 0) drops both f1 and f2.
    chat_coord = RecordingChat([{"label": "supported", "passage": 1}])
    scorer_coord = cited_nli_scorer(
        Judge(chat_coord), premise, "Statutory rule alpha.", [0], scope="section"
    )
    v_coord = Verifier(scorer_coord, STRICT_FIGURES)
    v_coord.check_sentence(premise, "Statutory rule alpha.")
    block_coord = _passages_block(chat_coord.prompts[0])
    assert "Statutory rule alpha" in block_coord
    assert "Fact one" not in block_coord
    assert "Fact two" not in block_coord


# ---------------------------------------------------------------------------- R2: policy


def test_section_policy_constant_hash_and_default_unchanged() -> None:
    assert SECTION_PASSAGES_CONFIG == "STRICT_FIGURES+SECTION_JUDGE"
    assert VERIFIER_CONFIGS[SECTION_PASSAGES_CONFIG] is STRICT_FIGURES

    default_policy = build_policy("qwen27-ar4")
    all_policy = build_policy("qwen27-ar4", verifier_config_name=ALL_PASSAGES_CONFIG)
    cited_policy = build_policy("qwen27-ar4", verifier_config_name=CITED_PASSAGES_CONFIG)
    section_policy = _section_policy()

    assert default_policy == all_policy
    assert default_policy.verifier_config_name == "STRICT_FIGURES"
    assert default_policy.policy_hash.startswith("235ec4a6")
    assert verifier_config(section_policy) is STRICT_FIGURES
    assert section_policy.policy_hash != all_policy.policy_hash
    assert section_policy.policy_hash != cited_policy.policy_hash
