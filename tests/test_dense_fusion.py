"""Tests for dense fusion into discovery (requirement R7).

Tests:
- fusion order on a constructed example
- NoEvidence unchanged when BM25F is empty even if dense has hits
"""

from __future__ import annotations

from unittest.mock import MagicMock

from legal_rag_router import Router

from chapterverse.contracts import RefusalEvent
from chapterverse.dense.colbert import ColbertMatch, ColbertScorer
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


def test_colbert_off_is_identical() -> None:
    cand_a = MagicMock(coordinate="uk/ukpga/1996/18/s1", heading="Heading A")
    cand_b = MagicMock(coordinate="uk/ukpga/1996/18/s2", heading="Heading B")

    mock_discovered = MagicMock(
        candidates=(cand_a, cand_b),
        confident=True,
        reason=None,
        index_snapshot="2026-09-28",
    )
    mock_router = MagicMock(spec=Router)
    mock_router.discover.return_value = mock_discovered
    mock_router.index.snapshot = "2026-09-28"

    hit_b = DenseHit(
        doc_id=1, coordinate="uk/ukpga/1996/18/s2", heading="Heading B", rank=1, score=0.9
    )
    mock_dense = MagicMock(spec=DenseRetriever)
    mock_dense.search.return_value = [hit_b]

    # Calling without colbert keyword argument
    out1 = discover_step(mock_router, "test query", dense=mock_dense, limit=2)
    # Calling with colbert=None explicitly
    out2 = discover_step(mock_router, "test query", dense=mock_dense, colbert=None, limit=2)

    assert isinstance(out1, Suggest)
    assert isinstance(out2, Suggest)
    assert out1.candidates == out2.candidates
    assert out1.confident == out2.confident
    assert out1.question == out2.question
    assert out1.spans is None
    assert out2.spans is None


def test_colbert_rerank_order_constructed_case() -> None:
    # 4 candidates after initial retrieval:
    # Rank 1: coord_b (s2)
    # Rank 2: coord_a (s1)
    # Rank 3: coord_d (s4)
    # Rank 4: coord_c (s3)
    cand_a = MagicMock(coordinate="uk/ukpga/1996/18/s1", heading="Heading A")
    cand_b = MagicMock(coordinate="uk/ukpga/1996/18/s2", heading="Heading B")
    cand_c = MagicMock(coordinate="uk/ukpga/1996/18/s3", heading="Heading C")
    cand_d = MagicMock(coordinate="uk/ukpga/1996/18/s4", heading="Heading D")

    mock_discovered = MagicMock(
        candidates=(cand_a, cand_b, cand_c, cand_d),
        confident=False,
        reason=None,
        index_snapshot="2026-09-28",
    )
    mock_router = MagicMock(spec=Router)
    mock_router.discover.return_value = mock_discovered
    mock_router.index.snapshot = "2026-09-28"

    # Fused candidates in order: cand_b, cand_a, cand_d, cand_c
    hit_b = DenseHit(
        doc_id=1, coordinate="uk/ukpga/1996/18/s2", heading="Heading B", rank=1, score=0.9
    )
    hit_a = DenseHit(
        doc_id=2, coordinate="uk/ukpga/1996/18/s1", heading="Heading A", rank=2, score=0.8
    )
    hit_d = DenseHit(
        doc_id=3, coordinate="uk/ukpga/1996/18/s4", heading="Heading D", rank=3, score=0.7
    )
    hit_c = DenseHit(
        doc_id=4, coordinate="uk/ukpga/1996/18/s3", heading="Heading C", rank=4, score=0.6
    )
    mock_dense = MagicMock(spec=DenseRetriever)
    mock_dense.search.return_value = [hit_b, hit_a, hit_d, hit_c]

    # Fused candidates in initial RRF order:
    # index 0: s1 (score 0.032522, b_tie 1)
    # index 1: s2 (score 0.032522, b_tie 2)
    # index 2: s3 (score 0.031490, b_tie 3)
    # index 3: s4 (score 0.031490, b_tie 4)
    #
    # We assign ColBERT scores:
    # cand_s1 (index 0): score 12.0, span (5, 15)
    # cand_s2 (index 1): score 10.0, span (0, 20) <- ties with cand_s4 (10.0);
    # s2 has better fused rank (2 vs 4)
    # cand_s3 (index 2): score 15.0, span (10, 25) <- highest score
    # cand_s4 (index 3): score 10.0, span (0, 10)
    mock_colbert = MagicMock(spec=ColbertScorer)
    mock_colbert.score.return_value = [
        ColbertMatch(score=12.0, best_window=0, span_char_start=5, span_char_end=15),
        ColbertMatch(score=10.0, best_window=0, span_char_start=0, span_char_end=20),
        ColbertMatch(score=15.0, best_window=0, span_char_start=10, span_char_end=25),
        ColbertMatch(score=10.0, best_window=0, span_char_start=0, span_char_end=10),
    ]

    outcome = discover_step(
        mock_router,
        "test query",
        dense=mock_dense,
        colbert=mock_colbert,
        limit=4,
        rerank_n=4,
    )
    assert isinstance(outcome, Suggest)
    coords = [c.coordinate for c in outcome.candidates]
    # Expected rerank order:
    # 1. cand_c / s3 (15.0)
    # 2. cand_a / s1 (12.0)
    # 3. cand_b / s2 (10.0, fused rank 2)
    # 4. cand_d / s4 (10.0, fused rank 4)
    assert coords == [
        "uk/ukpga/1996/18/s3",
        "uk/ukpga/1996/18/s1",
        "uk/ukpga/1996/18/s2",
        "uk/ukpga/1996/18/s4",
    ]
    # Match spans are exposed on Suggest in corresponding order
    assert outcome.spans == (
        (10, 25),
        (5, 15),
        (0, 20),
        (0, 10),
    )
