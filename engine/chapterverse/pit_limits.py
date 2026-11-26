"""Manifest-verified point-in-time statutory limits lookup (Layer 3, ``bench/pit``).

Loads ``limits.json`` only after verifying its SHA-256, size, and snapshot against the sibling
``pit-manifest.json`` (matching the fail-closed pattern of ``clarify_prior.json``). When an exact
calendar date and covered coordinate match a single verified row, the pipeline emits a
deterministic zero-LLM answer from the official Statutory Instrument Schedule text. Uncovered
dates, imprecise dates, or uncovered provisions fall through unchanged to ``HISTORY_NOT_HELD``.
"""

from __future__ import annotations

import itertools
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Final

from chapterverse.citations import (
    LEGISLATION_BASE,
    instrument_name,
    instrument_url,
    official_citation,
)
from chapterverse.contracts import (
    RenderedCitation,
    SentenceOut,
    TemporalStatus,
    Window,
)
from chapterverse.store.manifest import sha256_file

__all__ = [
    "PitLimitRow",
    "PitLimits",
    "build_pit_window",
    "load_pit_limits",
    "parse_exact_date",
    "render_pit_citation",
    "render_pit_sentence",
]

log = logging.getLogger(__name__)

_HEX64_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_S124_1ZA_INSERTED: Final = date(2013, 7, 29)
_REQUIRED_STR_FIELDS: Final[tuple[str, ...]] = (
    "coordinate",
    "amount",
    "valid_from",
    "si",
    "title",
    "made",
    "provision",
    "previous_amount",
    "commencement",
    "transitional",
    "source_url",
    "xml_sha256",
)

_OPERATIVE_ALIASES: Final[dict[str, str]] = {
    "uk/ukpga/1996/18/s124/1": "uk/ukpga/1996/18/s124",
    "uk/ukpga/1996/18/s124/1ZA": "uk/ukpga/1996/18/s124",
    "uk/ukpga/1996/18/s124/1ZA/a": "uk/ukpga/1996/18/s124",
    "uk/ukpga/1996/18/s186/1": "uk/ukpga/1996/18/s186",
    "uk/ukpga/1996/18/s186/1/a": "uk/ukpga/1996/18/s186",
    "uk/ukpga/1996/18/s186/1/b": "uk/ukpga/1996/18/s186",
    "uk/ukpga/1996/18/s227/1": "uk/ukpga/1996/18/s227",
}
_S124_1ZA_COORDS: Final = frozenset(
    {
        "uk/ukpga/1996/18/s124/1ZA",
        "uk/ukpga/1996/18/s124/1ZA/a",
    }
)

_MONTHS: Final = {
    name: number
    for number, names in enumerate(
        (
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sept", "sep"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ),
        start=1,
    )
    for name in names
}
_MONTH_RX: Final = "|".join(sorted(_MONTHS, key=len, reverse=True))
_FULL_DATE_RES: Final = (
    re.compile(rf"(?<!\d)(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH_RX})\.?,?\s+(\d{{4}})(?!\d)", re.I),
    re.compile(
        rf"(?<![a-z])({_MONTH_RX})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})(?!\d)", re.I
    ),
    re.compile(r"(?<![\d.])(\d{1,2})\s*[./]\s*(\d{1,2})\s*[./]\s*(\d{4})(?!\d)"),
    re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)"),
)


def parse_exact_date(hint: str | None) -> date | None:
    """Return the unique calendar date in ``hint``, or None if missing, imprecise, or ambiguous."""
    if not hint:
        return None
    found: set[date] = set()
    for idx, pattern in enumerate(_FULL_DATE_RES):
        for match in pattern.finditer(hint):
            a, b, c = match.groups()
            try:
                if idx == 0:
                    found.add(date(int(c), _MONTHS[b.lower()], int(a)))
                elif idx == 1:
                    found.add(date(int(c), _MONTHS[a.lower()], int(b)))
                elif idx == 2:  # noqa: PLR2004 - UK order: day, month, year
                    found.add(date(int(c), int(b), int(a)))
                else:
                    found.add(date(int(a), int(b), int(c)))
            except ValueError:
                continue
    return found.pop() if len(found) == 1 else None


@dataclass(frozen=True, slots=True, kw_only=True)
class PitLimitRow:
    """One verified point-in-time limit row from an Increase of Limits Order."""

    coordinate: str
    amount: str
    valid_from: date
    valid_to: date | None
    si: str
    title: str
    made: str
    provision: str
    previous_amount: str
    commencement: str
    transitional: str
    caveat: str | None
    source_url: str
    xml_sha256: str

    @property
    def si_citation(self) -> str:
        """Short SI number such as ``SI 2010/2926``."""
        return official_citation(self.si) or self.si

    @property
    def si_label(self) -> str:
        """Human-readable instrument name with SI number."""
        return instrument_name(self.si, {"title": self.title})

    @property
    def web_url(self) -> str:
        """User-facing legislation.gov.uk page URL for the SI."""
        if self.source_url.startswith(LEGISLATION_BASE):
            url = self.source_url.removesuffix("/data.xml").rstrip("/")
            if url:
                return url
        return instrument_url(self.si)


