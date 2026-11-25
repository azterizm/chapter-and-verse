"""Tests for S5.1b: judge on cited passages only (R1, R2, R4)."""

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
    VERIFIER_CONFIGS,
    build_policy,
    verifier_config,
)
from chapterverse.audit.verify import AuditVerifier, make_verifier
from chapterverse.contracts import JudgeReply, Policy, ProviderCall


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


def _cited_policy() -> Policy:
    return build_policy("qwen27-ar4", verifier_config_name=CITED_PASSAGES_CONFIG)


def _statute_premise() -> Premise:
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
        text="(3) A qualifying employee must have at least 2 years of continuous service.",
        coordinate="uk/ukpga/1996/18/s119/3",
        kind=PassageKind.PROVISION,
        heading="Employment Rights Act 1996, section 119(3)",
    )
    fact = Passage(
        text="Employment Rights Act 1996, section 119 has been in force since 1996.",
        coordinate=None,
        kind=PassageKind.FACT,
        heading="Employment Rights Act 1996, section 119",
    )
    return Premise(
        passages=(p1, p2, p3, fact),
        citations=frozenset({"s119/1", "s119/2", "s119/3"}),
        titles=("Employment Rights Act 1996",),
    )


def _passages_block(user_prompt: str) -> str:
    head = user_prompt.split("Answer so far.", 1)[0]
    return head.removeprefix("Passages:\n").strip()


# ---------------------------------------------------------------------------- R1: kept candidates


def test_cited_judge_keeps_only_cited_window_and_elements_numbered_from_1() -> None:
    premise = _statute_premise()
    chat = RecordingChat([{"label": "supported", "passage": 1}])
    calls: list[ProviderCall] = []
    verifier = make_verifier(chat, _cited_policy(), ["P1", "P2", "P3"], calls)

    verdict = verifier.check_sentence(premise, "The maximum week's pay is £700 [P2].")
    assert verdict.verdict is Verdict.EMIT
    assert len(chat.prompts) == 1
    block = _passages_block(chat.prompts[0])
    assert "[1] Employment Rights Act 1996, section 119(2): (2) The maximum" in block
    assert (
        "[2] Employment Rights Act 1996, section 119(2): A week's pay cannot exceed £700." in block
    )
    assert "[3]" not in block
    assert "basic award" not in block
    assert "continuous service" not in block
    assert "in force since 1996" not in block
    assert verdict.nli is not None
    assert verdict.nli.window == 1
    assert verdict.aligned == (1,)


def test_cited_judge_keeps_joined_candidate_starting_at_cited_window() -> None:
    premise = _statute_premise()
    # P1 cites subsection (2), so _nli_candidates builds a joined (P1, P2) candidate.
    chat = RecordingChat([{"label": "supported", "passage": 2}])
    calls: list[ProviderCall] = []
    verifier = make_verifier(chat, _cited_policy(), ["P1", "P2", "P3"], calls)

    verdict = verifier.check_sentence(
        premise, "An employee receives a basic award up to £700 [P1]."
    )
    assert verdict.verdict is Verdict.EMIT
    block = _passages_block(chat.prompts[0])
    assert "[1] Employment Rights Act 1996, section 119(1):" in block
    assert "[2] Employment Rights Act 1996, section 119(1):" in block
    assert "£700" in block
    assert "[3]" not in block
    assert "continuous service" not in block
    assert verdict.nli is not None
    assert verdict.nli.window == 0
    assert verdict.aligned == (0, 1)


def test_cited_judge_matches_full_claim_plus_kept_nli_premise() -> None:
    premise = _statute_premise()
    # Cite P3; judge contradicts passage 1 (which is P3 -> premise index 2).
    chat_cited = RecordingChat([{"label": "contradicted", "passage": 1}])
    v_cited = AuditVerifier(Judge(chat_cited), ["P1", "P2", "P3"], STRICT_FIGURES, cited_only=True)
    got = v_cited.check_sentence(premise, "Employees only need 1 year of continuous service [P3].")

    # Equivalent premise with only the kept candidate (P3) for the entailment check:
    chat_ref = RecordingChat([{"label": "contradicted", "passage": 1}])
    ref_premise = Premise(
        passages=(premise.passages[2],),
        citations=premise.citations,
        titles=premise.titles,
    )
    ref = Verifier(Judge(chat_ref), STRICT_FIGURES).check_sentence(
        ref_premise, "Employees only need 1 year of continuous service."
    )

    assert got.verdict == ref.verdict
    assert got.reasons == ref.reasons
    assert got.repair == ref.repair
    assert got.nli is not None
    assert got.nli.window == 2


