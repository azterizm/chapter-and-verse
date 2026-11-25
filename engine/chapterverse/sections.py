"""Section-level coordinate helpers shared across discovery, audit, and evaluation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

__all__ = ["section_coordinate"]

_YEAR_PART_INDEX: Final = 2
_CALENDAR_INST_PARTS: Final = 4
_REGNAL_INST_PARTS: Final = 5


def _provision_start(parts: Sequence[str]) -> int:
    return (
        _CALENDAR_INST_PARTS
        if (len(parts) > _YEAR_PART_INDEX and parts[_YEAR_PART_INDEX].isdigit())
        else _REGNAL_INST_PARTS
    )


def section_coordinate(coord: str) -> str:
    """Return the section-level coordinate for ``coord``.

    For a calendar-numbered instrument (4 parts) or regnal-year Act (5 parts), this is the
    instrument coordinate plus the first provision segment (``s12``, ``reg4``, ``art5``, ...);
    for a schedule, ``schN`` plus the next segment (``sch1/para2``). A coordinate with no
    provision part is returned unchanged.
    """
    parts = coord.split("/")
    base = _provision_start(parts)
    if len(parts) <= base:
        return coord
    if parts[base].startswith("sch") and len(parts) > base + 1:
        return "/".join(parts[: base + 2])
    return "/".join(parts[: base + 1])