@dataclass(frozen=True, slots=True)
class PitLimits:
    """In-memory index of verified point-in-time statutory limit rows."""

    rows: tuple[PitLimitRow, ...]
    snapshot: str
    sha256: str
    _by_coordinate: Mapping[str, tuple[PitLimitRow, ...]] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        grouped: dict[str, list[PitLimitRow]] = {}
        for row in self.rows:
            grouped.setdefault(row.coordinate, []).append(row)
        frozen = {
            coord: tuple(sorted(coord_rows, key=lambda r: r.valid_from))
            for coord, coord_rows in grouped.items()
        }
        object.__setattr__(self, "_by_coordinate", frozen)

    def lookup(self, coordinate: str, as_at: date) -> PitLimitRow | None:
        """Return the unique row covering ``coordinate`` on ``as_at``, or None."""
        target = coordinate
        if target not in self._by_coordinate:
            if target in _S124_1ZA_COORDS and as_at < _S124_1ZA_INSERTED:
                return None
            target = _OPERATIVE_ALIASES.get(target, "")
            if not target or target not in self._by_coordinate:
                return None
        matches = [
            row
            for row in self._by_coordinate[target]
            if row.valid_from <= as_at and (row.valid_to is None or as_at < row.valid_to)
        ]
        return matches[0] if len(matches) == 1 else None


def _parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) != 10:  # noqa: PLR2004 - YYYY-MM-DD
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_row(raw: Any) -> PitLimitRow | None:
    if not isinstance(raw, Mapping) or any(
        not isinstance(raw.get(key), str) or not str(raw[key]).strip()
        for key in _REQUIRED_STR_FIELDS
    ):
        return None
    valid_from = _parse_iso_date(raw["valid_from"])
    raw_valid_to = raw.get("valid_to")
    valid_to = None if raw_valid_to is None else _parse_iso_date(raw_valid_to)
    xml_sha = raw["xml_sha256"].strip().lower()
    source_url = raw["source_url"].strip()
    raw_caveat = raw.get("caveat")
    valid_dates = valid_from is not None and (
        raw_valid_to is None or (valid_to is not None and valid_from < valid_to)
    )
    valid_meta = (
        _HEX64_RE.match(xml_sha) is not None
        and source_url.startswith(LEGISLATION_BASE)
        and (raw_caveat is None or isinstance(raw_caveat, str))
    )
    if not (valid_dates and valid_meta):
        return None
    assert valid_from is not None  # noqa: S101 - checked in valid_dates
    caveat = raw_caveat.strip() if isinstance(raw_caveat, str) and raw_caveat.strip() else None
    return PitLimitRow(
        coordinate=raw["coordinate"].strip(),
        amount=raw["amount"].strip(),
        valid_from=valid_from,
        valid_to=valid_to,
        si=raw["si"].strip(),
        title=raw["title"].strip(),
        made=raw["made"].strip(),
        provision=raw["provision"].strip(),
        previous_amount=raw["previous_amount"].strip(),
        commencement=raw["commencement"].strip(),
        transitional=raw["transitional"].strip(),
        caveat=caveat,
        source_url=source_url,
        xml_sha256=xml_sha,
    )


def _has_overlapping_intervals(rows: tuple[PitLimitRow, ...]) -> bool:
    by_coord: dict[str, list[PitLimitRow]] = {}
    for row in rows:
        by_coord.setdefault(row.coordinate, []).append(row)
    for coord_rows in by_coord.values():
        ordered = sorted(coord_rows, key=lambda r: r.valid_from)
        for prev, curr in itertools.pairwise(ordered):
            if prev.valid_to is None or prev.valid_to > curr.valid_from:
                return True
    return False


def _read_manifest_entry(path: Path, *, router_snapshot: str) -> tuple[str, int | None] | None:
    manifest_path = path.parent / "pit-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_bytes()) if manifest_path.is_file() else None
    except (OSError, ValueError) as exc:
        log.warning("pit limits manifest load failed (%s)", type(exc).__name__)
        return None
    if not isinstance(manifest, dict):
        log.warning("pit limits manifest missing or invalid at %s", manifest_path)
        return None
    snapshot = str(manifest.get("snapshot") or "").strip()
    if not snapshot or snapshot != router_snapshot:
        log.warning(
            "pit limits snapshot %r does not match router snapshot %r",
            snapshot,
            router_snapshot,
        )
        return None
    files = manifest.get("files")
    meta = (
        (files.get(path.name) or files.get("limits.json")) if isinstance(files, Mapping) else None
    )
    expected_sha = meta.get("sha256") if isinstance(meta, Mapping) else None
    if (
        not isinstance(meta, Mapping)
        or not isinstance(expected_sha, str)
        or not _HEX64_RE.match(expected_sha)
    ):
        log.warning("pit limits manifest invalid or missing entry for %s", path.name)
        return None
    expected_size = meta.get("size", meta.get("bytes"))
    if expected_size is not None and (
        not isinstance(expected_size, int) or isinstance(expected_size, bool)
    ):
        log.warning("pit limits manifest size invalid for %s", path.name)
        return None
    return expected_sha, expected_size


