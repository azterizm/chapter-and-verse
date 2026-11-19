"""Temporal status of a bound coordinate (plan section 5 stage 6, ARCHITECTURE section 4.1).

Decided from fields the records already hold; no model, no network. ``legal-rag-temporal``
replaces this module when it exists.

Signals, per node:

* ``TEXT_NOT_HELD``: the instrument record has ``structure == "metadata_only"``.
* ``HISTORY_NOT_HELD``: an ``as_at`` date is given and is before the date of the version held.
  That date is the instrument's ``version_date``; for ``text_version == "as_enacted"`` records
  without one (common for SIs) it is the ``enactment_date`` or ``made_date``. When no date is
  known at all, any ``as_at`` gives ``HISTORY_NOT_HELD`` (reason ``version_date_unknown``):
  the text held cannot be shown to apply on that date.
* ``REPEALED``: the instrument record, the router (``RouteResult.repealed``), the node or an
  ancestor among the records says ``repealed: true``; or the node's text is only a dot run
  (``. . . .``) and it has no children (``s124/2`` of ERA 1996 is so, with ``repealed: false``).
* ``NOT_YET_IN_FORCE``: the node or an ancestor among the records says ``prospective: true``.

Precedence for one node or one window, first match wins:

1. ``TEXT_NOT_HELD`` (there is no text to date or to flag).
2. ``HISTORY_NOT_HELD`` (the flags describe the version held, not the one asked about; a
   provision repealed now may have been in force on the date asked).
3. ``REPEALED``.
4. ``NOT_YET_IN_FORCE``. A window is ``NOT_YET_IN_FORCE`` when any text it shows is
   prospective, even if its stem is in force: no prospective words are shown as law in force.
5. ``IN_FORCE``.

Precedence for a bound subtree (:func:`decide_status`), after rules 1 to 3 at instrument level:

* the bound node itself repealed: ``REPEALED`` (``provision_repealed``);
* the bound node itself prospective: ``NOT_YET_IN_FORCE`` (``provision_prospective``);
* otherwise each text-bearing node of the subtree is in force, prospective or repealed. Any in
  force: ``IN_FORCE``, with reason ``partly_repealed``, ``partly_not_yet_in_force`` or
  ``partly_repealed_and_not_yet_in_force`` when the rest is mixed, so the answer goes ahead
  and each window carries its own status. None in force but some prospective:
  ``NOT_YET_IN_FORCE`` (``text_prospective``). Only repealed text: ``REPEALED``
  (``text_repealed``). No text at all: ``IN_FORCE`` (``no_text``); there are no windows.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from legal_rag_verifier.corpus import instrument_coordinate

from chapterverse.citations import dated_url, record_url
from chapterverse.contracts import Coordinate, TemporalStatus

__all__ = [
    "REASONS",
    "StatusDecision",
    "decide_status",
    "held_version_date",
    "instrument_record",
    "instrument_status",
    "window_statuses",
]

REASONS: Final = frozenset(
    {
        "in_force",
        "no_text",
        "partly_repealed",
        "partly_not_yet_in_force",
        "partly_repealed_and_not_yet_in_force",
        "text_not_held",
        "history_not_held",
        "version_date_unknown",
        "instrument_repealed",
        "provision_repealed",
        "text_repealed",
        "provision_prospective",
        "text_prospective",
    }
)
"""Every value :attr:`StatusDecision.reason` can take."""

_DOT_RUN: Final = re.compile(r"^[\s.\u2026]*$")


@dataclass(frozen=True, slots=True)
class StatusDecision:
    """The temporal status of one bound coordinate, why, and the link to give.

    ``link`` is the instrument's legislation.gov.uk URL for ``TEXT_NOT_HELD`` and the dated URL
    of the bound provision at ``as_at`` for ``HISTORY_NOT_HELD``; None otherwise.
    ``version_date`` is the instrument record's own field (None when it has none).
    """

    coordinate: Coordinate
    status: TemporalStatus
    reason: str
    link: str | None = None
    version_date: str | None = None
    as_at: date | None = None


# ---------------------------------------------------------------------------- helpers


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def held_version_date(instrument: Mapping[str, Any]) -> date | None:
    """The date of the version of the text held, or None when the records do not say."""
    held = _parse_date(instrument.get("version_date"))
    if held is None and instrument.get("text_version") == "as_enacted":
        held = _parse_date(instrument.get("enactment_date")) or _parse_date(
            instrument.get("made_date")
        )
    return held


def instrument_record(records: Iterable[Mapping[str, Any]]) -> Mapping[str, Any]:
    """The instrument record among ``records``.

    Raises:
        ValueError: there is none.
    """
    for row in records:
        if row.get("record_type") == "instrument":
            return row
    raise ValueError("records hold no instrument record")


def _is_dot_run(text: Any) -> bool:
    return isinstance(text, str) and bool(text) and _DOT_RUN.match(text) is not None


def _has_text(row: Mapping[str, Any]) -> bool:
    return any(
        isinstance(row.get(k), str) and row[k].strip() and not _is_dot_run(row[k])
        for k in ("text", "text_after")
    )


class _Tree:
    """Provision rows by coordinate with inherited repeal and prospective flags."""

    def __init__(self, records: Iterable[Mapping[str, Any]]) -> None:
        self.rows: dict[str, Mapping[str, Any]] = {}
        for row in records:
            if row.get("record_type") != "instrument":
                self.rows.setdefault(str(row["coordinate"]), row)
        self.parent: dict[str, str | None] = {}
        has_children: set[str] = set()
        for coordinate, row in self.rows.items():
            parent = str(row.get("parent") or "")
            self.parent[coordinate] = parent if parent in self.rows else None
            if parent in self.rows:
                has_children.add(parent)
        self.has_children = has_children

    def ancestors_or_self(self, coordinate: str) -> Iterable[str]:
        current: str | None = coordinate
        while current is not None:
            yield current
            current = self.parent.get(current)

    def repealed(self, coordinate: str) -> bool:
        for node in self.ancestors_or_self(coordinate):
            row = self.rows[node]
            if row.get("repealed"):
                return True
        row = self.rows[coordinate]
        return (
            coordinate not in self.has_children
            and _is_dot_run(row.get("text"))
            and not _has_text(row)
        )

    def prospective(self, coordinate: str) -> bool:
        return any(self.rows[n].get("prospective") for n in self.ancestors_or_self(coordinate))

    def in_subtree(self, root: str) -> list[str]:
        prefix = root + "/"
        return [c for c in self.rows if c == root or c.startswith(prefix)]

    def category(self, coordinate: str) -> TemporalStatus | None:
        """The node's own text status, or None when it carries no text and is not repealed."""
        if self.repealed(coordinate):
            row = self.rows[coordinate]
            has_any = _has_text(row) or _is_dot_run(row.get("text"))
            return TemporalStatus.REPEALED if has_any else None
        if not _has_text(self.rows[coordinate]):
            return None
        if self.prospective(coordinate):
            return TemporalStatus.NOT_YET_IN_FORCE
        return TemporalStatus.IN_FORCE