def test_not_stated_still_points_to_cited_window_not_index_0() -> None:
    premise = _statute_premise()
    chat = RecordingChat([{"label": "not_stated", "passage": 0}])
    calls: list[ProviderCall] = []
    verifier = make_verifier(chat, _cited_policy(), ["P1", "P2", "P3"], calls)

    verdict = verifier.check_sentence(premise, "The claimant receives a bonus [P3].")
    assert verdict.verdict is Verdict.ROLLBACK
    assert verdict.reasons in ((Reason.NLI_NOT_ENTAILED,), (Reason.NEUTRAL_STRICT,))
    assert verdict.nli is not None
    assert verdict.nli.window == 2
    assert verdict.aligned == (2,)


def test_no_cited_passage_reaching_judge_falls_back_and_logs_once_without_sentence(
    caplog: Any,
) -> None:
    premise = _statute_premise()
    # Two-clause non-checkable sentence with no [P#]: passes check_refs and calls score() twice.
    chat = RecordingChat(
        [{"label": "supported", "passage": 1}, {"label": "supported", "passage": 1}]
    )
    calls: list[ProviderCall] = []
    verifier = make_verifier(chat, _cited_policy(), ["P1", "P2", "P3"], calls)

    sentence = "The statement holds; the rule applies."
    with caplog.at_level(logging.WARNING, logger="chapterverse.audit.verify"):
        verdict = verifier.check_sentence(premise, sentence)

    assert verdict.verdict is Verdict.EMIT
    assert len(chat.prompts) == 2
    # All candidates were sent (including P1, P2, P3, joined, elements, fact).
    block = _passages_block(chat.prompts[0])
    assert "[1]" in block
    assert "[2]" in block
    assert "[3]" in block
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    msg = warnings[0].getMessage()
    assert "statement holds" not in msg
    assert "rule applies" not in msg


def test_claim_checks_still_see_uncited_window_figure() -> None:
    p1 = Passage(
        text="(1) Compensation may be awarded to a dismissed employee.",
        coordinate="uk/ukpga/1996/18/s123/1",
        kind=PassageKind.PROVISION,
        heading="Employment Rights Act 1996, section 123(1)",
    )
    p2 = Passage(
        text="(2) The compensatory award shall not exceed £500.",
        coordinate="uk/ukpga/1996/18/s123/2",
        kind=PassageKind.PROVISION,
        heading="Employment Rights Act 1996, section 123(2)",
    )
    premise = Premise(
        passages=(p1, p2),
        citations=frozenset({"s123/1", "s123/2"}),
        titles=("Employment Rights Act 1996",),
    )

    chat = RecordingChat([{"label": "supported", "passage": 1}])
    calls: list[ProviderCall] = []
    verifier = make_verifier(chat, _cited_policy(), ["P1", "P2"], calls)

    verdict = verifier.check_sentence(premise, "An employee may receive up to £500 [P1].")
    assert verdict.verdict is Verdict.EMIT
    assert verdict.claims[0].grounded_by is GroundedBy.PREMISE
    # Judge only saw P1, never P2:
    assert "£500" not in _passages_block(chat.prompts[0])


# ---------------------------------------------------------------------------- R2: policy


def test_policy_names_hashes_and_make_verifier_scope() -> None:
    assert ALL_PASSAGES_CONFIG == "STRICT_FIGURES"
    assert CITED_PASSAGES_CONFIG == "STRICT_FIGURES+CITED_JUDGE"
    assert VERIFIER_CONFIGS[ALL_PASSAGES_CONFIG] is STRICT_FIGURES
    assert VERIFIER_CONFIGS[CITED_PASSAGES_CONFIG] is STRICT_FIGURES

    default_policy = build_policy("qwen27-ar4")
    cited_policy = _cited_policy()
    all_policy = build_policy("qwen27-ar4", verifier_config_name=ALL_PASSAGES_CONFIG)
    # The held-out gate failed (reports/heldout-qwen-modal-cited.md): the default stays all.
    assert default_policy == all_policy
    assert cited_policy.verifier_config_name == CITED_PASSAGES_CONFIG
    assert all_policy.verifier_config_name == ALL_PASSAGES_CONFIG
    assert verifier_config(cited_policy) is STRICT_FIGURES
    assert verifier_config(all_policy) is STRICT_FIGURES
    assert cited_policy.policy_hash != all_policy.policy_hash

    premise = _statute_premise()
    all_chat = RecordingChat([{"label": "supported", "passage": 2}])
    cited_chat = RecordingChat([{"label": "supported", "passage": 1}])
    v_all = make_verifier(all_chat, all_policy, ["P1", "P2", "P3"], [])
    v_cited = make_verifier(cited_chat, cited_policy, ["P1", "P2", "P3"], [])

    v_all.check_sentence(premise, "The maximum week's pay is £700 [P2].")
    v_cited.check_sentence(premise, "The maximum week's pay is £700 [P2].")
    assert "[3]" in _passages_block(all_chat.prompts[0])
    assert "[3]" not in _passages_block(cited_chat.prompts[0])
