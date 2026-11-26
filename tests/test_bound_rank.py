"""Tests for bound retrieval: instrument ranges, restricted ranking, and sibling expansion (R5)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from legal_rag_router import Router
from legal_rag_router.concepts import PRIOR_FLAGS
from legal_rag_router.coordinate import Coordinate as RouterCoordinate
from legal_rag_router.discovery import DEFAULT_DISCOVERY
from legal_rag_router.result import Candidate, NextAction, RouteResult, RouteStatus
from legal_rag_verifier.premise import Passage, PassageKind, Premise

if TYPE_CHECKING:
    from tests.test_pipeline import POLICY, CountingStore, of, receipt, run
    from tests.test_pipeline_fakes import FakeRotation
else:
    from test_pipeline import POLICY, CountingStore, of, receipt, run
    from test_pipeline_fakes import FakeRotation

from chapterverse.bound_rank import (
    InstrumentRanges,
    build_instrument_ranges,
    expand_siblings,
    parse_sibling_range,
    rank_sections,
)
from chapterverse.contracts import (
    RouteEvent,
    TemporalStatus,
    TextStore,
    Window,
    canonical_json,
)
from chapterverse.dense.store import BinaryInt8Store
from chapterverse.pipeline import (
    TOP_SECTION_WINDOWS,
    UNTRIMMED_MAX_CHARS,
    Pipeline,
    _premise_passage,
    select_ranked_windows,
)
from chapterverse.store.manifest import sha256_file
from chapterverse.windows import build_windows


def _get_concepts(router: Router) -> Any:
    return getattr(router, "concepts", None) or getattr(router, "_concepts", None)


@pytest.fixture
def store(fixture_text_store: TextStore) -> CountingStore:
    return CountingStore(fixture_text_store)


def _make_dummy_store(
    tmp_path: Path,
    vectors: np.ndarray,
    corpus_id: str = "test-corpus-id-1234",
    snapshot: str = "2026-09-28",
    first_instrument_doc: int | None = None,
) -> BinaryInt8Store:
    count, dim = vectors.shape
    max_abs = np.max(np.abs(vectors), axis=0).astype(np.float32)
    scales = max_abs / 127.0
    scales[scales == 0] = 1.0
    scales_f32 = scales.astype(np.float32)

    q = np.clip(np.round(vectors / scales), -127, 127).astype(np.int8)
    bits = vectors > 0
    codes = np.packbits(bits, axis=-1, bitorder="little").astype(np.uint8)

    tmp_path.mkdir(parents=True, exist_ok=True)
    vecs_file = tmp_path / "vectors.i8"
    codes_file = tmp_path / "codes.bin"
    scales_file = tmp_path / "scales.f32"
    manifest_file = tmp_path / "dense-manifest.json"

    vecs_file.write_bytes(q.tobytes())
    codes_file.write_bytes(codes.tobytes())
    scales_file.write_bytes(scales_f32.tobytes())

    files_info = {
        "codes.bin": {"sha256": sha256_file(codes_file), "size": codes_file.stat().st_size},
        "scales.f32": {"sha256": sha256_file(scales_file), "size": scales_file.stat().st_size},
        "vectors.i8": {"sha256": sha256_file(vecs_file), "size": vecs_file.stat().st_size},
    }

    manifest = {
        "format_version": 1,
        "kind": "chapterverse-dense-index",
        "model": "bge-small-en-v1.5",
        "revision": "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        "dim": dim,
        "count": count,
        "corpus_id": corpus_id,
        "snapshot": snapshot,
        "first_instrument_doc": count if first_instrument_doc is None else first_instrument_doc,
        "quantisation": "int8_scalar_per_dim+binary_sign",
        "files": files_info,
    }
    manifest_file.write_bytes(canonical_json(manifest))
    return BinaryInt8Store.open(tmp_path, corpus_id=corpus_id)


# ---------------------------------------------------------------------- 1. Instrument Ranges


def test_instrument_ranges_build(fixture_router: Router) -> None:
    concepts = _get_concepts(fixture_router)
    ranges_map = build_instrument_ranges(concepts)
    assert len(ranges_map) > 0
    assert "uk/ukpga/1996/18" in ranges_map
    first, end = ranges_map["uk/ukpga/1996/18"]
    assert first < end
    assert first >= 0
    assert end <= concepts.first_instrument_doc


def test_instrument_ranges_background_ready(fixture_router: Router) -> None:
    concepts = _get_concepts(fixture_router)
    ranges = InstrumentRanges()
    assert bool(ranges.ready) is False
    assert ranges.get("uk/ukpga/1996/18") is None

    thread = ranges.start_background_build(concepts)
    thread.join(timeout=5.0)
    assert bool(ranges.ready) is True

    got = ranges.get("uk/ukpga/1996/18")
    assert got is not None
    assert got[0] < got[1]
    assert ranges.get("uk/ukpga/9999/99") is None
    assert ranges.memory_bytes() > 0


# ---------------------------------------------------------------------- 2. BM25F Restricted Ranking


def test_rank_sections_bm25f(fixture_router: Router) -> None:
    concepts = _get_concepts(fixture_router)
    ranges = build_instrument_ranges(concepts)

    results = rank_sections(
        "dismissal capability conduct redundancy",
        "uk/ukpga/1996/18",
        concepts=concepts,
        ranges=ranges,
        dense=None,
    )
    assert len(results) > 0
    scores = [score for _, score in results]
    assert scores == sorted(scores, reverse=True)
    coords = [coord for coord, _ in results]
    assert all(c.startswith("uk/ukpga/1996/18") for c in coords)
    # Section 98 is about dismissal capability, conduct, redundancy
    assert any("s98" in c for c in coords)


def test_rank_sections_empty_or_missing(fixture_router: Router) -> None:
    concepts = _get_concepts(fixture_router)
    ranges = build_instrument_ranges(concepts)
    assert rank_sections("", "uk/ukpga/1996/18", concepts=concepts, ranges=ranges) == []
    assert rank_sections("dismissal", "uk/ukpga/9999/99", concepts=concepts, ranges=ranges) == []


# ---------------------------------------------------------------------- 3. RRF with Dense Scorer


def test_rank_sections_rrf_dense(fixture_router: Router) -> None:
    concepts = _get_concepts(fixture_router)
    ranges = build_instrument_ranges(concepts)
    start_doc, end_doc = ranges["uk/ukpga/1996/18"]
    num_provisions = end_doc - start_doc

    fake_scores = np.zeros(num_provisions, dtype=np.float32)
    fake_scores[1] = 10.0  # high dense score for provision doc at start_doc + 1
    fake_scores[0] = 5.0

    mock_store = MagicMock()
    mock_store.score_rows.return_value = fake_scores

    mock_embedder = MagicMock()
    mock_embedder.embed_query.return_value = np.zeros(384, dtype=np.float32)

    mock_dense = MagicMock()
    mock_dense.store = mock_store
    mock_dense.embedder = mock_embedder

    fused = rank_sections(
        "dismissal",
        "uk/ukpga/1996/18",
        concepts=concepts,
        ranges=ranges,
        dense=mock_dense,
        rrf_k=60,
    )
    assert len(fused) > 0
    scores = [score for _, score in fused]
    assert scores == sorted(scores, reverse=True)


# ------------------------------------------------ 4. score_rows matches rescore


def test_score_rows_matches_rescore(tmp_path: Path) -> None:
    np.random.seed(42)
    dim = 384
    n = 50
    vecs = np.random.randn(n, dim).astype(np.float32)
    store = _make_dummy_store(tmp_path, vecs)

    query = np.random.randn(dim).astype(np.float32)

    start = 10
    end = 35
    scores = store.score_rows(query, start, end)
    assert len(scores) == end - start

    # Rescore equivalent: candidate rows dequantised and dot-producted with query
    cand_vecs = store._vectors[start:end].astype(np.float32)
    expected = (cand_vecs * store._scales).dot(query)

    assert np.allclose(scores, expected, atol=1e-5)

    # Inverted or empty slice returns empty
    assert len(store.score_rows(query, 10, 10)) == 0
    assert len(store.score_rows(query, 20, 10)) == 0


# ---------------------------------------------------------------------- 5. select_ranked_windows


def _make_window(
    coord: str,
    text: str,
    num: int = 1,
    status: TemporalStatus = TemporalStatus.IN_FORCE,
) -> Window:
    return Window(
        passage_id=f"P{num}",
        coordinate=coord,
        heading="Section heading",
        text=text,
        status=status,
        version_date="2026-07-29",
        source_url=f"https://example.com/{coord}",
    )


def test_select_ranked_windows_order_budget_cap() -> None:
    query = "liability limitation period"
    # Windows in section s1, s2, s3
    w_s1_1 = _make_window("uk/ukpga/1980/58/s1", "general provisions for actions", num=1)
    w_s2_1 = _make_window("uk/ukpga/1980/58/s2", "limitation period for actions in tort", num=2)
    w_s2_2 = _make_window("uk/ukpga/1980/58/s2/1", "further details on tort liability", num=3)
    w_s3_1 = _make_window("uk/ukpga/1980/58/s3", "special time limits", num=4)

    all_windows = [w_s1_1, w_s2_1, w_s2_2, w_s3_1]

    # Rank s2 first, s3 second, s1 last
    ranked = [
        ("uk/ukpga/1980/58/s2", 0.9),
        ("uk/ukpga/1980/58/s3", 0.5),
        ("uk/ukpga/1980/58/s1", 0.1),
    ]

    # With cap = 2: should select windows from s2
    selection = select_ranked_windows(all_windows, ranked, query, budget_tokens=1500, max_windows=2)
    assert selection.strategy == "ranked"
    assert len(selection.windows) == 2
    # Document order preserved in final output: s2 comes before s2/1
    coords = [w.coordinate for w in selection.windows]
    assert coords == ["uk/ukpga/1980/58/s2", "uk/ukpga/1980/58/s2/1"]
    assert [w.passage_id for w in selection.windows] == ["P1", "P2"]


def test_select_ranked_windows_highlight_first_within_section() -> None:
    query = "limitation tort"
    # In section s2, w2 has lexical match for query terms, w1 does not
    w1 = _make_window("uk/ukpga/1980/58/s2", "nothing matching here at all", num=1)
    w2 = _make_window("uk/ukpga/1980/58/s2/1", "the limitation period for tort is six years", num=2)

    ranked = [("uk/ukpga/1980/58/s2", 1.0)]
    selection = select_ranked_windows([w1, w2], ranked, query, budget_tokens=1500, max_windows=1)
    assert len(selection.windows) == 1
    # Highlighted window w2 was picked first
    assert selection.windows[0].coordinate == "uk/ukpga/1980/58/s2/1"
    assert selection.windows[0].highlight is not None


def test_rank_sections_scotland_prior_demotion(fixture_router: Router) -> None:
    """R7: a Scotland-flagged section is demoted, and not demoted when query names Scotland."""
    raw_concepts = _get_concepts(fixture_router)
    ranges = build_instrument_ranges(raw_concepts)
    start_doc, _end_doc = ranges["uk/ukpga/1996/18"]

    # Let doc 1 (s98) have the scotland flag set
    scotland_bit = 1 << PRIOR_FLAGS.index("scotland")

    class ScotlandConceptProxy:
        def __init__(self, target: Any) -> None:
            self._target = target

        def prior(self, doc_id: int) -> tuple[int, int]:
            flags, deg = self._target.prior(doc_id)
            if doc_id == start_doc + 1:  # s98
                flags |= scotland_bit
            return flags, deg

        def __getattr__(self, name: str) -> Any:
            return getattr(self._target, name)

    concepts: Any = ScotlandConceptProxy(raw_concepts)

    # Without Scotland in query: s98 is demoted by Scotland penalty
    results_no_scotland = rank_sections(
        "dismissal capability conduct redundancy",
        "uk/ukpga/1996/18",
        concepts=concepts,
        ranges=ranges,
        dense=None,
    )
    s98_score_demoted = next(score for coord, score in results_no_scotland if "s98" in coord)

    # With Scotland in query: Scotland penalty is lifted
    results_with_scotland = rank_sections(
        "dismissal capability conduct redundancy in Scotland",
        "uk/ukpga/1996/18",
        concepts=concepts,
        ranges=ranges,
        dense=None,
    )
    s98_score_lifted = next(score for coord, score in results_with_scotland if "s98" in coord)

    # Score is higher when lifted than when demoted
    assert s98_score_lifted > s98_score_demoted
    penalty = DEFAULT_DISCOVERY.prior_penalties[PRIOR_FLAGS.index("scotland")]
    assert abs(s98_score_demoted - s98_score_lifted * penalty) < 1e-4

    # Also test with 'scottish'
    results_scottish = rank_sections(
        "scottish dismissal capability conduct redundancy",
        "uk/ukpga/1996/18",
        concepts=concepts,
        ranges=ranges,
        dense=None,
    )
    s98_score_scottish = next(score for coord, score in results_scottish if "s98" in coord)
    assert abs(s98_score_scottish - s98_score_lifted) < 1e-4


def test_select_ranked_windows_rounds_on_fixture(fixture_text_store: TextStore) -> None:
    """R8: window allocation over ranked sections runs in three rounds on fixture."""
    rows = fixture_text_store.get("uk/ukpga/1996/18")
    premise = Premise.from_records(rows)
    all_windows = build_windows(premise, rows)
    assert len(all_windows) > 20

    # s123 has 9 windows, s122 has 6 windows, s98 has 5 windows
    ranked = [
        ("uk/ukpga/1996/18/s123", 1.0),
        ("uk/ukpga/1996/18/s122", 0.8),
        ("uk/ukpga/1996/18/s98", 0.6),
    ]

    # Test with max_windows = 5:
    # Round 1: s123 gets up to TOP_SECTION_WINDOWS (3) windows
    # Round 2: s122 gets 1 window, s98 gets 1 window (total 5 -> cap reached)
    sel5 = select_ranked_windows(
        all_windows,
        ranked,
        "compensatory award calculation",
        budget_tokens=5000,
        max_windows=5,
    )
    assert sel5.strategy == "ranked"
    assert len(sel5.windows) == 5

    s123_windows = [w for w in sel5.windows if "s123" in w.coordinate]
    s122_windows = [w for w in sel5.windows if "s122" in w.coordinate]
    s98_windows = [w for w in sel5.windows if "s98" in w.coordinate]

    assert len(s123_windows) == TOP_SECTION_WINDOWS  # 3
    assert len(s122_windows) == 1
    assert len(s98_windows) == 1

    # Store order preserved: s98 comes before s122, which comes before s123
    all_coords = [w.coordinate for w in all_windows]
    coords = [w.coordinate for w in sel5.windows]
    assert coords == sorted(coords, key=all_coords.index)

    # Test with max_windows = 8:
    # Round 1: s123 gets 3 windows
    # Round 2: s122 gets 1 window, s98 gets 1 window (total 5)
    # Round 3: remaining 3 slots go to further windows in rank order:
    #   s123 gets 3 more windows (total 6) -> cap 8 reached
    sel8 = select_ranked_windows(
        all_windows,
        ranked,
        "compensatory award calculation",
        budget_tokens=8000,
        max_windows=8,
    )
    assert len(sel8.windows) == 8
    assert len([w for w in sel8.windows if "s123" in w.coordinate]) == 6
    assert len([w for w in sel8.windows if "s122" in w.coordinate]) == 1
    assert len([w for w in sel8.windows if "s98" in w.coordinate]) == 1


# ------------------------------------------------ 6. Fallback when Ranges not Ready


def test_fallback_when_ranges_not_ready(
    fixture_router: Router,
    store: CountingStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    not_ready_ranker = InstrumentRanges()
    assert not not_ready_ranker.ready

    rotation = FakeRotation([("Provisions quoted [P1].", "stop")])
    pipeline = Pipeline(
        router=fixture_router,
        store=store,
        rotation=rotation,
        policy=POLICY,
        bound_ranker=not_ready_ranker,
    )

    caplog.clear()
    with caplog.at_level(logging.INFO):
        # A whole instrument query where windows exceed budget
        _events1, outcome1 = run(pipeline, "What does the Employment Rights Act 1996 say?")
        assert outcome1.selection in ("lexical", "leading")
        assert "bound ranking not ready or unavailable" in caplog.text

        # Second call: logged once only
        count_before = caplog.text.count("bound ranking not ready or unavailable")
        _events2, outcome2 = run(
            pipeline, "What does the Employment Rights Act 1996 say about terms?"
        )
        assert outcome2.selection in ("lexical", "leading")
        assert caplog.text.count("bound ranking not ready or unavailable") == count_before


# ------------------------------------------------ 7. parse_sibling_range & expand_siblings


@pytest.mark.parametrize(
    ("query", "start", "expected"),
    [
        ("section 172(1)(a) to (d) of the Act", "a", ["a", "d"]),
        ("see paragraph (a)-(d)", "a", ["a", "d"]),
        ("see paragraph (a) - (d)", "a", ["a", "d"]),
        ("sections (a) and (b)", "a", ["a", "b"]),
        ("paragraphs (a), (b) and (c)", "a", ["a", "b", "c"]),
        ("section 98(1) to (3)", "1", ["1", "3"]),
    ],
)
def test_parse_sibling_range(query: str, start: str, expected: list[str]) -> None:
    assert parse_sibling_range(query, start) == expected


def test_expand_siblings_patterns(fixture_text_store: TextStore) -> None:
    # In fixture Employment Rights Act 1996:
    # uk/ukpga/1996/18/s98/2 has children: a, b, ba, c, d
    base = "uk/ukpga/1996/18/s98/2/a"

    # Pattern: (a) to (d)
    res = expand_siblings("Quote section 98(2)(a) to (d) of the Act", base, fixture_text_store)
    assert res == [
        "uk/ukpga/1996/18/s98/2/a",
        "uk/ukpga/1996/18/s98/2/b",
        "uk/ukpga/1996/18/s98/2/ba",
        "uk/ukpga/1996/18/s98/2/c",
        "uk/ukpga/1996/18/s98/2/d",
    ]

    # Pattern: (a)-(d)
    res = expand_siblings("Quote section 98(2)(a)-(d)", base, fixture_text_store)
    assert len(res) == 5

    # Pattern: (a) - (d)
    res = expand_siblings("Quote section 98(2)(a) - (d)", base, fixture_text_store)
    assert len(res) == 5

    # Pattern: (a) and (b)
    res = expand_siblings("Quote section 98(2)(a) and (b)", base, fixture_text_store)
    assert res == [
        "uk/ukpga/1996/18/s98/2/a",
        "uk/ukpga/1996/18/s98/2/b",
    ]

    # Pattern: (a), (b) and (c)
    res = expand_siblings("Quote section 98(2)(a), (b) and (c)", base, fixture_text_store)
    assert res == [
        "uk/ukpga/1996/18/s98/2/a",
        "uk/ukpga/1996/18/s98/2/b",
        "uk/ukpga/1996/18/s98/2/ba",
        "uk/ukpga/1996/18/s98/2/c",
    ]

    # Pattern: (1) to (3) under s98 (includes s98/2A in store order between s98/2 and s98/3)
    base_s98_1 = "uk/ukpga/1996/18/s98/1"
    res = expand_siblings("Quote section 98(1) to (3)", base_s98_1, fixture_text_store)
    assert res == [
        "uk/ukpga/1996/18/s98/1",
        "uk/ukpga/1996/18/s98/2",
        "uk/ukpga/1996/18/s98/2A",
        "uk/ukpga/1996/18/s98/3",
    ]


def test_expand_siblings_unclear_cases(fixture_text_store: TextStore) -> None:
    base = "uk/ukpga/1996/18/s98/2/a"

    # Coordinate is an instrument (<= 4 parts): unchanged
    assert expand_siblings("range (a) to (d)", "uk/ukpga/1996/18", fixture_text_store) == [
        "uk/ukpga/1996/18"
    ]

    # No range in query: unchanged
    assert expand_siblings("What does s98(2)(a) say?", base, fixture_text_store) == [base]

    # End before start: (d) to (a)
    assert expand_siblings("s98(2)(d) to (a)", "uk/ukpga/1996/18/s98/2/d", fixture_text_store) == [
        "uk/ukpga/1996/18/s98/2/d"
    ]

    # Label not found / not siblings
    assert expand_siblings("s98(2)(a) to (z)", base, fixture_text_store) == [base]

    # Store missing parent: mock store raising KeyError
    bad_store = MagicMock()
    bad_store.get.side_effect = KeyError("missing")
    assert expand_siblings("s98(2)(a) to (d)", base, bad_store) == [base]


# ------------------------------------------------ 8. Pipeline Range Query Expands Route and Receipt


def test_pipeline_range_query_expands_route_and_receipt(
    fixture_router: Router,
    store: CountingStore,
) -> None:
    coord = RouterCoordinate.parse("uk/ukpga/1996/18/s98/1")
    mock_router = MagicMock()
    mock_router.route.return_value = RouteResult(
        status=RouteStatus.BOUNDED,
        next_action=NextAction.RETRIEVE_BOUNDED,
        coordinates=(coord,),
        candidates=(Candidate(coordinate=coord, label="s.98(1)"),),
    )
    mock_router.concepts = _get_concepts(fixture_router)
    mock_router._concepts = _get_concepts(fixture_router)

    rotation = FakeRotation([("Dismissal provisions quoted [P1].", "stop")])
    pipeline = Pipeline(
        router=mock_router,
        store=store,
        rotation=rotation,
        policy=POLICY,
    )

    query = "Quote section 98(1) to (3) of the Employment Rights Act 1996"
    events, _outcome = run(pipeline, query)

    # RouteEvent coordinates should be expanded to s98/1, s98/2, s98/2A, s98/3
    route_events = of(events, RouteEvent)
    assert len(route_events) == 1
    assert list(route_events[0].coordinates) == [
        "uk/ukpga/1996/18/s98/1",
        "uk/ukpga/1996/18/s98/2",
        "uk/ukpga/1996/18/s98/2A",
        "uk/ukpga/1996/18/s98/3",
    ]

    # ReceiptEvent fact coordinates should have all 4
    rec = receipt(events)
    assert rec.fact.coordinates == (
        "uk/ukpga/1996/18/s98/1",
        "uk/ukpga/1996/18/s98/2",
        "uk/ukpga/1996/18/s98/2A",
        "uk/ukpga/1996/18/s98/3",
    )

    # All coordinates read from store
    assert "uk/ukpga/1996/18/s98/1" in store.gets
    assert "uk/ukpga/1996/18/s98/2" in store.gets
    assert "uk/ukpga/1996/18/s98/2A" in store.gets
    assert "uk/ukpga/1996/18/s98/3" in store.gets


# ------------------------------------------------ 9. Layer 2: Cross-refs, Ordering, Untrimmed


def test_select_ranked_windows_promotes_targeted_xref_not_omnibus() -> None:
    query = "agree work more than 48 hours week"
    w_reg4 = _make_window(
        "uk/uksi/1998/1833/reg4/1",
        "A worker's working time shall not exceed 48 hours each week.",
        num=1,
    )
    w_reg5 = Window(
        passage_id="P2",
        coordinate="uk/uksi/1998/1833/reg5/2",
        heading="Regulation 5(2) (Agreement to exclude the maximum)",
        text="An agreement for the purposes of regulation 4 may apply indefinitely.",
        status=TemporalStatus.IN_FORCE,
        version_date="2026-04-06",
        source_url="https://example.com/reg5/2",
    )
    w_omni = Window(
        passage_id="P3",
        coordinate="uk/uksi/1998/1833/reg18/2",
        heading="Regulation 18(2) (Excluded sectors)",
        text="Regulations 4(1), 6(1), 8, 10(1), 11(1), 12(1) and 13 do not apply.",
        status=TemporalStatus.IN_FORCE,
        version_date="2026-04-06",
        source_url="https://example.com/reg18/2",
    )
    extra = [
        _make_window(f"uk/uksi/1998/1833/reg{i}/1", f"Other hours week text {i}.", num=i)
        for i in range(6, 14)
    ]
    all_windows = [w_reg4, w_reg5, w_omni, *extra]
    ranked = [
        ("uk/uksi/1998/1833/reg4", 0.9),
        *[(f"uk/uksi/1998/1833/reg{i}", 0.8 - i * 0.01) for i in range(6, 14)],
        ("uk/uksi/1998/1833/reg18", 0.2),
        ("uk/uksi/1998/1833/reg5", 0.1),
    ]

    sel = select_ranked_windows(all_windows, ranked, query, budget_tokens=1500, max_windows=8)
    coords = [w.coordinate for w in sel.windows]
    assert "uk/uksi/1998/1833/reg5/2" in coords
    assert "uk/uksi/1998/1833/reg18/2" not in coords


def test_select_ranked_windows_keeps_long_window_untrimmed_within_budget() -> None:
    query = "protected disclosure qualifying period"
    # > 600 chars so trim_window_text would chop the tail, yet fits inside budget=4000
    long_text = (
        "(3) Subsection (1) does not apply if— "
        + " ".join(f"({chr(97 + i % 26)}) section {98 + i} applies," for i in range(30))
        + " (ff) section 103A applies."
    )
    assert len(long_text) > 600
    w1 = _make_window("uk/ukpga/1996/18/s108/1", "Two years qualifying period.", num=1)
    w3 = _make_window("uk/ukpga/1996/18/s108/3", long_text, num=2)
    w_other = _make_window("uk/ukpga/1996/18/s103A", "Protected disclosure dismissal.", num=3)
    ranked = [
        ("uk/ukpga/1996/18/s103A", 0.95),
        ("uk/ukpga/1996/18/s108", 0.90),
    ]

    sel = select_ranked_windows(
        [w_other, w1, w3], ranked, query, budget_tokens=4000, max_windows=12
    )
    by_coord = {w.coordinate: w for w in sel.windows}
    assert by_coord["uk/ukpga/1996/18/s108/3"].highlight is None


def test_select_ranked_windows_depth_for_ranks_two_and_three_with_12_windows() -> None:
    query = "annual leave entitlement"
    w_s1 = [
        _make_window(f"uk/uksi/1998/1833/reg13/{i}", f"leave reg13 {i}", num=i) for i in range(1, 5)
    ]
    w_s2 = [
        _make_window(f"uk/uksi/1998/1833/reg13A/{i}", f"leave reg13A {i}", num=10 + i)
        for i in range(1, 5)
    ]
    w_s3 = [
        _make_window(f"uk/uksi/1998/1833/reg15B/{i}", f"leave reg15B {i}", num=20 + i)
        for i in range(1, 5)
    ]
    w_rest = [
        _make_window(f"uk/uksi/1998/1833/reg{20 + i}/1", f"leave reg{20 + i}", num=30 + i)
        for i in range(8)
    ]
    all_windows = [*w_s1, *w_s2, *w_s3, *w_rest]
    ranked = [
        ("uk/uksi/1998/1833/reg13", 1.0),
        ("uk/uksi/1998/1833/reg13A", 0.9),
        ("uk/uksi/1998/1833/reg15B", 0.8),
        *[(f"uk/uksi/1998/1833/reg{20 + i}", 0.5 - i * 0.01) for i in range(8)],
    ]

    sel = select_ranked_windows(all_windows, ranked, query, budget_tokens=4000, max_windows=12)
    assert len(sel.windows) == 12
    reg13a_count = sum(1 for w in sel.windows if "/reg13A/" in w.coordinate)
    reg15b_count = sum(1 for w in sel.windows if "/reg15B/" in w.coordinate)
    assert reg13a_count == 3
    assert reg15b_count == 3


def test_select_ranked_windows_keeps_huge_window_trimmed_above_untrimmed_max() -> None:
    query = "special qualifying exemption clause"
    huge_text = (
        ("Introductory filler words repeated. " * 110)
        + "special qualifying exemption clause applies here. "
        + ("Trailing schedule text repeated. " * 20)
    )
    assert len(huge_text) > UNTRIMMED_MAX_CHARS
    w_huge = _make_window("uk/ukpga/1996/18/s108/3", huge_text, num=1)
    w_other = _make_window("uk/ukpga/1996/18/s103A", "Protected disclosure.", num=2)
    w_extra = _make_window("uk/ukpga/1996/18/s109/1", "Other unrelated text.", num=3)
    ranked = [
        ("uk/ukpga/1996/18/s103A", 0.95),
        ("uk/ukpga/1996/18/s108", 0.90),
        ("uk/ukpga/1996/18/s109", 0.50),
    ]

    sel = select_ranked_windows(
        [w_other, w_huge, w_extra], ranked, query, budget_tokens=4000, max_windows=2
    )
    assert sel.strategy == "ranked"
    by_coord = {w.coordinate: w for w in sel.windows}
    assert by_coord["uk/ukpga/1996/18/s108/3"].highlight is not None


def test_select_ranked_windows_round4_distributes_round_robin() -> None:
    query = "annual leave entitlement"
    w_s1 = _make_window("uk/uksi/1998/1833/reg1/1", "annual leave reg1", num=1)
    w_s2 = [
        _make_window(f"uk/uksi/1998/1833/reg2/{i}", f"annual leave reg2 {i}", num=10 + i)
        for i in range(1, 6)
    ]
    w_s3 = [
        _make_window(f"uk/uksi/1998/1833/reg3/{i}", f"annual leave reg3 {i}", num=20 + i)
        for i in range(1, 6)
    ]
    w_s4 = [
        _make_window(f"uk/uksi/1998/1833/reg4/{i}", f"annual leave reg4 {i}", num=30 + i)
        for i in range(1, 6)
    ]
    w_rest = [
        _make_window(f"uk/uksi/1998/1833/reg{10 + i}/1", f"annual leave reg{10 + i}", num=40 + i)
        for i in range(3)
    ]
    all_windows = [w_s1, *w_s2, *w_s3, *w_s4, *w_rest]
    ranked = [
        ("uk/uksi/1998/1833/reg1", 1.0),
        ("uk/uksi/1998/1833/reg2", 0.9),
        ("uk/uksi/1998/1833/reg3", 0.8),
        ("uk/uksi/1998/1833/reg4", 0.7),
        *[(f"uk/uksi/1998/1833/reg{10 + i}", 0.5 - i * 0.01) for i in range(3)],
    ]

    sel = select_ranked_windows(all_windows, ranked, query, budget_tokens=4000, max_windows=12)
    assert len(sel.windows) == 12
    reg2_count = sum(1 for w in sel.windows if "/reg2/" in w.coordinate)
    reg3_count = sum(1 for w in sel.windows if "/reg3/" in w.coordinate)
    reg4_count = sum(1 for w in sel.windows if "/reg4/" in w.coordinate)
    assert reg2_count == TOP_SECTION_WINDOWS
    assert reg3_count == TOP_SECTION_WINDOWS
    assert reg4_count == 2


def test_premise_passage_trims_to_highlight_and_clears_limbs() -> None:
    long_text = ("A " * 500) + "target clause here." + (" B" * 500)
    passage = Passage(
        text=long_text,
        coordinate="uk/ukpga/2015/15/sch5",
        kind=PassageKind.PROVISION,
        heading="Schedule 5",
        limbs=("limb 1", "limb 2"),
    )
    win_untrimmed = _make_window("uk/ukpga/2015/15/sch5", long_text, num=1)
    assert _premise_passage(passage, win_untrimmed) is passage

    win_short_hl = Window(
        passage_id="P1",
        coordinate="uk/ukpga/2015/15/sch5",
        heading="Schedule 5",
        text="Short text.",
        status=TemporalStatus.IN_FORCE,
        version_date="2026-04-06",
        source_url="https://example.com/sch5",
        highlight=(0, 5),
    )
    short_passage = Passage(
        text="Short text.",
        coordinate="uk/ukpga/2015/15/sch5",
        kind=PassageKind.PROVISION,
        limbs=("limb",),
    )
    assert _premise_passage(short_passage, win_short_hl) is short_passage

    win_trimmed = Window(
        passage_id="P1",
        coordinate="uk/ukpga/2015/15/sch5",
        heading="Schedule 5",
        text=long_text,
        status=TemporalStatus.IN_FORCE,
        version_date="2026-04-06",
        source_url="https://example.com/sch5",
        highlight=(1000, 1019),
    )
    trimmed = _premise_passage(passage, win_trimmed)
    assert trimmed is not passage
    assert len(trimmed.text) < len(long_text)
    assert "target clause here." in trimmed.text
    assert trimmed.limbs == ()