def _verify_manifest(path: Path, *, router_snapshot: str) -> str | None:
    entry = _read_manifest_entry(path, router_snapshot=router_snapshot)
    if entry is None:
        return None
    expected_sha, expected_size = entry
    try:
        actual_size = path.stat().st_size
        actual_sha = sha256_file(path)
    except OSError as exc:
        log.warning("pit limits read failed (%s)", type(exc).__name__)
        return None
    if expected_size is not None and actual_size != expected_size:
        log.warning("pit limits size mismatch for %s", path.name)
        return None
    if actual_sha != expected_sha:
        log.warning("pit limits sha256 mismatch for %s", path.name)
        return None
    return actual_sha


def _parse_rows(raw: Any) -> tuple[PitLimitRow, ...] | None:
    if not isinstance(raw, list) or not raw:
        log.warning("pit limits JSON must be a non-empty list")
        return None
    parsed: list[PitLimitRow] = []
    for item in raw:
        row = _parse_row(item)
        if row is None:
            log.warning("pit limits JSON contains an invalid row")
            return None
        parsed.append(row)
    rows = tuple(parsed)
    if _has_overlapping_intervals(rows):
        log.warning("pit limits JSON contains overlapping effective intervals")
        return None
    return rows


def load_pit_limits(
    path: Path | str | None,
    *,
    router_snapshot: str,
) -> PitLimits | None:
    """Load and verify an optional point-in-time limits JSON artefact, failing closed to None."""
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    actual_sha = _verify_manifest(p, router_snapshot=router_snapshot)
    if actual_sha is None:
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("pit limits JSON load failed (%s)", type(exc).__name__)
        return None
    rows = _parse_rows(raw)
    if rows is None:
        return None
    return PitLimits(rows=rows, snapshot=router_snapshot, sha256=actual_sha)


def build_pit_window(row: PitLimitRow, *, passage_id: str = "P1") -> Window:
    """Build the verified Schedule ``Window`` shown in ``SourcesEvent``."""
    until_window = (
        "onwards (until replaced)"
        if row.valid_to is None
        else f"to {(row.valid_to - timedelta(days=1)).isoformat()} "
        f"(superseded on {row.valid_to.isoformat()})"
    )
    lines = [
        f"Provision: {row.provision} (Employment Rights Act 1996)",
        f"Statutory limit: {row.amount} (previous limit: {row.previous_amount})",
        f"Order: {row.title} ({row.si_citation}), made {row.made}",
        f"Effective period: {row.valid_from.isoformat()} {until_window}",
        f"Commencement: {row.commencement}",
        f"Transitional provision: {row.transitional}",
    ]
    if row.caveat:
        lines.append(f"Caveat: {row.caveat}")
    lines.append(f"Source: {row.web_url} (XML SHA-256: {row.xml_sha256})")
    return Window(
        passage_id=passage_id,
        coordinate=row.si,
        heading=f"{row.si_label}, Schedule ({row.provision})",
        text="\n".join(lines),
        status=TemporalStatus.IN_FORCE,
        version_date=row.valid_from.isoformat(),
        source_url=row.web_url,
        highlight=None,
    )


def render_pit_sentence(
    row: PitLimitRow,
    as_at: date,
    *,
    passage_id: str = "P1",
) -> SentenceOut:
    """Build the deterministic templated ``SentenceOut`` for a verified limit row."""
    until = (
        "onwards (until replaced)"
        if row.valid_to is None
        else f"to {(row.valid_to - timedelta(days=1)).isoformat()}"
    )
    parts = [
        (
            f"As at {as_at.isoformat()}, the statutory limit for {row.provision} "
            f"(Employment Rights Act 1996) was {row.amount}, set by {row.title} "
            f"({row.si_citation}), in force from {row.valid_from.isoformat()} {until}."
        )
    ]
    if row.caveat:
        parts.append(row.caveat)
    if row.transitional:
        parts.append(f"Transitional provision: {row.transitional}")
    return SentenceOut(
        index=0,
        text=" ".join(parts),
        verdict="supported",
        passage_ids=(passage_id,),
        reasons=("PIT_VERIFIED_LIMIT",),
        rollbacks=0,
    )


def render_pit_citation(row: PitLimitRow, *, passage_id: str = "P1") -> RenderedCitation:
    """Build the system-rendered ``RenderedCitation`` pointing to the SI URL."""
    return RenderedCitation(
        passage_id=passage_id,
        coordinate=row.si,
        label=row.si_label,
        url=row.web_url,
    )