def instrument_status(
    instrument: Mapping[str, Any],
    *,
    as_at: date | None = None,
    repealed: bool = False,
    coordinate: Coordinate | None = None,
    provision: Mapping[str, Any] | None = None,
) -> StatusDecision:
    """Rules 1 to 3 at instrument level; ``IN_FORCE`` (``in_force``) when none applies.

    ``repealed`` is the router's ``RouteResult.repealed``. ``coordinate`` is the bound
    coordinate (default: the instrument) and ``provision`` its record, used for the dated link.
    """
    inst = str(instrument.get("coordinate") or "")
    target = coordinate or inst
    version = instrument.get("version_date")
    version_str = version if isinstance(version, str) else None

    def decision(status: TemporalStatus, reason: str, link: str | None = None) -> StatusDecision:
        return StatusDecision(target, status, reason, link, version_str, as_at)

    if instrument.get("structure") == "metadata_only":
        return decision(
            TemporalStatus.TEXT_NOT_HELD,
            "text_not_held",
            record_url(instrument_coordinate(target), instrument),
        )
    if as_at is not None:
        held = held_version_date(instrument)
        if held is None or as_at < held:
            record = provision
            if record is None and target == inst:
                record = instrument
            link = dated_url(record_url(target, record), as_at)
            reason = "version_date_unknown" if held is None else "history_not_held"
            return decision(TemporalStatus.HISTORY_NOT_HELD, reason, link)
    if repealed or instrument.get("repealed"):
        return decision(TemporalStatus.REPEALED, "instrument_repealed")
    return decision(TemporalStatus.IN_FORCE, "in_force")


def _bound_root(tree: _Tree, instrument: str) -> str:
    """The single root provision of the rows, else the instrument.

    An instrument with one top-level provision reads as a binding of that provision; callers
    that bound the instrument pass ``coordinate`` explicitly.
    """
    roots = [c for c, p in tree.parent.items() if p is None]
    return roots[0] if len(roots) == 1 else instrument


