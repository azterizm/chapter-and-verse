"""Unit tests for engine/chapterverse/pii.py."""

from __future__ import annotations

from typing import TYPE_CHECKING

from chapterverse.nlp import analyse
from chapterverse.pii import Masked, mask_pii

if TYPE_CHECKING:
    from legal_rag_router import Router

from pathlib import Path

import pytest


def _repo_vocab() -> frozenset[str]:
    p = Path("artefacts/symspell/dictionary.txt")
    if not p.is_file():
        pytest.skip("artefacts/symspell/dictionary.txt not present")
    words: set[str] = set()
    with p.open(encoding="utf-8") as f:
        for line in f:
            parts = line.strip().split()
            if parts:
                words.add(parts[0].lower())
    return frozenset(words)


def test_mask_pii_r4_acme_john_smith() -> None:
    text = "Can my employer Acme Ltd dismiss John Smith for whistleblowing?"
    res = analyse(text)
    masked = mask_pii(text, res)
    assert isinstance(masked, Masked)
    # Acme Ltd and John Smith must be masked
    assert "Acme Ltd" not in masked.text
    assert "John Smith" not in masked.text
    assert "[ORG 1]" in masked.text
    assert "[PERSON 1]" in masked.text
    # "whistleblowing" must be kept
    assert "whistleblowing" in masked.text
    assert masked.text == "Can my employer [ORG 1] dismiss [PERSON 1] for whistleblowing?"

    # Check reverse map
    assert masked.reverse_map["[ORG 1]"] == "Acme Ltd"
    assert masked.reverse_map["[PERSON 1]"] == "John Smith"
    # Unmasking restores the original text
    assert masked.unmask() == text


def test_mask_pii_r4_equality_act_untouched() -> None:
    text = "Under Equality Act 2010 s.39, discrimination in employment is unlawful."
    res = analyse(text)
    masked = mask_pii(text, res)
    assert "Equality Act 2010 s.39" in masked.text
    assert masked.text == text


def test_mask_pii_r4_patterns() -> None:
    text = (
        "Contact me at alice@test.co.uk or call 020 7946 0991. Postcode SW1A 1AA, NI QQ 12 34 56 A."
    )
    res = analyse(text)
    masked = mask_pii(text, res)
    assert "[EMAIL 1]" in masked.text
    assert "[PHONE 1]" in masked.text
    assert "[POSTCODE 1]" in masked.text
    assert "[NI 1]" in masked.text
    assert "alice@test.co.uk" not in masked.text
    assert "020 7946 0991" not in masked.text
    assert "SW1A 1AA" not in masked.text
    assert "QQ 12 34 56 A" not in masked.text

    assert masked.unmask() == text


def test_mask_pii_r4_repeated_names_share_placeholder() -> None:
    text = (
        "Can my employer Acme Ltd dismiss John Smith? "
        "John Smith reported safety concerns to Acme Ltd."
    )
    res = analyse(text)
    masked = mask_pii(text, res)
    assert "[ORG 1]" in masked.text
    assert "[PERSON 1]" in masked.text
    assert "[ORG 2]" not in masked.text
    assert "[PERSON 2]" not in masked.text
    assert masked.reverse_map["[ORG 1]"] == "Acme Ltd"
    assert masked.reverse_map["[PERSON 1]"] == "John Smith"
    assert masked.unmask() == text


def test_mask_pii_secretary_of_state_never_masked() -> None:
    text = "Can the Secretary of State intervene under the Employment Rights Act 1996?"
    res = analyse(text)
    masked = mask_pii(text, res)
    assert "Secretary of State" in masked.text
    assert "Employment Rights Act 1996" in masked.text
    assert masked.text == text


def test_mask_pii_protected_citation_spans() -> None:
    text = "Section 98 of Employment Rights Act 1996 applies to John Smith."
    cit = "Employment Rights Act 1996"
    s = text.index(cit)
    e = s + len(cit)
    res = analyse(text, protected_spans=[(s, e)])
    masked = mask_pii(text, res, protected_spans=[(s, e)])
    assert "Employment Rights Act 1996" in masked.text
    assert "[PERSON 1]" in masked.text
    assert masked.reverse_map["[PERSON 1]"] == "John Smith"


def test_mask_pii_fac_corporate_suffixes() -> None:
    vocab = _repo_vocab()
    # FAC ending in corporate suffix gets masked as ORG
    text = "The meeting took place at Apex Tower Ltd yesterday."
    res = analyse(text)
    masked = mask_pii(text, res, vocabulary=vocab)
    assert "[ORG 1]" in masked.text
    assert "Apex Tower Ltd" not in masked.text

    # FAC without corporate suffix should NOT be masked
    text_building = "The meeting took place at Big Ben yesterday."
    res_building = analyse(text_building)
    masked_building = mask_pii(text_building, res_building, vocabulary=vocab)
    assert "Big Ben" in masked_building.text


