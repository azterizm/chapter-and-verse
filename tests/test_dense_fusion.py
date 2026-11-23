"""Tests for dense fusion into discovery (requirement R7).

Tests:
- fusion order on a constructed example
- NoEvidence unchanged when BM25F is empty even if dense has hits
"""

from __future__ import annotations

from unittest.mock import MagicMock

from legal_rag_router import Router

from chapterverse.contracts import RefusalEvent
from chapterverse.dense.retriever import DenseHit, DenseRetriever
from chapterverse.discovery import NoEvidence, Suggest, discover_step


def test_rrf_fusion_order_constructed_example() -> None:
    # Construct BM25F candidates:
    # Rank 1: coord_a
    # Rank 2: coord_b
    # Rank 3: coord_c
    cand_a = MagicMock(coordinate="uk/ukpga/1996/18/s1", heading="Heading A")
    cand_b = MagicMock(coordinate="uk/ukpga/1996/18/s2", heading="Heading B")
    cand_c = MagicMock(coordinate="uk/ukpga/1996/18/s3", heading="Heading C")

    mock_discovered = MagicMock(
        candidates=(cand_a, cand_b, cand_c),
        confident=True,
        reason=None,
        index_snapshot="2026-09-28",
    )
    mock_router = MagicMock(spec=Router)
    mock_router.discover.return_value = mock_discovered
    mock_router.index.snapshot = "2026-09-28"

    # Construct Dense candidates:
    # Rank 1: coord_b (in both: BM25F rank 2, dense rank 1)
    # Rank 2: coord_d (dense only: dense rank 2)
    # Rank 3: coord_a (in both: BM25F rank 1, dense rank 3)
    hit_b = DenseHit(
        doc_id=1, coordinate="uk/ukpga/1996/18/s2", heading="Heading B", rank=1, score=0.9
    )
    hit_d = DenseHit(
        doc_id=2, coordinate="uk/ukpga/1996/18/s4", heading="Heading D", rank=2, score=0.85
    )
    hit_a = DenseHit(
        doc_id=3, coordinate="uk/ukpga/1996/18/s1", heading="Heading A", rank=3, score=0.8
    )

    mock_dense = MagicMock(spec=DenseRetriever)
    mock_dense.search.return_value = [hit_b, hit_d, hit_a]

    # Calculate expected RRF scores (with rrf_k=60):
    # A: 1/(60+1) + 1/(60+3) = 1/61 + 1/63 = 0.016393 + 0.015873 = 0.032266
    # B: 1/(60+2) + 1/(60+1) = 1/62 + 1/61 = 0.016129 + 0.016393 = 0.032522
    # C: 1/(60+3)            = 1/63          = 0.015873
    # D: 1/(60+2)            = 1/62          = 0.016129
    # Ordering: B (0.032522) > A (0.032266) > D (0.016129) > C (0.015873)

    outcome = discover_step(mock_router, "test query", dense=mock_dense, limit=4, pool=10, rrf_k=60)
    assert isinstance(outcome, Suggest)
    assert len(outcome.candidates) == 4

    coords = [c.coordinate for c in outcome.candidates]
    assert coords == [
        "uk/ukpga/1996/18/s2",  # B
        "uk/ukpga/1996/18/s1",  # A
        "uk/ukpga/1996/18/s4",  # D
        "uk/ukpga/1996/18/s3",  # C
    ]

    # Confident flag and question text match BM25F
    assert outcome.confident is True
    assert outcome.question == "Did you mean one of these provisions?"


def test_rrf_fusion_tie_breaking() -> None:
    # Test ties broken by BM25F rank, then coordinate
    cand_a = MagicMock(coordinate="uk/ukpga/1996/18/s1", heading="Heading A")
    cand_b = MagicMock(coordinate="uk/ukpga/1996/18/s2", heading="Heading B")

    mock_discovered = MagicMock(
        candidates=(cand_a, cand_b),
        confident=False,
        reason=None,
        index_snapshot="2026-09-28",
    )
    mock_router = MagicMock(spec=Router)
    mock_router.discover.return_value = mock_discovered
    mock_router.index.snapshot = "2026-09-28"

    # Dense only hits with identical ranks / scores
    # hit_x: rank 1, score 0.5
    # hit_y: rank 1, score 0.5
    hit_x = DenseHit(
        doc_id=10, coordinate="uk/ukpga/1996/18/s10", heading="Heading X", rank=1, score=0.5
    )
    hit_y = DenseHit(
        doc_id=11, coordinate="uk/ukpga/1996/18/s11", heading="Heading Y", rank=1, score=0.5
    )

    mock_dense = MagicMock(spec=DenseRetriever)
    mock_dense.search.return_value = [hit_x, hit_y]

    # Both X and Y have equal RRF score (1/61), neither is in BM25F (bm25_rank = inf).
    # Tie broken by coordinate string: s10 < s11.
    outcome = discover_step(mock_router, "test query", dense=mock_dense, limit=10, rrf_k=60)
    assert isinstance(outcome, Suggest)

    coords = [c.coordinate for c in outcome.candidates]
    idx_x = coords.index("uk/ukpga/1996/18/s10")
    idx_y = coords.index("uk/ukpga/1996/18/s11")
    assert idx_x < idx_y


def test_no_evidence_unchanged_when_bm25f_is_empty_even_if_dense_has_hits() -> None:
    # Router returns no candidates (BM25F empty / no_match)
    mock_discovered = MagicMock(
        candidates=(),
        confident=False,
        reason="no_match",
        index_snapshot="2026-09-28",
    )
    mock_router = MagicMock()
    mock_router.discover.return_value = mock_discovered
    mock_router.index.snapshot = "2026-09-28"
    mock_concepts = MagicMock()
    mock_concepts.doc_count = 1000
    mock_concepts.first_instrument_doc = 900
    mock_router.concepts = mock_concepts
    mock_router._concepts = mock_concepts

    # Dense retriever has strong hits
    mock_dense = MagicMock(spec=DenseRetriever)
    mock_dense.search.return_value = [
        DenseHit(
            doc_id=1,
            coordinate="uk/ukpga/1996/18/s124",
            heading="Compensation",
            rank=1,
            score=0.95,
        ),
        DenseHit(
            doc_id=2,
            coordinate="uk/ukpga/1996/18/s94",
            heading="Unfair dismissal",
            rank=2,
            score=0.92,
        ),
    ]

    outcome = discover_step(mock_router, "quantum teleportation asteroid", dense=mock_dense)

    # Must return NoEvidence even though dense has hits!
    assert isinstance(outcome, NoEvidence)
    receipt = outcome.receipt
    assert isinstance(receipt, RefusalEvent)
    assert receipt.kind == "ROUTE_UNRESOLVED"
    assert "Searched 900 provisions" in receipt.message
    assert "No provision matched." in receipt.message