def decide_status(
    records: Sequence[Mapping[str, Any]],
    coordinate: Coordinate | None = None,
    *,
    as_at: date | None = None,
    repealed: bool = False,
) -> StatusDecision:
    """The status of the bound ``coordinate`` from ``TextStore.get(coordinate)``'s rows.

    ``coordinate`` defaults to the single root provision of ``records``, or the instrument when
    the rows have several roots (a whole-instrument binding) or none. ``repealed`` is the
    router's ``RouteResult.repealed``. See the module docstring for the precedence.

    Raises:
        ValueError: no instrument record, or ``coordinate`` is not among the records.
    """
    instrument = instrument_record(records)
    inst = str(instrument.get("coordinate") or "")
    tree = _Tree(records)
    target = coordinate if coordinate is not None else _bound_root(tree, inst)
    if target != inst and target not in tree.rows:
        raise ValueError(f"{target} is not among the records")
    base = instrument_status(
        instrument,
        as_at=as_at,
        repealed=repealed,
        coordinate=target,
        provision=tree.rows.get(target),
    )
    version = base.version_date

    def decision(status: TemporalStatus, reason: str) -> StatusDecision:
        return StatusDecision(target, status, reason, None, version, as_at)

    if base.status is not TemporalStatus.IN_FORCE:
        return base
    if target != inst:
        if tree.repealed(target):
            return decision(TemporalStatus.REPEALED, "provision_repealed")
        if tree.prospective(target):
            return decision(TemporalStatus.NOT_YET_IN_FORCE, "provision_prospective")
        nodes = tree.in_subtree(target)
    else:
        nodes = list(tree.rows)
    status, reason = _subtree_status({tree.category(n) for n in nodes})
    return decision(status, reason)


def _subtree_status(
    seen: set[TemporalStatus | None],
) -> tuple[TemporalStatus, str]:
    """Status and reason from the categories of a subtree's text-bearing nodes."""
    repealed = TemporalStatus.REPEALED in seen
    prospective = TemporalStatus.NOT_YET_IN_FORCE in seen
    if TemporalStatus.IN_FORCE in seen:
        reasons = {
            (True, True): "partly_repealed_and_not_yet_in_force",
            (True, False): "partly_repealed",
            (False, True): "partly_not_yet_in_force",
            (False, False): "in_force",
        }
        return TemporalStatus.IN_FORCE, reasons[(repealed, prospective)]
    if prospective:
        return TemporalStatus.NOT_YET_IN_FORCE, "text_prospective"
    if repealed:
        return TemporalStatus.REPEALED, "text_repealed"
    return TemporalStatus.IN_FORCE, "no_text"


def window_statuses(
    records: Sequence[Mapping[str, Any]],
    window_coordinates: Sequence[Coordinate],
    *,
    as_at: date | None = None,
    repealed: bool = False,
) -> dict[Coordinate, TemporalStatus]:
    """The status of each window, keyed by window coordinate.

    Each provision row belongs to the window of its nearest ancestor-or-self that is a window
    coordinate, which is exactly the text ``Premise.from_records`` puts in that window (a
    section stem window holds its own text; each subsection window holds its descendants).
    The window's status follows the module's precedence over its rows: ``REPEALED`` when the
    window node or an ancestor is repealed, ``NOT_YET_IN_FORCE`` when any non-repealed row it
    shows is prospective, else ``IN_FORCE``. Instrument-level rules 1 to 3 apply to all.

    Raises:
        ValueError: no instrument record, or a window coordinate is not among the records.
    """
    instrument = instrument_record(records)
    tree = _Tree(records)
    wanted = list(dict.fromkeys(window_coordinates))
    for c in wanted:
        if c not in tree.rows:
            raise ValueError(f"window {c} is not among the records")
    base = instrument_status(instrument, as_at=as_at, repealed=repealed)
    if base.status is not TemporalStatus.IN_FORCE:
        return dict.fromkeys(wanted, base.status)

    windows = set(wanted)
    owned: dict[str, list[str]] = {c: [] for c in wanted}
    for c in tree.rows:
        for node in tree.ancestors_or_self(c):
            if node in windows:
                owned[node].append(c)
                break
    out: dict[Coordinate, TemporalStatus] = {}
    for c in wanted:
        if tree.repealed(c):
            out[c] = TemporalStatus.REPEALED
        elif any(
            tree.category(n) is TemporalStatus.NOT_YET_IN_FORCE for n in owned[c]
        ) or tree.prospective(c):
            out[c] = TemporalStatus.NOT_YET_IN_FORCE
        else:
            out[c] = TemporalStatus.IN_FORCE
    return out