def test_mask_pii_multiple_distinct_entities() -> None:
    text = (
        "Alice Cooper at employer Acme Ltd and Bob Dylan at Beta Corp discussed the policy. "
        "Alice Cooper called Bob Dylan."
    )
    res = analyse(text)
    masked = mask_pii(text, res)
    assert "[PERSON 1]" in masked.text
    assert "[PERSON 2]" in masked.text
    assert "[ORG 1]" in masked.text
    assert "[ORG 2]" in masked.text
    assert masked.reverse_map["[PERSON 1]"] == "Alice Cooper"
    assert masked.reverse_map["[PERSON 2]"] == "Bob Dylan"
    assert masked.reverse_map["[ORG 1]"] == "Acme Ltd"
    assert masked.reverse_map["[ORG 2]"] == "Beta Corp"
    assert masked.unmask() == text


def test_mask_pii_with_router_index(fixture_router: Router) -> None:
    text = "Under Employment Rights Act 1996, John Smith was dismissed."
    res = analyse(text)
    masked = mask_pii(text, res, router_index=fixture_router.index)
    assert "Employment Rights Act 1996" in masked.text
    assert "[PERSON 1]" in masked.text
    assert masked.reverse_map["[PERSON 1]"] == "John Smith"


def test_mask_pii_empty() -> None:
    res = analyse("")
    masked = mask_pii("", res)
    assert masked.text == ""
    assert masked.reverse_map == {}
    assert masked.unmask() == ""


def test_mask_pii_r5_priya_nkemelu_and_stoplist() -> None:
    vocab = _repo_vocab()
    text = (
        "Can Priya Nkemelu claim redundancy pay from Zentrix Ltd at the "
        "Employment Tribunal in Scotland?"
    )
    res = analyse(text)
    masked = mask_pii(text, res, vocabulary=vocab)

    # Priya Nkemelu masked as NAME
    assert "Priya Nkemelu" not in masked.text
    assert "[NAME 1]" in masked.text
    assert masked.reverse_map["[NAME 1]"] == "Priya Nkemelu"

    # Zentrix Ltd masked as ORG
    assert "Zentrix Ltd" not in masked.text
    assert "[ORG 1]" in masked.text
    assert masked.reverse_map["[ORG 1]"] == "Zentrix Ltd"

    # Stoplist items kept
    assert "Employment Tribunal" in masked.text
    assert "Scotland" in masked.text
    assert masked.unmask() == text


def test_mask_pii_r5_repeated_fallback_names() -> None:
    vocab = _repo_vocab()
    text = "Can Priya Nkemelu claim redundancy from Zentrix Ltd? Priya Nkemelu reported concerns."
    res = analyse(text)
    masked = mask_pii(text, res, vocabulary=vocab)
    assert "[NAME 1]" in masked.text
    assert "[NAME 2]" not in masked.text
    assert masked.reverse_map["[NAME 1]"] == "Priya Nkemelu"
    assert masked.unmask() == text


def test_mask_pii_r6_email_and_ni_false_positives() -> None:
    text = "Email me at alice@test.co.uk NI AB123456C."
    res = analyse(text)
    masked = mask_pii(text, res)

    # "Email" is not masked as PERSON or NAME
    assert "Email me at" in masked.text
    assert "[PERSON 1]" not in masked.text
    assert "[ORG 1]" not in masked.text

    # "NI" is masked only as part of National Insurance match
    assert "[EMAIL 1]" in masked.text
    assert "[NI 1]" in masked.text
    assert "alice@test.co.uk" not in masked.text
    assert "AB123456C" not in masked.text
    assert masked.unmask() == text


def test_mask_pii_r6_standalone_ni_not_masked() -> None:
    text = "I work in NI and my employer is Acme Ltd."
    res = analyse(text)
    masked = mask_pii(text, res)
    assert "I work in NI" in masked.text
    assert "[ORG 1]" in masked.text
    assert masked.reverse_map["[ORG 1]"] == "Acme Ltd"
    assert masked.unmask() == text


def test_mask_pii_r6_sentence_initial_multi_token_person() -> None:
    text = "John Smith was dismissed by Acme Ltd."
    res = analyse(text)
    masked = mask_pii(text, res)
    assert masked.text == "[PERSON 1] was dismissed by [ORG 1]."
    assert masked.reverse_map["[PERSON 1]"] == "John Smith"
    assert masked.reverse_map["[ORG 1]"] == "Acme Ltd"
    assert masked.unmask() == text
