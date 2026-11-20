"""Extractive answer with lexical highlights (chapterverse.extractive)."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from legal_rag_router.normalise import fold
from legal_rag_verifier.premise import Premise

from chapterverse.contracts import ExtractiveEvent, TemporalStatus, Window, encode_sse
from chapterverse.extractive import (
    MAX_EXTRACTIVE_WINDOWS,
    MAX_HIGHLIGHT_CHARS,
    extractive_answer,
    highlight_span,
    query_terms,
)
from chapterverse.windows import build_windows

S124 = "uk/ukpga/1996/18/s124"
QUESTION = "What is the limit of the compensatory award under s.124 ERA 1996?"


def _windows(store: Any, coordinate: str) -> tuple[Window, ...]:
    rows = store.get(coordinate)
    return build_windows(Premise.from_records(rows), rows)


def _window(text: str, passage_id: str = "P1") -> Window:
    return Window(
        passage_id=passage_id,
        coordinate="uk/ukpga/1996/18/s1",
        heading="",
        text=text,
        status=TemporalStatus.IN_FORCE,
        version_date=None,
        source_url="https://www.legislation.gov.uk/ukpga/1996/18/section/1",
    )


def test_era_s124_highlights(fixture_text_store: Any) -> None:
    windows = _windows(fixture_text_store, S124)
    event = extractive_answer(windows, QUESTION, "providers_unavailable")
    assert isinstance(event, ExtractiveEvent)
    assert event.reason == "providers_unavailable"
    assert [w.passage_id for w in event.windows] == [w.passage_id for w in windows]
    first = event.windows[0]
    assert first.highlight is not None
    start, end = first.highlight
    assert first.text[start:end] == "compensatory award"
    # s124(1ZA) shares no content word with the question.
    assert event.windows[1].highlight is None
    for window in event.windows:
        if window.highlight is not None:
            start, end = window.highlight
            assert 0 <= start < end <= len(window.text)
            assert end - start <= MAX_HIGHLIGHT_CHARS
    assert b"event: extractive" in encode_sse(event)


def test_highlights_other_fixture_roles(fixture_text_store: Any) -> None:
    windows = _windows(fixture_text_store, "uk/ukpga/2018/11")
    event = extractive_answer(windows, "When does section 1 come into force?", "provider_warming")
    assert event.reason == "provider_warming"
    spans = {w.coordinate: w.highlight for w in event.windows}
    s2_2 = next(w for w in event.windows if w.coordinate == "uk/ukpga/2018/11/s2/2")
    assert s2_2.highlight is not None
    assert "force" in s2_2.text[s2_2.highlight[0] : s2_2.highlight[1]]
    assert spans["uk/ukpga/2018/11/s2/1"] is None


def test_query_terms() -> None:
    assert query_terms("What is the COMPENSATORY Awards cap under section 124?") == {
        "compensatory",
        "award",
        "cap",
        "124",
    }
    assert query_terms("the of and section act") == frozenset()
    # Folding is the router's: full-width and accented letters meet plain ones.
    assert query_terms("\uff21\uff37\uff21\uff32\uff24 caf\u00e9") == {"award", "cafe"}


def test_nothing_matches() -> None:
    windows = [_window("The Secretary of State may by order amend.")]
    event = extractive_answer(windows, "zebra", "provider_warming")
    assert event.windows[0].highlight is None
    assert extractive_answer([], QUESTION, "providers_unavailable").windows == ()


def test_window_count_is_bounded() -> None:
    windows = [_window(f"Text {i} with nothing.", f"P{i + 1}") for i in range(12)]
    windows[10] = _window("The compensatory award is limited.", "P11")
    windows[3] = _window("An award may be made.", "P4")
    event = extractive_answer(windows, "compensatory award limit", "providers_unavailable")
    assert len(event.windows) == MAX_EXTRACTIVE_WINDOWS
    ids = [w.passage_id for w in event.windows]
    assert "P11" in ids
    assert "P4" in ids
    assert ids == sorted(ids, key=lambda p: int(p[1:]))
    small = extractive_answer(windows, "award", "providers_unavailable", max_windows=1)
    assert [w.passage_id for w in small.windows] == ["P4"]
    with pytest.raises(ValueError, match="positive"):
        extractive_answer(windows, "award", "providers_unavailable", max_windows=0)


def test_highlight_length_is_capped() -> None:
    text = "award " + "filler " * 100 + "compensatory award"
    span, distinct, matches = highlight_span(text, query_terms("compensatory award"), max_chars=40)
    assert span is not None
    assert text[span[0] : span[1]] == "compensatory award"
    assert (distinct, matches) == (2, 2)


def test_non_ascii_offsets_point_at_the_original_text() -> None:
    text = (
        "The amount is the lower of\u2014 (a) \u00a3123,543, and (b) 52 multiplied by "
        "a week\u2019s pay."
    )
    event = extractive_answer([_window(text)], "week's PAY multiplied", "providers_unavailable")
    span = event.windows[0].highlight
    assert span is not None
    assert text[span[0] : span[1]] == "multiplied by a week\u2019s pay"


def test_deterministic(fixture_text_store: Any) -> None:
    windows = _windows(fixture_text_store, S124)
    first = extractive_answer(windows, QUESTION, "providers_unavailable")
    assert first == extractive_answer(windows, QUESTION, "providers_unavailable")
    assert [dataclasses.replace(w, highlight=None) for w in first.windows] == list(windows)


_WORDS = st.sampled_from(["award", "limit", "compensatory", "tribunal", "the", "pay", "week", "x"])


@settings(max_examples=200, deadline=None)
@given(
    st.lists(_WORDS, max_size=80).map(" ".join),
    st.lists(_WORDS, max_size=6).map(" ".join),
    st.integers(min_value=1, max_value=120),
)
def test_span_properties(text: str, query: str, max_chars: int) -> None:
    terms = query_terms(query)
    span, distinct, matches = highlight_span(text, terms, max_chars=max_chars)
    words = set(fold(text).text.split())
    if not terms & words:
        assert span is None
        assert (distinct, matches) == (0, 0)
        return
    assert span is not None
    start, end = span
    assert 0 <= start < end <= len(text)
    assert end - start <= max_chars
    assert distinct >= 1
    assert matches >= distinct
