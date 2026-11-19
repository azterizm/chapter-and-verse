"""Temporal status of bound coordinates and windows (chapterverse.status)."""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

import pytest

from chapterverse.contracts import TemporalStatus
from chapterverse.status import (
    REASONS,
    decide_status,
    held_version_date,
    instrument_status,
    window_statuses,
)

IN_FORCE = TemporalStatus.IN_FORCE
NOT_YET = TemporalStatus.NOT_YET_IN_FORCE
REPEALED = TemporalStatus.REPEALED
TEXT_NOT_HELD = TemporalStatus.TEXT_NOT_HELD
HISTORY = TemporalStatus.HISTORY_NOT_HELD
S124 = "uk/ukpga/1996/18/s124"


def _rows(store: Any, coordinate: str) -> list[dict[str, Any]]:
    return copy.deepcopy(store.get(coordinate))


def _set(rows: list[dict[str, Any]], coordinate: str, **fields: Any) -> None:
    for row in rows:
        if row.get("coordinate") == coordinate:
            row.update(fields)
            return
    raise KeyError(coordinate)


# ---------------------------------------------------------------------------- fixture roles


def test_era_s124_in_force_with_a_repealed_subsection(fixture_text_store: Any) -> None:
    # s124(2) is a dot run with repealed: false; the rest is in force.
    decision = decide_status(fixture_text_store.get(S124), S124)
    assert decision.status is IN_FORCE
    assert decision.reason == "partly_repealed"
    assert decision.link is None
    assert decision.version_date == "2026-07-29"
    assert decide_status(fixture_text_store.get(S124 + "/1")).reason == "in_force"
    assert decide_status(fixture_text_store.get(S124 + "/2")).status is REPEALED


def test_era_s124_with_one_prospective_subsection(fixture_text_store: Any) -> None:
    rows = _rows(fixture_text_store, S124)
    _set(rows, S124 + "/1A", prospective=True)
    decision = decide_status(rows, S124)
    assert decision.status is IN_FORCE
    assert decision.reason == "partly_repealed_and_not_yet_in_force"
    _set(rows, S124 + "/2", text="")  # no repealed text left
    assert decide_status(rows, S124).reason == "partly_not_yet_in_force"
    statuses = window_statuses(rows, [S124 + "/1", S124 + "/1A", S124 + "/3"])
    assert statuses == {S124 + "/1": IN_FORCE, S124 + "/1A": NOT_YET, S124 + "/3": IN_FORCE}
    assert decide_status(rows, S124 + "/1A").reason == "provision_prospective"


def test_window_with_a_prospective_paragraph_is_not_yet_in_force(fixture_text_store: Any) -> None:
    rows = _rows(fixture_text_store, S124)
    _set(rows, S124 + "/1/b", prospective=True)
    assert window_statuses(rows, [S124 + "/1"]) == {S124 + "/1": NOT_YET}
    # The paragraph is owned by the nearer window when it has one of its own.
    statuses = window_statuses(rows, [S124 + "/1", S124 + "/1/b"])
    assert statuses == {S124 + "/1": IN_FORCE, S124 + "/1/b": NOT_YET}


def test_title_only_instrument(fixture_text_store: Any, fixture_roles: dict[str, Any]) -> None:
    coordinate = fixture_roles["title_only"]["instrument"]
    rows = fixture_text_store.get(coordinate)
    decision = decide_status(rows)
    assert decision.coordinate == coordinate
    assert decision.status is TEXT_NOT_HELD
    assert decision.reason == "text_not_held"
    assert decision.link == "https://www.legislation.gov.uk/ukpga/1980/15"
    # TEXT_NOT_HELD comes before HISTORY_NOT_HELD.
    assert decide_status(rows, as_at=date(1990, 1, 1)).status is TEXT_NOT_HELD


