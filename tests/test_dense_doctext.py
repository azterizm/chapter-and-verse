"""Unit tests for engine/chapterverse/dense/doctext.py on the fixture store."""

from __future__ import annotations

from pathlib import Path

import pytest
from legal_rag_router.concepts import ConceptIndex, load_concepts

from chapterverse.contracts import TextStore
from chapterverse.dense.doctext import (
    DocTextBuilder,
    doc_text,
    iter_doc_text,
    set_default_store_and_concepts,
)


@pytest.fixture
def concepts(fixture_data_root: Path) -> ConceptIndex:
    return load_concepts(fixture_data_root / "concepts")


def test_doctext_provision_prefix_and_body(
    fixture_text_store: TextStore,
    concepts: ConceptIndex,
) -> None:
    # Doc 0 is ERA 1996 s.94
    text = doc_text(0, fixture_text_store, concepts)

    # Determinism
    assert text == doc_text(0, fixture_text_store, concepts)

    # Whitespace collapsed: no consecutive spaces, no newlines, no tabs
    assert "\n" not in text
    assert "\t" not in text
    assert "  " not in text

    # Prefix form: [<instrument short title with year>] [<Part or Chapter>]
    # [<provision label and heading>]
    assert text.startswith("[Employment Rights Act 1996]")
    assert "[The right]" in text
    assert "[s.94 The right.]" in text
    # Heading and body text present
    assert "The right." in text
    assert "An employee has the right not to be unfairly dismissed by his employer." in text


def test_doctext_provision_with_crossheading(
    fixture_text_store: TextStore,
    concepts: ConceptIndex,
) -> None:
    # Doc 8 is ERA 1996 s.124
    text = doc_text(8, fixture_text_store, concepts)

    assert text.startswith("[Employment Rights Act 1996]")
    assert "[Compensation]" in text
    assert "[s.124 Limit of compensatory award etc.]" in text
    assert "Limit of compensatory award etc." in text
    assert "The amount of—" in text
    assert "123,543" in text


def test_doctext_instrument_doc(
    fixture_text_store: TextStore,
    concepts: ConceptIndex,
) -> None:
    inst_doc_id = concepts.first_instrument_doc

    # Doc 23 is National Health Service (Invalid Direction) Act 1980 (metadata only)
    text = doc_text(inst_doc_id, fixture_text_store, concepts)
    assert text.startswith("[National Health Service (Invalid Direction) Act 1980]")
    assert "  " not in text
    assert "\n" not in text

    # Doc 24 is ERA 1996 instrument doc with long title
    era_inst_id = inst_doc_id + 1
    if era_inst_id < concepts.doc_count:
        era_text = doc_text(era_inst_id, fixture_text_store, concepts)
        assert era_text.startswith("[Employment Rights Act 1996]")
        assert "An Act to consolidate enactments relating to employment rights." in era_text


def test_iter_doc_text(
    fixture_text_store: TextStore,
    concepts: ConceptIndex,
) -> None:
    target_ids = list(range(5))
    iter_results = list(iter_doc_text(target_ids, fixture_text_store, concepts))
    expected = [doc_text(i, fixture_text_store, concepts) for i in target_ids]
    assert iter_results == expected


def test_doctext_builder(
    fixture_text_store: TextStore,
    concepts: ConceptIndex,
) -> None:
    builder = DocTextBuilder(fixture_text_store, concepts)
    assert builder(0) == doc_text(0, fixture_text_store, concepts)
    assert list(builder.iter_doc_text([0, 1])) == [
        doc_text(0, fixture_text_store, concepts),
        doc_text(1, fixture_text_store, concepts),
    ]


def test_unconfigured_raises() -> None:
    set_default_store_and_concepts(None, None)
    with pytest.raises(ValueError, match="TextStore and ConceptIndex must be provided"):
        doc_text(0)
