"""Windows from a verifier premise (chapterverse.windows)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from legal_rag_verifier.premise import Passage, PassageKind, Premise

from chapterverse.contracts import TemporalStatus, Window
from chapterverse.windows import build_windows, passage_ids, passages_by_id, provision_passages

S124 = "uk/ukpga/1996/18/s124"


def _windows(store: Any, coordinate: str, **kwargs: Any) -> tuple[Window, ...]:
    rows = store.get(coordinate)
    return build_windows(Premise.from_records(rows), rows, **kwargs)


def test_era_s124_windows(fixture_text_store: Any) -> None:
    rows = fixture_text_store.get(S124)
    premise = Premise.from_records(rows)
    windows = build_windows(premise, rows)
    assert [w.passage_id for w in windows] == ["P1", "P2", "P3", "P4", "P5", "P6"]
    # s124(2) is repealed text, which the premise drops.
    assert [w.coordinate.removeprefix(S124 + "/") for w in windows] == [
        "1",
        "1ZA",
        "1A",
        "3",
        "4",
        "5",
    ]
    urls = {r["coordinate"]: r["source_url"] for r in rows[1:]}
    passages = provision_passages(premise)
    for window, passage in zip(windows, passages, strict=True):
        assert window.text == passage.text
        assert window.heading == passage.heading
        assert window.status is TemporalStatus.IN_FORCE
        assert window.version_date == "2026-07-29"
        assert window.source_url == urls[window.coordinate]
        assert window.highlight is None
    assert "\u00a3123,543" in windows[1].text


def test_passages_by_id_matches_windows(fixture_text_store: Any) -> None:
    rows = fixture_text_store.get(S124)
    premise = Premise.from_records(rows, facts=["The cap was raised on 6 April 2026."])
    windows = build_windows(premise, rows)
    by_id = passages_by_id(premise)
    assert list(by_id) == [w.passage_id for w in windows]
    for window in windows:
        passage = by_id[window.passage_id]
        assert isinstance(passage, Passage)
        assert passage.kind is PassageKind.PROVISION
        assert (passage.coordinate, passage.text) == (window.coordinate, window.text)
    # Fact passages carry no coordinate and are not windows.
    assert len(premise.passages) == len(windows) + 1


def test_repealed_sections_have_no_window(
    fixture_text_store: Any, fixture_roles: dict[str, Any]
) -> None:
    role = fixture_roles["repealed"]
    windows = _windows(fixture_text_store, role["instrument"])
    coordinates = {w.coordinate for w in windows}
    for gone in [*role["provisions"], "uk/ukpga/2008/7/s6"]:
        assert not any(c == gone or c.startswith(gone + "/") for c in coordinates)
    assert {w.status for w in windows} == {TemporalStatus.IN_FORCE}
    assert windows[0].version_date == "2018-07-04"


def test_prospective_windows(fixture_text_store: Any, fixture_roles: dict[str, Any]) -> None:
    role = fixture_roles["prospective"]
    windows = _windows(fixture_text_store, role["instrument"])
    for window in windows:
        expected = (
            TemporalStatus.NOT_YET_IN_FORCE
            if window.coordinate.startswith(role["provisions"][0] + "/")
            else TemporalStatus.IN_FORCE
        )
        assert window.status is expected, window.coordinate
    assert {w.status for w in windows} == {TemporalStatus.NOT_YET_IN_FORCE, TemporalStatus.IN_FORCE}


def test_title_only_has_no_windows(fixture_text_store: Any, fixture_roles: dict[str, Any]) -> None:
    assert _windows(fixture_text_store, fixture_roles["title_only"]["instrument"]) == ()


def test_as_at_before_version_date(fixture_text_store: Any) -> None:
    windows = _windows(fixture_text_store, S124, as_at=date(2019, 1, 1))
    assert {w.status for w in windows} == {TemporalStatus.HISTORY_NOT_HELD}
    later = _windows(fixture_text_store, S124, as_at=date(2026, 8, 1))
    assert {w.status for w in later} == {TemporalStatus.IN_FORCE}


def test_router_repealed_flag(fixture_text_store: Any) -> None:
    windows = _windows(fixture_text_store, "uk/ukpga/2008/7/s3", repealed=True)
    assert windows
    assert {w.status for w in windows} == {TemporalStatus.REPEALED}


def test_passage_outside_records_is_an_error(fixture_text_store: Any) -> None:
    premise = Premise.from_records(fixture_text_store.get(S124))
    with pytest.raises(ValueError, match="not among the records"):
        build_windows(premise, fixture_text_store.get("uk/ukpga/1996/18/s125"))


def test_passage_ids() -> None:
    assert passage_ids(0) == ()
    assert passage_ids(3) == ("P1", "P2", "P3")