def test_repealed_provisions(fixture_text_store: Any, fixture_roles: dict[str, Any]) -> None:
    role = fixture_roles["repealed"]
    for coordinate in role["provisions"]:
        decision = decide_status(fixture_text_store.get(coordinate), coordinate)
        assert (decision.status, decision.reason, decision.link) == (
            REPEALED,
            "provision_repealed",
            None,
        )
    in_force = role["in_force_provision"]
    assert decide_status(fixture_text_store.get(in_force)).status is IN_FORCE
    whole = decide_status(fixture_text_store.get(role["instrument"]), role["instrument"])
    assert (whole.status, whole.reason) == (IN_FORCE, "partly_repealed")
    # s6 has dot-run text without the flag: repealed in fact.
    assert decide_status(fixture_text_store.get("uk/ukpga/2008/7/s6")).status is REPEALED


def test_prospective_provision(fixture_text_store: Any, fixture_roles: dict[str, Any]) -> None:
    role = fixture_roles["prospective"]
    (s1,) = role["provisions"]
    decision = decide_status(fixture_text_store.get(s1), s1)
    assert (decision.status, decision.reason) == (NOT_YET, "provision_prospective")
    assert decide_status(fixture_text_store.get(s1 + "/2")).status is NOT_YET
    assert decide_status(fixture_text_store.get(role["in_force_provision"])).status is IN_FORCE
    whole = decide_status(fixture_text_store.get(role["instrument"]), role["instrument"])
    assert (whole.status, whole.reason) == (IN_FORCE, "partly_not_yet_in_force")


def test_as_at_before_version_date(fixture_text_store: Any) -> None:
    rows = fixture_text_store.get(S124)
    decision = decide_status(rows, S124, as_at=date(2020, 4, 6))
    assert decision.status is HISTORY
    assert decision.reason == "history_not_held"
    assert decision.link == "https://www.legislation.gov.uk/ukpga/1996/18/section/124/2020-04-06"
    assert decision.as_at == date(2020, 4, 6)
    on_the_day = decide_status(rows, S124, as_at=date(2026, 7, 29))
    assert on_the_day.status is IN_FORCE
    assert decide_status(rows, S124, as_at=date(2026, 9, 1)).status is IN_FORCE
    s94 = "uk/ukpga/1996/18/s94"
    whole = decide_status(fixture_text_store.get(s94), s94, as_at=date(2000, 1, 1))
    assert whole.link == "https://www.legislation.gov.uk/ukpga/1996/18/section/94/2000-01-01"


def test_history_comes_before_repealed_and_prospective(fixture_text_store: Any) -> None:
    repealed = decide_status(fixture_text_store.get("uk/ukpga/2008/7/s4"), as_at=date(2010, 1, 1))
    assert repealed.status is HISTORY
    assert repealed.link == "https://www.legislation.gov.uk/ukpga/2008/7/section/4/2010-01-01"
    prospective = decide_status(
        fixture_text_store.get("uk/ukpga/2018/11/s1"), as_at=date(2018, 1, 1)
    )
    assert prospective.status is HISTORY
    statuses = window_statuses(
        fixture_text_store.get("uk/ukpga/2018/11/s1"),
        ["uk/ukpga/2018/11/s1/1"],
        as_at=date(2018, 1, 1),
    )
    assert statuses == {"uk/ukpga/2018/11/s1/1": HISTORY}


def test_instrument_level_history_link(fixture_text_store: Any) -> None:
    record = fixture_text_store.instrument("uk/ukpga/1996/18")
    decision = instrument_status(record, as_at=date(2001, 2, 3))
    assert decision.link == "https://www.legislation.gov.uk/ukpga/1996/18/2001-02-03"


# ---------------------------------------------------------------------------- repealed Acts


def test_repealed_instrument(fixture_text_store: Any) -> None:
    rows = _rows(fixture_text_store, "uk/ukpga/2008/7/s3")
    by_router = decide_status(rows, repealed=True)
    assert (by_router.status, by_router.reason) == (REPEALED, "instrument_repealed")
    rows[0]["repealed"] = True
    by_record = decide_status(rows)
    assert (by_record.status, by_record.reason) == (REPEALED, "instrument_repealed")
    statuses = window_statuses(rows, ["uk/ukpga/2008/7/s3/1", "uk/ukpga/2008/7/s3/2"])
    assert set(statuses.values()) == {REPEALED}


