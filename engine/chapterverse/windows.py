"""Contract windows from a verifier premise (ARCHITECTURE section 4.3).

``Premise.from_records(store.get(coordinate))`` builds the passages the auditor verifies
against. :func:`build_windows` numbers its provision passages ``P1..Pn`` in premise order and
turns each into a :class:`~chapterverse.contracts.Window` the model, the auditor and the user
all see. Fact and free-text passages carry no coordinate and are not windows.

The audit stage needs the verifier's own :class:`~legal_rag_verifier.premise.Passage` for each
passage id; :func:`passages_by_id` gives it, numbered by the same rule, so ``P3`` is the same
passage on both sides.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from legal_rag_verifier.premise import Passage, PassageKind, Premise

from chapterverse.citations import record_url
from chapterverse.contracts import Window
from chapterverse.status import instrument_record, window_statuses

__all__ = ["build_windows", "passage_ids", "passages_by_id", "provision_passages"]


def provision_passages(premise: Premise) -> tuple[Passage, ...]:
    """The premise's provision passages with a coordinate, in premise order."""
    return tuple(p for p in premise.passages if p.kind is PassageKind.PROVISION and p.coordinate)


def passage_ids(count: int) -> tuple[str, ...]:
    """``("P1", ..., "Pn")``."""
    return tuple(f"P{i}" for i in range(1, count + 1))


def passages_by_id(premise: Premise) -> dict[str, Passage]:
    """Passage id to the verifier passage behind that window (same numbering as the windows)."""
    passages = provision_passages(premise)
    return dict(zip(passage_ids(len(passages)), passages, strict=True))


def build_windows(
    premise: Premise,
    records: Sequence[Mapping[str, Any]],
    *,
    as_at: date | None = None,
    repealed: bool = False,
) -> tuple[Window, ...]:
    """One :class:`Window` per provision passage of ``premise``, numbered ``P1..Pn``.

    ``records`` are the rows the premise was built from (``TextStore.get``), instrument record
    included. Each window's ``status`` comes from :func:`chapterverse.status.window_statuses`
    (``as_at`` and the router's ``repealed`` flag apply), ``version_date`` is the instrument
    record's, ``source_url`` the provision record's (built from the coordinate when the record
    has none). ``highlight`` is None; extractive mode sets it.

    Raises:
        ValueError: no instrument record, or a passage's coordinate is not among ``records``.
    """
    passages = provision_passages(premise)
    instrument = instrument_record(records)
    by_coordinate: dict[str, Mapping[str, Any]] = {}
    for row in records:
        if row.get("record_type") != "instrument":
            by_coordinate.setdefault(str(row["coordinate"]), row)
    coordinates = [str(p.coordinate) for p in passages]
    statuses = window_statuses(records, coordinates, as_at=as_at, repealed=repealed)
    version = instrument.get("version_date")
    version_date = version if isinstance(version, str) else None
    windows: list[Window] = []
    for passage_id, passage, coordinate in zip(
        passage_ids(len(passages)), passages, coordinates, strict=True
    ):
        windows.append(
            Window(
                passage_id=passage_id,
                coordinate=coordinate,
                heading=passage.heading or "",
                text=passage.text,
                status=statuses[coordinate],
                version_date=version_date,
                source_url=record_url(coordinate, by_coordinate[coordinate]),
            )
        )
    return tuple(windows)