def test_repeal_is_inherited_by_subsections(fixture_text_store: Any) -> None:
    rows = _rows(fixture_text_store, "uk/ukpga/2008/7/s3")
    _set(rows, "uk/ukpga/2008/7/s3", repealed=True)
    assert decide_status(rows).reason == "provision_repealed"
    assert window_statuses(rows, ["uk/ukpga/2008/7/s3/5"]) == {"uk/ukpga/2008/7/s3/5": REPEALED}


def test_subtree_categories(fixture_text_store: Any) -> None:
    s3 = "uk/ukpga/2008/7/s3"
    rows = _rows(fixture_text_store, s3)
    for row in rows[1:]:
        if row["coordinate"] != s3:
            row["prospective"] = True
    assert decide_status(rows).reason == "text_prospective"
    assert decide_status(rows).status is NOT_YET
    rows = _rows(fixture_text_store, s3)
    for row in rows[1:]:
        if row["coordinate"] != s3:
            row["repealed"] = True
    assert (decide_status(rows).status, decide_status(rows).reason) == (REPEALED, "text_repealed")
    rows = _rows(fixture_text_store, s3)
    for row in rows[1:]:
        row["text"] = ""
    assert (decide_status(rows).status, decide_status(rows).reason) == (IN_FORCE, "no_text")


# ---------------------------------------------------------------------------- version dates


def test_held_version_date_for_as_enacted_records() -> None:
    made = {"text_version": "as_enacted", "version_date": None, "made_date": "2011-12-05"}
    assert held_version_date(made) == date(2011, 12, 5)
    assert held_version_date({"version_date": "2026-07-29"}) == date(2026, 7, 29)
    assert held_version_date({"version_date": "not a date"}) is None
    instrument = {
        "record_type": "instrument",
        "coordinate": "uk/uksi/2011/3006",
        "structure": "full",
        **made,
    }
    assert instrument_status(instrument, as_at=date(2011, 1, 1)).status is HISTORY
    assert instrument_status(instrument, as_at=date(2012, 1, 1)).status is IN_FORCE


def test_unknown_version_date_with_as_at() -> None:
    instrument = {
        "record_type": "instrument",
        "coordinate": "uk/uksi/2011/3006",
        "structure": "full",
        "text_version": "current",
        "version_date": None,
    }
    decision = instrument_status(instrument, as_at=date(2020, 1, 1))
    assert (decision.status, decision.reason) == (HISTORY, "version_date_unknown")
    assert decision.link == "https://www.legislation.gov.uk/uksi/2011/3006/2020-01-01"
    assert instrument_status(instrument).status is IN_FORCE


# ---------------------------------------------------------------------------- errors, reasons


def test_errors(fixture_text_store: Any) -> None:
    rows = fixture_text_store.get(S124)
    with pytest.raises(ValueError, match="no instrument record"):
        decide_status(rows[1:])
    with pytest.raises(ValueError, match="not among the records"):
        decide_status(rows, "uk/ukpga/1996/18/s99")
    with pytest.raises(ValueError, match="not among the records"):
        window_statuses(rows, ["uk/ukpga/1996/18/s99"])


def test_every_reason_is_known(fixture_text_store: Any, fixture_roles: dict[str, Any]) -> None:
    coordinates = [
        S124,
        S124 + "/1",
        fixture_roles["title_only"]["instrument"],
        *fixture_roles["repealed"]["provisions"],
        *fixture_roles["prospective"]["provisions"],
    ]
    for coordinate in coordinates:
        for as_at in (None, date(2000, 1, 1)):
            rows = fixture_text_store.get(coordinate)
            assert decide_status(rows, coordinate, as_at=as_at).reason in REASONS
